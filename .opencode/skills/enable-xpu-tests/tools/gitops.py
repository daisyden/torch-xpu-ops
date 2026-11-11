"""Git helpers that operate on the workspace clone ($XPU_ENABLE_WS/pytorch).

Every mutating helper requires a clean tree and leaves the clone on a detached
HEAD at the file's base when it is done (see `restore`).
"""
import hashlib
import os
import random
import re
import subprocess
import tempfile

from common import CLONE


class GitError(RuntimeError):
    pass


def git(*args, cwd: str = CLONE, check: bool = True, input: str | None = None, raw: bool = False) -> str:
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, input=input)
    if check and p.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {(p.stderr or p.stdout).strip()}")
    return p.stdout if raw else p.stdout.strip()


def is_clean(cwd: str = CLONE) -> bool:
    return git("status", "--porcelain", "--untracked-files=no", cwd=cwd) == ""


def require_clean(cwd: str = CLONE) -> None:
    if not is_clean(cwd):
        raise GitError(f"clone is dirty; commit or stash first:\n{git('status', '--short', cwd=cwd)}")


def head(cwd: str = CLONE) -> str:
    return git("rev-parse", "HEAD", cwd=cwd)


def current_branch(cwd: str = CLONE) -> str:
    return git("branch", "--show-current", cwd=cwd)


def branch_exists(name: str, cwd: str = CLONE) -> bool:
    return subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{name}"],
                          cwd=cwd, capture_output=True).returncode == 0


def find_agent_branch(slug: str, cwd: str = CLONE) -> str:
    out = git("branch", "--list", f"agent_{slug}_*", "--format=%(refname:short)", cwd=cwd)
    pat = re.compile(rf"^agent_{re.escape(slug)}_\d{{4}}$")
    names = [n for n in out.splitlines() if pat.match(n.strip())]
    return names[0] if names else ""


def new_agent_branch_name(slug: str, cwd: str = CLONE) -> str:
    while True:
        name = f"agent_{slug}_{random.randint(1000, 9999)}"
        if not branch_exists(name, cwd):
            return name


def switch(ref: str, create_from: str | None = None, cwd: str = CLONE) -> None:
    require_clean(cwd)
    if create_from:
        git("switch", "-c", ref, create_from, cwd=cwd)
    else:
        git("switch", ref, cwd=cwd)


def restore(base_sha: str, cwd: str = CLONE) -> None:
    """Return the clone to a detached HEAD at base (the idle state)."""
    git("switch", "--detach", base_sha, cwd=cwd)


def commits(base: str, tip: str, cwd: str = CLONE) -> list[str]:
    out = git("rev-list", "--reverse", f"{base}..{tip}", cwd=cwd)
    return [c for c in out.splitlines() if c]


def changed_files(base: str, tip: str, cwd: str = CLONE) -> list[str]:
    return [p for p in git("diff", "--name-only", f"{base}..{tip}", cwd=cwd).splitlines() if p]


_ISSUE_TOKEN = re.compile(r"issues/[A-Za-z0-9_]+")


def sha_of_text(text: str) -> str:
    """Content hash of a patch, ignoring issue numbers/placeholders, so backfilling
    an issue number does not invalidate earlier measurements."""
    return hashlib.sha1(_ISSUE_TOKEN.sub("issues/N", text).encode()).hexdigest()[:12]


def write_patch(base: str, branch: str, path: str, cwd: str = CLONE) -> str:
    """Write base..branch as a patch file; return its content hash ('' if empty)."""
    text = git("diff", f"{base}..{branch}", cwd=cwd, raw=True)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return sha_of_text(text) if text else ""


def reapply_patch(branch: str, base: str, patch_text: str, message: str, cwd: str = CLONE) -> str:
    """Rebuild `branch` as base + one commit carrying `patch_text`. Rolls back on failure."""
    require_clean(cwd)
    old = git("rev-parse", branch, cwd=cwd)
    git("switch", branch, cwd=cwd)
    try:
        git("reset", "--hard", base, cwd=cwd)
        with tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False) as fh:
            fh.write(patch_text if patch_text.endswith("\n") else patch_text + "\n")
            tmp = fh.name
        try:
            git("apply", "--index", "--whitespace=nowarn", tmp, cwd=cwd)
        finally:
            os.unlink(tmp)
        git("commit", "-m", message, cwd=cwd)
        return head(cwd)
    except GitError:
        git("reset", "--hard", old, cwd=cwd)
        raise
    finally:
        restore(base, cwd)


def commit_message(sha: str, cwd: str = CLONE) -> str:
    return git("log", "-1", "--format=%B", sha, cwd=cwd)


def edit_on_branch(branch: str, base: str, edit_fn, message: str, cwd: str = CLONE) -> str | None:
    """Check out `branch`, run edit_fn(clone_dir) -> list of changed paths, then
    amend HEAD if HEAD already touched all of them, otherwise add a new commit.
    Returns the new HEAD sha, or None when nothing changed."""
    require_clean(cwd)
    git("switch", branch, cwd=cwd)
    try:
        paths = edit_fn(cwd) or []
        if is_clean(cwd):
            return None
        git("add", "--", *paths, cwd=cwd)
        head_files = set(git("show", "--name-only", "--format=", "HEAD", cwd=cwd).splitlines())
        ahead = commits(base, "HEAD", cwd)
        if ahead and set(paths) <= head_files:
            git("commit", "--amend", "--no-edit", cwd=cwd)
        else:
            git("commit", "-m", message, cwd=cwd)
        return head(cwd)
    finally:
        if not is_clean(cwd):
            git("reset", "--hard", "HEAD", cwd=cwd)
        restore(base, cwd)


def patch_ids(base: str, tip: str, cwd: str = CLONE) -> set[str]:
    """Stable patch-ids of commits in base..tip (for 'already picked' detection)."""
    out = set()
    for c in commits(base, tip, cwd):
        pid = git("patch-id", "--stable", cwd=cwd, input=git("show", c, cwd=cwd, raw=True))
        if pid:
            out.add(pid.split()[0])
    return out


def push(remote: str, branch: str, cwd: str = CLONE) -> str:
    """Push a local branch to the user's fork (creates it there if missing). Uses
    --force-with-lease so a rewritten agent branch (patch Save / backfill amend) can be
    re-pushed without clobbering anything we have not seen."""
    p = subprocess.run(["git", "push", "--force-with-lease", "-u", remote, f"{branch}:{branch}"],
                       cwd=cwd, capture_output=True, text=True, timeout=600)
    if p.returncode != 0:
        raise GitError(f"git push {remote} {branch}: {(p.stderr or p.stdout).strip()}")
    return (p.stderr or p.stdout).strip()


def cherry_pick(shas: list[str], cwd: str = CLONE) -> tuple[list[str], list[str]]:
    """Cherry-pick shas onto the current branch. On conflict abort and return
    (picked_new_shas, conflicting_paths)."""
    picked = []
    for s in shas:
        p = subprocess.run(["git", "cherry-pick", "-x", s], cwd=cwd, capture_output=True, text=True)
        if p.returncode != 0:
            conflicts = git("diff", "--name-only", "--diff-filter=U", cwd=cwd, check=False).splitlines()
            git("cherry-pick", "--abort", cwd=cwd, check=False)
            return picked, conflicts or [(p.stderr or p.stdout).strip()]
        picked.append(head(cwd))
    return picked, []
