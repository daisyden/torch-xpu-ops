"""Fetch PR metadata, unified diff, and full base/head file contents.

Strategy (per user's choice: "gh CLI + git fetch of the real repo"):
  * `gh api` for PR metadata + changed-file list + unified diff.
  * Full file blobs come from a local clone when possible (`git cat-file`),
    fetching the missing objects on demand. Falls back to the GitHub contents
    API if no usable clone is available.

Everything is cached on disk under ~/.cache/pytorch-refactor-review so that
re-opening a PR is instant.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CACHE_ROOT = Path(
    os.environ.get(
        "REFACTOR_REVIEW_CACHE", Path.home() / ".cache" / "pytorch-refactor-review"
    )
)

PR_URL_RE = re.compile(
    r"""(?x)
    ^(?:https?://(?:www\.)?github\.com/)?
    (?:(?P<owner>[\w.\-]+)/(?P<repo>[\w.\-]+)
        (?:/pull/|/pulls/|\#)
    )?
    (?P<number>\d+)
    """
)


class PRDataError(RuntimeError):
    pass


def parse_pr_ref(ref: str, default_repo: str = "pytorch/pytorch") -> tuple[str, int]:
    """Accept a full URL, `owner/repo#123`, `owner/repo/pull/123` or a bare number."""
    ref = ref.strip()
    if not ref:
        raise PRDataError("empty PR reference")
    ref = ref.split("?")[0].split("#diff")[0]
    m = PR_URL_RE.match(ref)
    if not m:
        raise PRDataError(f"cannot parse PR reference: {ref!r}")
    owner, repo, number = m.group("owner"), m.group("repo"), m.group("number")
    if owner and repo:
        return f"{owner}/{repo}", int(number)
    return default_repo, int(number)


def _run(cmd: list[str], cwd: str | None = None, check: bool = True) -> str:
    proc = subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, errors="replace"
    )
    if check and proc.returncode != 0:
        raise PRDataError(
            f"command failed ({proc.returncode}): {' '.join(cmd)}\n{proc.stderr.strip()}"
        )
    return proc.stdout


# --------------------------------------------------------------------------- #
# unified diff parsing
# --------------------------------------------------------------------------- #


@dataclass
class DiffLine:
    kind: str  # "ctx" | "del" | "add" | "hunk" | "meta"
    text: str
    base_no: int | None = None
    head_no: int | None = None


@dataclass
class FileDiff:
    path: str  # head path (new name)
    old_path: str  # base path
    status: str
    additions: int = 0
    deletions: int = 0
    binary: bool = False
    truncated: bool = False
    lines: list[DiffLine] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "old_path": self.old_path,
            "status": self.status,
            "additions": self.additions,
            "deletions": self.deletions,
            "binary": self.binary,
            "truncated": self.truncated,
            "lines": [
                {
                    "kind": ln.kind,
                    "text": ln.text,
                    "base_no": ln.base_no,
                    "head_no": ln.head_no,
                }
                for ln in self.lines
            ],
        }

    def verify(self, base_text: str, head_text: str) -> bool:
        """Check the hunk line numbers against the real file contents.

        GitHub truncates very large diffs; the surviving hunks then carry line
        numbers that no longer correspond to the files.  Detect that so callers
        can stop trusting this diff's positions.
        """
        bl = base_text.split("\n")
        hl = head_text.split("\n")
        checked = mismatched = 0
        for ln in self.lines:
            if ln.kind == "ctx" and ln.base_no and ln.head_no:
                checked += 1
                ok = (
                    ln.base_no <= len(bl)
                    and ln.head_no <= len(hl)
                    and bl[ln.base_no - 1] == ln.text
                )
                if not ok:
                    mismatched += 1
            elif ln.kind == "del" and ln.base_no:
                checked += 1
                if ln.base_no > len(bl) or bl[ln.base_no - 1] != ln.text:
                    mismatched += 1
            elif ln.kind == "add" and ln.head_no:
                checked += 1
                if ln.head_no > len(hl) or hl[ln.head_no - 1] != ln.text:
                    mismatched += 1
        self.truncated = checked > 0 and mismatched / checked > 0.02
        return not self.truncated


_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")


def parse_unified_diff(diff_text: str) -> list[FileDiff]:
    files: list[FileDiff] = []
    cur: FileDiff | None = None
    base_no = head_no = 0

    for raw in diff_text.split("\n"):
        if raw.startswith("diff --git "):
            m = re.match(r"diff --git a/(.*) b/(.*)$", raw)
            old_p, new_p = (m.group(1), m.group(2)) if m else ("", "")
            cur = FileDiff(path=new_p, old_path=old_p, status="modified")
            files.append(cur)
            continue
        if cur is None:
            continue
        if raw.startswith("new file mode"):
            cur.status = "added"
            continue
        if raw.startswith("deleted file mode"):
            cur.status = "removed"
            continue
        if raw.startswith("rename from "):
            cur.old_path = raw[len("rename from ") :]
            cur.status = "renamed"
            continue
        if raw.startswith("rename to "):
            cur.path = raw[len("rename to ") :]
            cur.status = "renamed"
            continue
        if raw.startswith("Binary files"):
            cur.binary = True
            continue
        if raw.startswith(("index ", "--- ", "+++ ", "similarity index", "old mode", "new mode")):
            continue
        m = _HUNK_RE.match(raw)
        if m:
            base_no = int(m.group(1))
            head_no = int(m.group(3))
            cur.lines.append(DiffLine("hunk", raw))
            continue
        if raw.startswith("\\ No newline"):
            continue
        if raw.startswith("-"):
            cur.lines.append(DiffLine("del", raw[1:], base_no=base_no))
            base_no += 1
            cur.deletions += 1
        elif raw.startswith("+"):
            cur.lines.append(DiffLine("add", raw[1:], head_no=head_no))
            head_no += 1
            cur.additions += 1
        elif raw.startswith(" ") or raw == "":
            if raw == "" and not cur.lines:
                continue
            cur.lines.append(
                DiffLine("ctx", raw[1:] if raw else "", base_no=base_no, head_no=head_no)
            )
            base_no += 1
            head_no += 1
    return files


# --------------------------------------------------------------------------- #
# repo access
# --------------------------------------------------------------------------- #


def _discover_clone(repo: str) -> Path | None:
    """Look for a local clone of `repo` in a few likely places."""
    env = os.environ.get("REFACTOR_REVIEW_CLONE")
    candidates: list[Path] = []
    if env:
        candidates.append(Path(env))
    name = repo.split("/")[-1]
    home = Path.home()
    candidates += [
        Path.cwd(),
        home / name,
        home / f"{name}_repo",
        home / "src" / name,
        home / "git" / name,
    ]
    for cand in candidates:
        if not (cand / ".git").exists() and not (cand / "HEAD").exists():
            continue
        try:
            remotes = _run(["git", "remote", "-v"], cwd=str(cand), check=False)
        except Exception:
            continue
        if repo.lower() in remotes.lower():
            return cand
    return None


class RepoAccess:
    """Reads blobs for arbitrary (sha, path) pairs, preferring a local clone."""

    # `git fetch` takes repository-wide locks (index.lock, refs), so two threads
    # fetching into the same clone can fail with "another git process is
    # running".  Serialise fetches per clone directory; blob *reads*
    # (`git show`) are safe in parallel and are not covered by this.
    _fetch_locks: dict[str, threading.Lock] = {}
    _fetch_locks_guard = threading.Lock()

    def __init__(self, repo: str, head_repo: str | None = None):
        self.repo = repo
        self.head_repo = head_repo or repo
        self.clone = _discover_clone(repo)
        self._fetched: set[str] = set()
        self._state_lock = threading.Lock()

    @classmethod
    def _fetch_lock(cls, clone: str) -> threading.Lock:
        with cls._fetch_locks_guard:
            lk = cls._fetch_locks.get(clone)
            if lk is None:
                lk = threading.Lock()
                cls._fetch_locks[clone] = lk
            return lk

    # -- local clone helpers ------------------------------------------------ #

    def _have_commit(self, sha: str) -> bool:
        if not self.clone:
            return False
        out = _run(
            ["git", "cat-file", "-t", sha], cwd=str(self.clone), check=False
        ).strip()
        return out == "commit"

    def _ensure_commit(self, sha: str, pr_number: int | None) -> bool:
        if not self.clone:
            return False
        if self._have_commit(sha):
            return True
        with self._state_lock:
            if sha in self._fetched:
                return False
            self._fetched.add(sha)
        with self._fetch_lock(str(self.clone)):
            # another thread may have fetched it while we waited
            if self._have_commit(sha):
                return True
            return self._do_fetch(sha, pr_number)

    def _do_fetch(self, sha: str, pr_number: int | None) -> bool:
        # try fetching the specific object, then the PR ref
        attempts: list[list[str]] = [
            ["git", "fetch", "--no-tags", "--depth", "1", "origin", sha],
        ]
        if pr_number:
            attempts.append(
                [
                    "git",
                    "fetch",
                    "--no-tags",
                    "origin",
                    f"pull/{pr_number}/head:refs/remotes/origin/pr/{pr_number}",
                ]
            )
        for cmd in attempts:
            _run(cmd, cwd=str(self.clone), check=False)
            if self._have_commit(sha):
                return True
        return False

    # -- public ------------------------------------------------------------- #

    def read_blob(self, sha: str, path: str, pr_number: int | None = None) -> str | None:
        """Return file contents at `sha`, or None if the file does not exist."""
        cache = CACHE_ROOT / "blobs" / sha[:2] / (
            sha + "-" + hashlib.sha1(path.encode()).hexdigest()[:16]
        )
        if cache.exists():
            data = cache.read_text(encoding="utf-8", errors="replace")
            return None if data == "\0MISSING\0" else data

        text: str | None = None
        if self._ensure_commit(sha, pr_number):
            proc = subprocess.run(
                ["git", "show", f"{sha}:{path}"],
                cwd=str(self.clone),
                capture_output=True,
                text=True,
                errors="replace",
            )
            if proc.returncode == 0:
                text = proc.stdout
            elif "does not exist" in proc.stderr or "exists on disk" in proc.stderr:
                text = None
        if text is None:
            text = self._read_via_api(sha, path)

        cache.parent.mkdir(parents=True, exist_ok=True)
        # Write atomically: several threads (or several server processes sharing
        # the cache directory) can race on the same blob, and a half-written
        # file would be read back as truncated source.
        _atomic_write(cache, text if text is not None else "\0MISSING\0")
        return text

    def _read_via_api(self, sha: str, path: str) -> str | None:
        for repo in dict.fromkeys([self.repo, self.head_repo]):
            proc = subprocess.run(
                [
                    "gh",
                    "api",
                    "-H",
                    "Accept: application/vnd.github.raw",
                    f"repos/{repo}/contents/{path}?ref={sha}",
                ],
                capture_output=True,
                text=True,
                errors="replace",
            )
            if proc.returncode == 0:
                return proc.stdout
        return None


# --------------------------------------------------------------------------- #
# top-level PR loader
# --------------------------------------------------------------------------- #


@dataclass
class PullRequest:
    repo: str
    number: int
    title: str
    author: str
    state: str
    url: str
    base_sha: str
    head_sha: str
    head_repo: str
    files: list[FileDiff]
    repo_access: RepoAccess

    def meta_json(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "number": self.number,
            "title": self.title,
            "author": self.author,
            "state": self.state,
            "url": self.url,
            "base_sha": self.base_sha,
            "head_sha": self.head_sha,
            "head_repo": self.head_repo,
            "clone": str(self.repo_access.clone) if self.repo_access.clone else None,
            "files": [
                {
                    "path": f.path,
                    "old_path": f.old_path,
                    "status": f.status,
                    "additions": f.additions,
                    "deletions": f.deletions,
                    "binary": f.binary,
                }
                for f in self.files
            ],
        }

    def file_diff(self, path: str) -> FileDiff | None:
        for f in self.files:
            if f.path == path:
                return f
        return None

    def base_text(self, fd: FileDiff) -> str:
        if fd.status == "added":
            return ""
        return (
            self.repo_access.read_blob(self.base_sha, fd.old_path, self.number) or ""
        )

    def head_text(self, fd: FileDiff) -> str:
        if fd.status == "removed":
            return ""
        return self.repo_access.read_blob(self.head_sha, fd.path, self.number) or ""


def rebuild_diff(fd: FileDiff, base_text: str, head_text: str, context: int = 3) -> FileDiff:
    """Recompute a full unified diff locally, for when GitHub truncated theirs.

    Produces the same FileDiff shape, so callers cannot tell the difference
    except that the line numbers are now correct.
    """
    import difflib

    bl = base_text.split("\n")
    hl = head_text.split("\n")
    if bl and bl[-1] == "":
        bl.pop()
    if hl and hl[-1] == "":
        hl.pop()

    out = FileDiff(
        path=fd.path,
        old_path=fd.old_path,
        status=fd.status,
        binary=fd.binary,
        truncated=False,
    )
    sm = difflib.SequenceMatcher(a=bl, b=hl, autojunk=False)
    groups = list(sm.get_grouped_opcodes(context))
    for group in groups:
        b_start, b_end = group[0][1], group[-1][2]
        h_start, h_end = group[0][3], group[-1][4]
        out.lines.append(
            DiffLine(
                "hunk",
                f"@@ -{b_start + 1},{b_end - b_start} "
                f"+{h_start + 1},{h_end - h_start} @@",
            )
        )
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                for k in range(i1, i2):
                    out.lines.append(
                        DiffLine("ctx", bl[k], base_no=k + 1, head_no=j1 + (k - i1) + 1)
                    )
            else:
                for k in range(i1, i2):
                    out.lines.append(DiffLine("del", bl[k], base_no=k + 1))
                    out.deletions += 1
                for k in range(j1, j2):
                    out.lines.append(DiffLine("add", hl[k], head_no=k + 1))
                    out.additions += 1
    return out


def _atomic_write(path: Path, text: str) -> None:
    """Write via a temp file + rename, so concurrent readers never see a
    partially written cache entry."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}-{threading.get_ident()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _cache_path(repo: str, number: int, name: str) -> Path:
    return CACHE_ROOT / "pr" / repo.replace("/", "_") / str(number) / name


def load_pr(ref: str, refresh: bool = False, default_repo: str = "pytorch/pytorch") -> PullRequest:
    repo, number = parse_pr_ref(ref, default_repo)

    meta_c = _cache_path(repo, number, "meta.json")
    diff_c = _cache_path(repo, number, "pr.diff")
    meta_c.parent.mkdir(parents=True, exist_ok=True)

    if refresh or not meta_c.exists():
        jq = (
            "{title:.title,author:.user.login,state:.state,url:.html_url,"
            "base_sha:.base.sha,head_sha:.head.sha,"
            'head_repo:(.head.repo.full_name // "")}'
        )
        meta_raw = _run(
            ["gh", "api", f"repos/{repo}/pulls/{number}", "--jq", jq]
        )
        _atomic_write(meta_c, meta_raw)
    meta = json.loads(meta_c.read_text(encoding="utf-8"))

    if refresh or not diff_c.exists():
        diff_text = _run(
            [
                "gh",
                "api",
                f"repos/{repo}/pulls/{number}",
                "-H",
                "Accept: application/vnd.github.v3.diff",
            ]
        )
        _atomic_write(diff_c, diff_text)
    diff_text = diff_c.read_text(encoding="utf-8")

    files = parse_unified_diff(diff_text)
    access = RepoAccess(repo, meta.get("head_repo") or repo)

    return PullRequest(
        repo=repo,
        number=number,
        title=meta.get("title", ""),
        author=meta.get("author", ""),
        state=meta.get("state", ""),
        url=meta.get("url", f"https://github.com/{repo}/pull/{number}"),
        base_sha=meta["base_sha"],
        head_sha=meta["head_sha"],
        head_repo=meta.get("head_repo") or repo,
        files=files,
        repo_access=access,
    )
