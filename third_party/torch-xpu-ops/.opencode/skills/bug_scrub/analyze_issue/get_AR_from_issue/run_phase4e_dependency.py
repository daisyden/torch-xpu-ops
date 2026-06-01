"""Phase 4e: Dependency Audit.

Applies rules D1/D2/D3 (see bug_scrub/SKILL.md "Phase 4e Dependency Audit"
section) to every issue whose Dependency column references one of the 6
tracked components, OR (with --all-issues) every issue regardless of
current Dependency value.

Two-stage flow:

  Stage 1 (--emit-worklist):
    Scan the Issues sheet, write a per-issue worklist JSON to
    agent_space/phase4e/worklist.json. Each entry holds the inputs an
    explore agent needs to render a D1 verdict (true_dep / false_dep)
    plus a one-line reason. Stage 1 makes no network calls and no
    workbook writes.

  Stage 2 (--merge):
    Reads agent_space/phase4e/results/<issue_id>.json (one per audited
    issue) produced by the agents, applies D2 (label hygiene) and D3
    (upstream tracking ref) deterministically using `gh` for ref-state
    checks, and writes the new verbs (and owner_transferred / cleared
    Dependency where applicable) back to Issues. A 24h-TTL cache at
    agent_space/phase4e_dep_ref_state_cache.json keeps re-runs cheap.

Phase 4e is idempotent: existing canonical verbs are never duplicated.

Run order: Phase 4a -> 4b -> 4c -> 4e -> 4d
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Optional

from openpyxl import load_workbook

COMMON_DIR = Path(__file__).resolve().parents[2] / "_common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))
from header_utils import cell_by_name, write_by_name  # noqa: E402
from paths import RESULT_DIR, AGENT_SPACE  # type: ignore[reportMissingImports] # noqa: E402

XLSX_PATH = RESULT_DIR / "torch_xpu_ops_issues.xlsx"
PHASE4E_ROOT = AGENT_SPACE / "phase4e"
WORKLIST_PATH = PHASE4E_ROOT / "worklist.json"
RESULTS_DIR = PHASE4E_ROOT / "results"
REF_STATE_CACHE_PATH = AGENT_SPACE / "phase4e_dep_ref_state_cache.json"
REF_STATE_TTL_SEC = 24 * 3600

# Component name (lowercase canonical) -> (label canonical, [upstream repos]).
# Order matters for prefix-style matching on Dependency column text.
COMPONENTS = {
    "driver":           ("dependency component: driver",           ["intel/compute-runtime", "intel/intel-graphics-compiler"]),
    "oneapi":           ("dependency component: oneAPI",           ["intel/llvm", "oneapi-src/level-zero"]),
    "onednn":           ("dependency component: oneDNN",           ["oneapi-src/oneDNN", "uxlfoundation/oneDNN"]),
    "oneccl":           ("dependency component: oneCCL",           ["oneapi-src/oneCCL", "intel/torch-ccl"]),
    "onemkl":           ("dependency component: oneMKL",           ["oneapi-src/oneMKL", "uxlfoundation/oneMath"]),
    "triton":           ("dependency component: Triton",           ["intel/intel-xpu-backend-for-triton", "triton-lang/triton"]),
    "upstream-pytorch": ("dependency component: upstream-pytorch", ["pytorch/pytorch"]),
}

DISPLAY_NAME = {
    "driver": "driver",
    "oneapi": "oneAPI",
    "onednn": "oneDNN",
    "oneccl": "oneCCL",
    "onemkl": "oneMKL",
    "triton": "Triton",
    "upstream-pytorch": "upstream-pytorch",
}

INTERNAL_TRACKER_PREFIXES = {
    "onednn": ("MFDNN",),
    "driver": ("IGC", "GSD", "PTI"),
    "oneapi": ("CMPLRLLVM", "CMPLRTOOLS", "LLVMSPIRV", "CMPLR"),
}
INTERNAL_TRACKER_RE = re.compile(
    r"\b(MFDNN|IGC|GSD|PTI|CMPLRLLVM|CMPLRTOOLS|LLVMSPIRV|CMPLR)-(\d{3,7})\b"
)


def _norm_label(s: str) -> str:
    return re.sub(r"[\s_\-]+", " ", (s or "").strip().lower())


def detect_components(dep_cell: str) -> list[str]:
    # Strict-word match against the Dependency cell.
    # `upstream-pytorch` requires the literal phrase (the bare word "pytorch"
    # is NOT recognized — would be ambiguous with unrelated repo refs).
    dep_low = (dep_cell or "").lower()
    found: list[str] = []
    for c in COMPONENTS:
        if re.search(rf"(?<![a-z0-9]){re.escape(c)}(?![a-z0-9])", dep_low):
            found.append(c)
    return found


def has_dep_label(labels_cell: str, component: str) -> bool:
    canonical = _norm_label(COMPONENTS[component][0])
    tokens = (t for t in (labels_cell or "").replace(";", ",").split(",") if t.strip())
    return any(_norm_label(t) == canonical for t in tokens)


REPO_RE  = r"[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+"
SHORT_RE = re.compile(rf"\b({REPO_RE})#(\d+)\b")
URL_RE   = re.compile(rf"https?://github\.com/({REPO_RE})/(?:issues|pull)/(\d+)")


def scan_refs_for_repos(text: str, repos: list[str]) -> list[tuple[str, int]]:
    repos_low = {r.lower() for r in repos}
    out: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    for m in SHORT_RE.finditer(text or ""):
        repo = m.group(1)
        if repo.lower() in repos_low:
            key = (repo, int(m.group(2)))
            if key not in seen:
                seen.add(key)
                out.append(key)
    for m in URL_RE.finditer(text or ""):
        repo = m.group(1)
        if repo.lower() in repos_low:
            key = (repo, int(m.group(2)))
            if key not in seen:
                seen.add(key)
                out.append(key)
    return out


# ---------------- Stage 1: emit worklist -------------------------------------

def emit_worklist(only_with_dep: bool) -> int:
    wb = load_workbook(XLSX_PATH, read_only=False)
    ws = wb["Issues"]
    worklist = []
    for r in range(2, ws.max_row + 1):
        iid = cell_by_name(ws, r, "Issue ID").value
        if iid is None:
            continue
        dep_cell = (cell_by_name(ws, r, "Dependency").value or "").strip()
        comps = detect_components(dep_cell)
        if only_with_dep and not comps:
            continue
        if not comps:
            pool = " ".join([
                (cell_by_name(ws, r, "Title").value or ""),
                (cell_by_name(ws, r, "Root Cause").value or ""),
                (cell_by_name(ws, r, "Fix Approach").value or ""),
                (cell_by_name(ws, r, "Labels").value or ""),
            ])
            comps = detect_components(pool)
            if not comps:
                continue
        try:
            reporter = cell_by_name(ws, r, "Reporter").value or ""
        except KeyError:
            reporter = ""
        worklist.append({
            "issue_id": iid,
            "title": cell_by_name(ws, r, "Title").value or "",
            "dependency_cell": dep_cell,
            "candidate_components": comps,
            "labels": cell_by_name(ws, r, "Labels").value or "",
            "root_cause": cell_by_name(ws, r, "Root Cause").value or "",
            "fix_approach": cell_by_name(ws, r, "Fix Approach").value or "",
            "action_TBD": cell_by_name(ws, r, "action_TBD").value or "",
            "action_reason": cell_by_name(ws, r, "action_reason").value or "",
            "assignee": cell_by_name(ws, r, "Assignee").value or "",
            "reporter": reporter,
            "owner_transferred": cell_by_name(ws, r, "owner_transferred").value or "",
        })
    PHASE4E_ROOT.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    WORKLIST_PATH.write_text(json.dumps(worklist, indent=2))
    print(f"wrote worklist: {WORKLIST_PATH} ({len(worklist)} issues)")
    return len(worklist)


# ---------------- Stage 2: merge results --------------------------------------

def load_ref_state_cache() -> dict:
    if REF_STATE_CACHE_PATH.exists():
        try:
            return json.loads(REF_STATE_CACHE_PATH.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def save_ref_state_cache(cache: dict) -> None:
    REF_STATE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    REF_STATE_CACHE_PATH.write_text(json.dumps(cache, indent=2, sort_keys=True))


def ref_state(repo: str, num: int, cache: dict) -> tuple[str, str]:
    """Return (state, kind) for repo#num. kind in {'pr','issue','unknown'}."""
    key = f"{repo}#{num}"
    entry = cache.get(key)
    if entry and (time.time() - entry.get("ts", 0)) < REF_STATE_TTL_SEC and "kind" in entry:
        return entry["state"], entry["kind"]
    state = "unknown"
    kind = "unknown"
    try:
        r = subprocess.run(
            ["gh", "pr", "view", str(num), "--repo", repo, "--json", "state,mergedAt"],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0:
            data = json.loads(r.stdout)
            pr_state = (data.get("state") or "").upper()
            if pr_state == "MERGED" or data.get("mergedAt"):
                state = "merged"
            elif pr_state == "OPEN":
                state = "open"
            elif pr_state == "CLOSED":
                state = "closed"
            if state != "unknown":
                kind = "pr"
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        pass
    if kind == "unknown":
        try:
            r = subprocess.run(
                ["gh", "issue", "view", str(num), "--repo", repo, "--json", "state"],
                capture_output=True, text=True, timeout=30,
            )
            if r.returncode == 0:
                data = json.loads(r.stdout)
                issue_state = (data.get("state") or "").upper()
                if issue_state == "OPEN":
                    state = "open"
                    kind = "issue"
                elif issue_state == "CLOSED":
                    state = "closed"
                    kind = "issue"
        except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
            pass
    cache[key] = {"state": state, "kind": kind, "ts": time.time()}
    return state, kind


def gh_issue_body_and_comments(num: int) -> tuple[str, str]:
    try:
        r = subprocess.run(
            ["gh", "issue", "view", str(num), "--repo", "intel/torch-xpu-ops",
             "--json", "body,comments"],
            capture_output=True, text=True, timeout=60,
        )
        if r.returncode != 0:
            return "", ""
        data = json.loads(r.stdout)
        body = data.get("body") or ""
        comments_concat = "\n".join((c.get("body") or "") for c in (data.get("comments") or []))
        return body, comments_concat
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        return "", ""


def append_verb_if_new(action_TBD: str, verb: str) -> tuple[str, bool]:
    if not action_TBD:
        return verb, True
    if verb.strip() in action_TBD:
        return action_TBD, False
    return action_TBD.rstrip("; ") + "; " + verb, True


def merge_results(only_with_dep: bool, dry_run: bool) -> None:
    if not WORKLIST_PATH.exists():
        print(f"ERROR: worklist missing at {WORKLIST_PATH}; run --emit-worklist first", file=sys.stderr)
        sys.exit(1)
    worklist_by_id = {entry["issue_id"]: entry for entry in json.loads(WORKLIST_PATH.read_text())}
    print(f"loaded worklist: {len(worklist_by_id)} issues")

    result_files = sorted(RESULTS_DIR.glob("*.json"))
    print(f"loaded {len(result_files)} agent result files from {RESULTS_DIR}")
    results = {}
    for f in result_files:
        try:
            data = json.loads(f.read_text())
            results[data["issue_id"]] = data
        except (json.JSONDecodeError, KeyError):
            print(f"  WARN: skipping malformed {f.name}", file=sys.stderr)

    missing = sorted(set(worklist_by_id) - set(results))
    if missing:
        print(f"WARN: {len(missing)} worklist issues lack a result file; will be skipped")
        if len(missing) <= 20:
            print(f"  missing: {missing}")

    if dry_run:
        backup_path = None
    else:
        backup_path = XLSX_PATH.with_name(
            XLSX_PATH.stem + f"_bk_before_phase4e_{time.strftime('%Y%m%d_%H%M%S')}.xlsx"
        )
        shutil.copy2(XLSX_PATH, backup_path)
        print(f"backup -> {backup_path.name}")

    wb = load_workbook(XLSX_PATH)
    ws = wb["Issues"]
    cache = load_ref_state_cache()
    cache_initial = len(cache)

    rows_changed = 0
    verb_counts = defaultdict(int)
    deps_cleared = 0
    deps_retagged = 0

    for r in range(2, ws.max_row + 1):
        iid = cell_by_name(ws, r, "Issue ID").value
        if iid is None or iid not in results:
            continue
        result = results[iid]
        wl = worklist_by_id[iid]
        verdict = result.get("verdict")
        component = (result.get("component") or "").lower()
        reason = (result.get("reason") or "").strip()
        upstream_ref = result.get("upstream_ref")

        if verdict == "false_dep":
            if wl["dependency_cell"]:
                if not dry_run:
                    write_by_name(ws, r, "Dependency", "")
                    write_by_name(ws, r, "dependency_reason", "")
                deps_cleared += 1
                rows_changed += 1
            continue

        if verdict != "true_dep":
            continue
        if component not in COMPONENTS:
            print(f"  WARN: issue {iid} verdict=true_dep but component '{component}' not tracked; skipping")
            continue

        repo_num: Optional[tuple[str, int]] = None
        if isinstance(upstream_ref, str):
            m = re.search(r"([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)#(\d+)", upstream_ref)
            upstream_ref = {"repo": m.group(1), "num": int(m.group(2))} if m else None
        if isinstance(upstream_ref, dict) and upstream_ref.get("repo") and upstream_ref.get("num"):
            repo_num = (upstream_ref["repo"], int(upstream_ref["num"]))
        else:
            repos = COMPONENTS[component][1]
            scan_text = "\n".join([
                wl.get("action_reason", ""),
                wl.get("root_cause", ""),
                wl.get("fix_approach", ""),
            ])
            refs = scan_refs_for_repos(scan_text, repos)
            if not refs:
                body, comments = gh_issue_body_and_comments(int(iid))
                refs = scan_refs_for_repos(body + "\n" + comments, repos)
            if refs:
                repo_num = refs[0]

        ref_kind = "unknown"
        ref_state_str = "no upstream ref"
        internal_ref: Optional[str] = None
        if repo_num:
            ref_state_str, ref_kind = ref_state(repo_num[0], repo_num[1], cache)
        else:
            allowed_prefixes = INTERNAL_TRACKER_PREFIXES.get(component, ())
            if allowed_prefixes:
                scan_text_internal = "\n".join([
                    wl.get("action_reason", ""),
                    wl.get("root_cause", ""),
                    wl.get("fix_approach", ""),
                ])
                for m in INTERNAL_TRACKER_RE.finditer(scan_text_internal):
                    if m.group(1) in allowed_prefixes:
                        internal_ref = f"{m.group(1)}-{m.group(2)}"
                        ref_state_str = "open"
                        ref_kind = "internal"
                        break

        if component == "upstream-pytorch":
            is_pt_core_issue = (
                ref_kind == "issue"
                and repo_num is not None
                and repo_num[0] == "pytorch/pytorch"
            )
            if not is_pt_core_issue:
                if wl["dependency_cell"]:
                    if not dry_run:
                        write_by_name(ws, r, "Dependency", "")
                        write_by_name(ws, r, "dependency_reason", "")
                    deps_cleared += 1
                    rows_changed += 1
                continue

        expected_dep_cell = DISPLAY_NAME[component]
        current_dep_cell = (cell_by_name(ws, r, "Dependency").value or "").strip()
        if current_dep_cell.lower() != expected_dep_cell.lower():
            if not dry_run:
                write_by_name(ws, r, "Dependency", expected_dep_cell)
            deps_retagged += 1
            rows_changed += 1

        labels_cell = cell_by_name(ws, r, "Labels").value or ""
        current_atbd = cell_by_name(ws, r, "action_TBD").value or ""
        current_owner = cell_by_name(ws, r, "owner_transferred").value or ""
        assignee = (cell_by_name(ws, r, "Assignee").value or "").strip()
        try:
            reporter = (cell_by_name(ws, r, "Reporter").value or "").strip()
        except KeyError:
            reporter = ""
        row_changed_local = False
        dep_state_token = ref_state_str
        dep_ref_token: Optional[str] = None

        if not has_dep_label(labels_cell, component):
            label_name = COMPONENTS[component][0]
            verb = f"Add label '{label_name}' - {reason}" if reason else f"Add label '{label_name}'"
            new_atbd, changed = append_verb_if_new(current_atbd, verb)
            if changed:
                if not dry_run:
                    write_by_name(ws, r, "action_TBD", new_atbd)
                current_atbd = new_atbd
                verb_counts["Add label"] += 1
                row_changed_local = True

        comp_display = DISPLAY_NAME[component]
        if repo_num:
            repo, num = repo_num
            dep_ref_token = f"{repo}#{num}"
            state = ref_state_str
            verb = None
            bucket_key = None
            new_owner = None
            if state == "open":
                verb = f"Wait for dependency fix {repo}#{num}"
                bucket_key = "Wait for dependency fix"
            elif state == "merged":
                verb = (f"Reporter to verify the fix from {repo}#{num} "
                        f"landed in {comp_display} and provide reason")
                bucket_key = "Verify (dep)"
                new_owner = reporter
            elif state == "closed":
                verb = (f"Reporter to re-investigate: upstream ref {repo}#{num} "
                        f"was closed without resolving and provide reason")
                bucket_key = "Re-investigate (dep)"
                new_owner = reporter
            if verb:
                new_atbd, changed = append_verb_if_new(current_atbd, verb)
                if changed:
                    if not dry_run:
                        write_by_name(ws, r, "action_TBD", new_atbd)
                    current_atbd = new_atbd
                    verb_counts[bucket_key] += 1
                    row_changed_local = True
                    if new_owner and new_owner != current_owner and not dry_run:
                        write_by_name(ws, r, "owner_transferred", new_owner)
                        current_owner = new_owner
        elif internal_ref:
            dep_ref_token = internal_ref
            verb = f"Wait for dependency fix {internal_ref}"
            new_atbd, changed = append_verb_if_new(current_atbd, verb)
            if changed:
                if not dry_run:
                    write_by_name(ws, r, "action_TBD", new_atbd)
                current_atbd = new_atbd
                verb_counts["Wait for dependency fix"] += 1
                row_changed_local = True
        else:
            verb = f"Assignee to submit issue to {comp_display} upstream - {reason}" if reason \
                else f"Assignee to submit issue to {comp_display} upstream"
            new_atbd, changed = append_verb_if_new(current_atbd, verb)
            if changed:
                if not dry_run:
                    write_by_name(ws, r, "action_TBD", new_atbd)
                current_atbd = new_atbd
                verb_counts["Submit issue"] += 1
                row_changed_local = True
                target_owner = assignee or current_owner
                if target_owner and target_owner != current_owner and not dry_run:
                    write_by_name(ws, r, "owner_transferred", target_owner)

        if dep_ref_token:
            dep_reason_text = f"{comp_display}: {dep_ref_token} ({dep_state_token}) — {reason}"
        else:
            dep_reason_text = f"{comp_display}: no upstream ref — {reason}"
        if not dry_run:
            write_by_name(ws, r, "dependency_reason", dep_reason_text)

        if row_changed_local:
            rows_changed += 1

    if not dry_run:
        wb.save(XLSX_PATH)
        save_ref_state_cache(cache)
    print(f"\nrows changed: {rows_changed}")
    print(f"dependency cells cleared (D1 false_dep): {deps_cleared}")
    print(f"dependency cells retagged (D1 true_dep, mismatch): {deps_retagged}")
    print(f"verbs added:")
    for k, n in sorted(verb_counts.items(), key=lambda kv: -kv[1]):
        print(f"  {n:4d}  {k}")
    print(f"ref-state cache: {len(cache)} entries ({len(cache) - cache_initial} new)")
    if dry_run:
        print("(dry-run: no workbook write, no cache save)")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--emit-worklist", action="store_true",
                    help="Stage 1: write per-issue worklist JSON for agents to consume")
    ap.add_argument("--merge", action="store_true",
                    help="Stage 2: read agent result JSONs and apply D2/D3 to xlsx")
    ap.add_argument("--all-issues", action="store_true",
                    help="Scope: include issues whose Dependency column is blank (rule definition: applies to all 300)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Stage 2 only: do not write workbook or cache")
    args = ap.parse_args()
    if not (args.emit_worklist or args.merge):
        ap.error("specify either --emit-worklist or --merge")
    if args.emit_worklist and args.merge:
        ap.error("--emit-worklist and --merge are mutually exclusive")
    only_with_dep = not args.all_issues
    if args.emit_worklist:
        n = emit_worklist(only_with_dep)
        print(f"Stage 1 complete: {n} issues in worklist")
    else:
        merge_results(only_with_dep, args.dry_run)


if __name__ == "__main__":
    main()
