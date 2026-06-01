#!/usr/bin/env python3
"""Phase 2.5 runner: Local Case Verification.

For each issue whose CI status is blank/`not found`/`not_run` on ALL of its
rows in the relevant sheet (Test Cases for UT, E2E Test Cases for E2E), and
for every Others-sheet issue, run the test locally and aggregate a single
verdict written to `Issues.Local status`.

Phase 1.0 (env setup) must have run in this session — see
prepare_data/issue-basic-info-extraction/SKILL.md. This runner verifies the
env is usable but does not update it.

This is the executable counterpart of
`.opencode/skills/bug_scrub/analyze_ci_result/local-case-verification/SKILL.md`.

Usage:
    python run_local_verification.py [--xlsx PATH]
                                     [--lanes ut,e2e,others] [--all-lanes]
                                     [--only-lane {ut,e2e,others}]   (legacy)
                                     [--issues ID,ID,...] [--dry-run]
                                     [--ut-timeout SECS] [--repro-timeout SECS]

Default scope:
    Only the Others sheet is verified locally. UT and E2E lanes are opt-in
    via --lanes (e.g. --lanes ut,others) or --all-lanes. Phase 2.1/2.2 already
    cover UT/E2E via CI artifacts; Phase 2.5 by default fills only the Others
    gap (issues with no enumerable test, hence no CI coverage).

Defaults:
    --xlsx          $REPO/result/torch_xpu_ops_issues.xlsx
    --lanes         others
    --ut-timeout    600
    --repro-timeout 1800

Outputs:
    Test Cases.Local Status        (new column appended if absent; one row
                                    per UT test case, in sheet order)
    E2E Test Cases.Local Status    (new column appended if absent; the per-
                                    issue verdict is written to every row of
                                    that issue, since the E2E lane runs all
                                    reproducers for an issue as one script)
    Others.Local Status            (new column appended if absent; one row per
                                    Others-sheet issue, holding the verdict
                                    from running the issue's reproducer)
    local_logs/<issue_id>.log      per-issue stdout+stderr
    local_logs/run_summary.json    machine-readable per-issue verdict map
    local_logs/phase25.log         driver log (env update, decisions, errors)

Others-lane reproducer input:
    For each Others-sheet issue, the runner reads
    `<--log-dir>/reproducers/<issue_id>.sh` as the runnable bash reproducer.
    These files are produced by an upstream deep-extraction step (see
    SKILL.md §5) — this runner does not parse issue bodies itself. If the
    file is absent or empty, the issue is recorded as `noreproducer`.

The script never modifies per-row XPU Status / Stock Status columns; those
remain CI-authoritative.
"""

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

import openpyxl


SCRIPT_DIR = Path(__file__).resolve().parent
COMMON_DIR = SCRIPT_DIR.parents[1] / "_common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))
from header_utils import cell_by_name, ensure_col, get_col, header_index, write_by_name  # type: ignore[reportMissingImports] # noqa: E402
from paths import DATA_DIR, RESULT_DIR  # type: ignore[reportMissingImports] # noqa: E402

DEFAULT_XLSX = RESULT_DIR / "torch_xpu_ops_issues.xlsx"
DEFAULT_LOG_DIR = Path(os.environ.get(
    "BUG_SCRUB_LOCAL_LOGS", str(RESULT_DIR.parent / "local_logs")
))
DEFAULT_PYTORCH_REPO = Path(os.environ.get(
    "PYTORCH_REPO_ROOT", str(Path.home() / "upstream" / "pytorch")
))
ISSUES_JSON = DATA_DIR / "torch_xpu_ops_issues.json"
# Subdirectory of --log-dir holding pre-materialized reproducer scripts.
# Each file is named "<issue_id>.sh" and contains the full runnable bash
# reproducer for that Others-lane issue. Produced by an upstream
# deep-extraction step (model-driven); consumed verbatim by this runner.
REPRODUCERS_SUBDIR = "reproducers"

BLANK_CI_TOKENS = {"", "not found", "not_run", "n/a", "none"}
PERFORMANCE_TOKENS = (
    "performance regression",
    "performance dropped",
    "performance issue",
    "latency",
    "throughput",
    "slow performance",
    "performance slow",
    "execution time",
    "runtime performance",
    "performance fail",
)


def _is_blank_ci(value):
    if value is None:
        return True
    return str(value).strip().lower() in BLANK_CI_TOKENS


def _open_log(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    return open(path, "a", buffering=1)


class Driver:
    def __init__(self, args):
        self.args = args
        self.xlsx_path = Path(args.xlsx).resolve()
        self.log_dir = Path(args.log_dir).resolve()
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.driver_log = _open_log(self.log_dir / "phase25.log")
        self.summary = {}
        self.pytorch_repo = Path(args.pytorch_repo).resolve()
        self.dry_run = args.dry_run
        self.issue_cache = None
        self.wb: openpyxl.Workbook = openpyxl.Workbook()

    def log(self, msg):
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {msg}"
        print(line, flush=True)
        self.driver_log.write(line + "\n")

    def run(self, cmd, *, cwd=None, timeout=None, log_path=None, env=None):
        if isinstance(cmd, str):
            shell = True
            display = cmd
        else:
            shell = False
            display = " ".join(shlex.quote(c) for c in cmd)
        self.log(f"$ {display}" + (f"  (cwd={cwd})" if cwd else ""))
        if self.dry_run:
            return 0, "<dry-run>"
        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)
        try:
            proc = subprocess.run(
                cmd, cwd=cwd, shell=shell, env=merged_env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                timeout=timeout, text=True, errors="replace",
            )
            output = proc.stdout or ""
            rc = proc.returncode
        except subprocess.TimeoutExpired as e:
            raw = e.stdout
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="replace")
            output = (raw or "") + f"\n<TIMEOUT after {timeout}s>"
            rc = 124
        if log_path is not None:
            with open(log_path, "a") as f:
                f.write(f"$ {display}\n{output}\nEXIT={rc}\n\n")
        return rc, output

    # ------------------------------------------------------------------
    # Step 1: verify Phase 1.0 prerequisites
    #
    # Phase 1.0 (in prepare_data/issue-basic-info-extraction/SKILL.md) owns
    # conda env activation, nightly XPU torch+triton install, and source-
    # repo commit sync. This skill verifies the env is usable but never
    # updates it.

    def verify_env(self):
        if self.dry_run:
            self.log("env verify: SKIPPED (--dry-run)")
            return True
        self.log("env verify: checking torch.xpu availability (Phase 1.0 must have run)")
        rc, output = self.run(
            [sys.executable, "-c",
             "import torch; "
             "assert hasattr(torch, 'xpu'), 'torch.xpu package not available'; "
             "assert torch.xpu.is_available(), 'XPU device not available'; "
             "print('torch', torch.__version__, "
             "'git', getattr(torch.version, 'git_version', '')[:12])"],
            cwd=str(self.pytorch_repo / "test"),
        )
        if rc == 0:
            return True
        self.log(
            "env verify: XPU torch unavailable; skipping Phase 2.5 local verification"
        )
        self.summary["_env"] = {
            "verdict": "skipped",
            "reason": "XPU torch unavailable",
            "detail": output.strip(),
        }
        with open(self.log_dir / "run_summary.json", "w") as f:
            json.dump(self.summary, f, indent=2, sort_keys=True)
        return False

    # ------------------------------------------------------------------
    # Step 2: build per-issue work list

    def load_workbook(self):
        self.wb = openpyxl.load_workbook(self.xlsx_path)
        for sheet in ("Issues", "Test Cases", "E2E Test Cases"):
            if sheet not in self.wb.sheetnames:
                raise RuntimeError(f"missing sheet: {sheet}")

    def _header_map(self, ws):
        return header_index(ws)

    def _xpu_status_col(self, ws):
        hdr = self._header_map(ws)
        for cand in ("XPU Status", "status in torch-xpu-ops nightly", "XPU Accuracy Status"):
            if cand in hdr:
                return hdr[cand]
        return None

    def _stock_status_col(self, ws):
        hdr = self._header_map(ws)
        for cand in ("Stock Status", "status in pytorch stock CI"):
            if cand in hdr:
                return hdr[cand]
        return None

    def issue_info(self, issue_id):
        if self.issue_cache is None:
            cache = {}
            try:
                with open(ISSUES_JSON) as f:
                    for issue in json.load(f):
                        cache[str(issue.get("number"))] = issue
            except (FileNotFoundError, json.JSONDecodeError):
                cache = {}
            self.issue_cache = cache
        return self.issue_cache.get(str(issue_id), {})

    def is_performance_issue(self, issue_id):
        issue = self.issue_info(issue_id)
        title = str(issue.get("title") or "")
        body = str(issue.get("body") or "")
        labels = " ".join(str(label.get("name") or "") for label in issue.get("labels", []))
        text = f"{title}\n{labels}\n{body}".lower()
        return any(token in text for token in PERFORMANCE_TOKENS)

    def load_reproducer(self, issue_id):
        path = self.log_dir / REPRODUCERS_SUBDIR / f"{issue_id}.sh"
        if not path.is_file():
            return ""
        return path.read_text()

    def build_worklist(self):
        ut = self.wb["Test Cases"]
        e2e = self.wb["E2E Test Cases"]
        others = self.wb["Others"] if "Others" in self.wb.sheetnames else None
        issues = self.wb["Issues"]

        worklist = []
        ut_by_issue = defaultdict(list)
        e2e_by_issue = defaultdict(list)

        for row in ut.iter_rows(min_row=2, values_only=False):
            row_idx = row[0].row
            issue_id = cell_by_name(ut, row_idx, "Issue ID").value
            if not issue_id:
                continue
            xpu_status = cell_by_name(ut, row_idx, "XPU Status").value if get_col(ut, "XPU Status") else None
            stock_status = cell_by_name(ut, row_idx, "Stock Status").value if get_col(ut, "Stock Status") else None
            ut_by_issue[str(issue_id)].append({
                "row_idx": row_idx,
                "test_file": cell_by_name(ut, row_idx, "Test File").value if get_col(ut, "Test File") else None,
                "test_case": cell_by_name(ut, row_idx, "Test Case").value if get_col(ut, "Test Case") else None,
                "xpu_status": xpu_status,
                "stock_status": stock_status,
                "status": xpu_status,
                "eligible": _is_blank_ci(xpu_status) and _is_blank_ci(stock_status),
            })
        for row in e2e.iter_rows(min_row=2, values_only=False):
            row_idx = row[0].row
            issue_id = cell_by_name(e2e, row_idx, "Issue ID").value
            if not issue_id:
                continue
            status_header = "XPU Status" if get_col(e2e, "XPU Status") else "XPU Accuracy Status"
            stock_header = "Stock Status" if get_col(e2e, "Stock Status") else None
            xpu_status = cell_by_name(e2e, row_idx, status_header).value if status_header and get_col(e2e, status_header) else None
            stock_status = cell_by_name(e2e, row_idx, stock_header).value if stock_header else None
            e2e_by_issue[str(issue_id)].append({
                "row_idx": row_idx,
                "repro": cell_by_name(e2e, row_idx, "Test Reproducer").value if get_col(e2e, "Test Reproducer") else None,
                "xpu_status": xpu_status,
                "stock_status": stock_status,
                "status": xpu_status,
                "eligible": _is_blank_ci(xpu_status) and _is_blank_ci(stock_status),
            })

        if others is not None and get_col(others, "ID"):
            for row in others.iter_rows(min_row=2, values_only=False):
                issue_id = cell_by_name(others, row[0].row, "ID").value
                if not issue_id:
                    continue
                issue_id = str(issue_id)
                if self.is_performance_issue(issue_id):
                    worklist.append({
                        "issue_id": issue_id,
                        "lane": "OTHERS",
                        "skip_reason": "performance issue",
                    })
                    continue
                worklist.append({
                    "issue_id": issue_id,
                    "lane": "OTHERS",
                    "reproducer": self.load_reproducer(issue_id),
                })

        others_ids = {w["issue_id"] for w in worklist}

        for issue_id, rows in ut_by_issue.items():
            if issue_id in others_ids:
                continue
            eligible_rows = [r for r in rows if r["eligible"]]
            if eligible_rows:
                worklist.append({"issue_id": issue_id, "lane": "UT", "rows": eligible_rows})

        for issue_id, rows in e2e_by_issue.items():
            if issue_id in others_ids:
                continue
            if any(w["issue_id"] == issue_id for w in worklist):
                continue
            eligible_rows = [r for r in rows if r["eligible"]]
            if eligible_rows:
                worklist.append({"issue_id": issue_id, "lane": "E2E", "rows": eligible_rows})

        if self.args.only_lane:
            wanted = {self.args.only_lane.upper()}
        else:
            wanted = {lane.strip().upper() for lane in self.args.lanes.split(",") if lane.strip()}
        worklist = [w for w in worklist if w["lane"] in wanted]
        if self.args.issues:
            wanted_ids = set(self.args.issues.split(","))
            worklist = [w for w in worklist if w["issue_id"] in wanted_ids]

        self.log(f"worklist: {len(worklist)} issues "
                 f"(UT={sum(1 for w in worklist if w['lane']=='UT')}, "
                 f"E2E={sum(1 for w in worklist if w['lane']=='E2E')}, "
                 f"OTHERS={sum(1 for w in worklist if w['lane']=='OTHERS')})")
        return worklist

    # ------------------------------------------------------------------
    # Step 3-5: lane execution

    def _classify_pytest(self, rc, output):
        if rc == 0:
            return "pass"
        if rc == 124:
            return "timeout"
        if re.search(r"\bERROR\b.*collecting", output) or "ModuleNotFoundError" in output:
            return "error"
        if re.search(r"\b(no tests ran|no tests collected)\b", output):
            return "notfound"
        if re.search(r"\b(\d+)\s+failed", output):
            return "fail"
        if re.search(r"\b(\d+)\s+error", output):
            return "error"
        if re.search(r"\b(\d+)\s+skipped", output) and "failed" not in output:
            return "skipped"
        return "fail"

    def _classify_repro(self, rc, output):
        if rc == 124:
            return "timeout"
        if re.search(r"can't open file '[^']+\.py'.*No such file or directory", output) or \
           re.search(r"Can't list '[^']*\.py'", output) or \
           re.search(r"python.*: error: argument .*: can't open", output):
            return "noreproducer"
        if rc == 0 and not re.search(r"Traceback|RuntimeError|AssertionError", output):
            return "pass"
        return "fail"

    def _resolve_test_file(self, test_file):
        if not test_file:
            return None
        rel = str(test_file).lstrip("/")
        tail = rel[len("test/"):] if rel.startswith("test/") else rel
        xpu_tail = tail[len("xpu/"):] if tail.startswith("xpu/") else tail
        candidates = [
            self.pytorch_repo / rel,
            self.pytorch_repo / "third_party" / rel,
            self.pytorch_repo / "test" / rel,
            self.pytorch_repo / "third_party" / "torch-xpu-ops" / "test" / "xpu" / xpu_tail,
            self.pytorch_repo / "third_party" / "torch-xpu-ops" / "test" / "xpu" / tail,
            self.pytorch_repo / "third_party" / "torch-xpu-ops" / "test" / "xpu" / rel,
            self.pytorch_repo / "third_party" / "torch-xpu-ops" / "test" / rel,
        ]
        for cand in candidates:
            if cand.is_file():
                return cand.resolve()
        return None

    def run_ut(self, issue_id, rows, log_path):
        verdicts = []
        for idx, r in enumerate(rows):
            test_file = r.get("test_file")
            test_case = r.get("test_case")
            if not test_file or not test_case:
                verdicts.append("notfound")
                continue
            resolved = self._resolve_test_file(test_file)
            if resolved is None:
                self.log(f"  unresolved test_file: {test_file!r}")
                verdicts.append("notfound")
                continue
            cmd = [
                sys.executable, "-m", "pytest", str(resolved),
                "-k", test_case, "-v", "--tb=short",
            ]
            env = {"PYTORCH_TEST_WITH_SLOW": "1"}
            rc, output = self.run(
                cmd, cwd=str(resolved.parent),
                timeout=self.args.ut_timeout + 60,
                log_path=log_path, env=env,
            )
            verdicts.append(self._classify_pytest(rc, output))
        return verdicts

    def run_repro(self, issue_id, reproducer, log_path):
        if not reproducer or not str(reproducer).strip():
            return ["noreproducer"]
        body = str(reproducer).strip()
        kind = self._detect_repro_kind(body)
        if body.startswith("cat > "):
            kind = "bash"
        if kind == "text" or self._is_env_setup(body):
            log_path.write_text(
                f"repro classified as non-runnable ({'env-setup' if self._is_env_setup(body) else 'text'}); not run.\n\n"
                "---begin reproducer---\n" + body + "\n---end reproducer---\n"
            )
            return ["noreproducer"]
        if kind == "bash":
            cwd = self.pytorch_repo / "test"
            for path_match in re.findall(r"(?<![A-Za-z0-9/_-])((?:test|tests|benchmarks)/[A-Za-z0-9_./-]+\.py)", body):
                if (cwd / path_match).is_file():
                    continue
                resolved = self._resolve_test_file(path_match)
                if resolved is not None:
                    body = body.replace(path_match, str(resolved))
        if kind == "python":
            script = self.log_dir / f"repro_{issue_id}.py"
            script.write_text(body + "\n")
            cmd = [sys.executable, str(script)]
        else:  # bash / shell
            script = self.log_dir / f"repro_{issue_id}.sh"
            script.write_text("#!/usr/bin/env bash\nset -e\n" + body + "\n")
            script.chmod(0o755)
            cmd = ["bash", str(script)]
        rc, output = self.run(
            cmd,
            cwd=str(self.pytorch_repo / "test"),
            timeout=self.args.repro_timeout,
            log_path=log_path,
        )
        return [self._classify_repro(rc, output)]

    @staticmethod
    def _detect_repro_kind(body):
        """Return 'python', 'bash', or 'text'."""
        import re as _re
        # Reproducers that materialize a script via heredoc
        # (`cat > script.py <<'PYREPRO' ... PYREPRO`) and then invoke it are
        # bash scripts even though the heredoc payload dominates the first
        # lines and looks like Python.
        if _re.search(r"^\s*cat\s*>\s*\S+\s*<<\s*'?[A-Za-z_]+'?\s*$", body, _re.MULTILINE):
            return "bash"
        first_lines = body.splitlines()[:30]
        joined = "\n".join(first_lines)
        # Strong bash signals
        if _re.search(r"^#!/.*\b(bash|sh)\b", body, _re.MULTILINE):
            return "bash"
        if _re.search(r"^\s*(pip |conda |python |python3 |bash |sh |export |cd |make |cmake |\./)", joined, _re.MULTILINE):
            return "bash"
        if _re.search(r"^\s*'?\S*/(python|python3)(?:[.0-9]*)?'?\s", joined, _re.MULTILINE):
            return "bash"
        # Strong python signals
        if _re.search(r"^\s*(import |from \w+ import )", joined, _re.MULTILINE):
            return "python"
        if _re.search(r"\btorch\.\w+\(|\btorch\.\w+\.\w+\(", joined):
            return "python"
        if _re.search(r"^\s*(def |class |if __name__\s*==)", joined, _re.MULTILINE):
            return "python"
        # Fenced code block markers can hint
        if _re.search(r"```py(thon)?\b", body, _re.IGNORECASE):
            return "python"
        if _re.search(r"```(bash|sh|shell)\b", body, _re.IGNORECASE):
            return "bash"
        # Plain prose / feature request: short, no code punctuation
        if "(" not in joined and "=" not in joined and ";" not in joined:
            return "text"
        return "text"

    @staticmethod
    def _is_env_setup(body):
        """Detect reproducers that are environment-setup scripts.

        These contain package installs, network downloads, sudo, or repo clones
        that would mutate the host or hang on interactive prompts. Treat as
        non-runnable to avoid side effects and indefinite hangs.
        """
        import re as _re
        patterns = [
            r"^\s*conda\s+(install|create|env\s+(create|update))",
            r"^\s*pip3?\s+install\b",
            r"^\s*mamba\s+install\b",
            r"^\s*sudo\s+",
            r"^\s*apt(-get)?\s+(install|update)",
            r"^\s*yum\s+(install|update)",
            r"^\s*brew\s+install\b",
            r"^\s*wget\b",
            r"^\s*curl\s+-[A-Za-z]*[OL]",
            r"^\s*git\s+clone\b",
            r"intel-deep-learning-essentials.*\.sh",
            r"setvars\.sh\b",
        ]
        return any(_re.search(p, body, _re.MULTILINE) for p in patterns)

    # ------------------------------------------------------------------
    # Step 6-7: aggregate + persist

    @staticmethod
    def aggregate(row_verdicts):
        if not row_verdicts:
            return ""
        order = ["fail", "timeout", "error"]
        for v in order:
            if v in row_verdicts:
                return v
        if all(v == "pass" for v in row_verdicts):
            return "pass"
        if all(v == "skipped" for v in row_verdicts):
            return "skipped"
        if all(v == "notfound" for v in row_verdicts):
            return "notfound"
        if all(v == "noreproducer" for v in row_verdicts):
            return "noreproducer"
        return "mixed"

    def _ensure_local_status_column(self, sheet_name):
        ws = self.wb[sheet_name]
        existing = get_col(ws, "Local Status")
        col = ensure_col(ws, "Local Status")
        if existing is None:
            self.log(f"appended '{sheet_name}'.Local Status at col {col}")
        return col

    def write_results(self, per_issue_rows):
        """per_issue_rows: {issue_id: {'lane': ..., 'rows': [...], 'row_indices': [...]}}.
        UT verdicts are written to the specific Test Cases rows recorded as eligible
        in build_worklist (by absolute row index). E2E lane produces a single verdict
        per issue; it is written to every eligible E2E row of that issue."""
        self._ensure_local_status_column("Test Cases")
        self._ensure_local_status_column("E2E Test Cases")

        ws_ut = self.wb["Test Cases"]
        ut_written = 0
        for issue_id, info in per_issue_rows.items():
            if info.get("lane") != "UT":
                continue
            row_idxs = info.get("row_indices") or []
            verdicts = info.get("rows") or []
            for row_idx, verdict in zip(row_idxs, verdicts):
                if row_idx is None:
                    continue
                write_by_name(ws_ut, row_idx, "Local Status", verdict)
                ut_written += 1

        ws_e2e = self.wb["E2E Test Cases"]
        e2e_written = 0
        for issue_id, info in per_issue_rows.items():
            if info.get("lane") != "E2E":
                continue
            row_idxs = info.get("row_indices") or []
            verdicts = info.get("rows") or []
            single_verdict = verdicts[0] if verdicts else ""
            for row_idx in row_idxs:
                if row_idx is None:
                    continue
                write_by_name(ws_e2e, row_idx, "Local Status", single_verdict)
                e2e_written += 1

        others_written = 0
        if "Others" in self.wb.sheetnames:
            self._ensure_local_status_column("Others")
            ws_oth = self.wb["Others"]
            oth_id_key = "ID" if get_col(ws_oth, "ID") else "Issue ID"
            if get_col(ws_oth, oth_id_key):
                for row in ws_oth.iter_rows(min_row=2):
                    row_idx = row[0].row
                    iid = cell_by_name(ws_oth, row_idx, oth_id_key).value
                    if not iid:
                        continue
                    key = str(iid)
                    info = per_issue_rows.get(key)
                    if not info or info["lane"] != "OTHERS":
                        continue
                    if row_idx is None:
                        continue
                    write_by_name(ws_oth, row_idx, "Local Status", info["rows"][0] if info["rows"] else "")
                    others_written += 1

        backup = self.xlsx_path.with_name(
            self.xlsx_path.stem + f"_bk_before_phase25_{time.strftime('%Y%m%d_%H%M%S')}.xlsx"
        )
        if self.xlsx_path.exists() and not self.dry_run:
            import shutil
            shutil.copy2(self.xlsx_path, backup)
            self.log(f"backup: {backup.name}")
        if not self.dry_run:
            self.wb.save(self.xlsx_path)
        self.log(f"wrote Local Status: UT rows={ut_written}, E2E rows={e2e_written}, OTHERS rows={others_written}")

    # ------------------------------------------------------------------

    def execute(self):
        if not self.verify_env():
            return
        self.load_workbook()
        worklist = self.build_worklist()
        per_issue_rows = {}
        for item in worklist:
            issue_id = item["issue_id"]
            lane = item["lane"]
            log_path = self.log_dir / f"{issue_id}.log"
            log_path.write_text("")
            self.log(f"--- issue {issue_id} lane={lane} ---")
            if lane == "UT":
                row_verdicts = self.run_ut(issue_id, item["rows"], log_path)
            elif lane == "E2E":
                repros = [r.get("repro") for r in item["rows"]]
                joined = "\n".join(str(r) for r in repros if r)
                row_verdicts = self.run_repro(issue_id, joined, log_path)
            else:
                row_verdicts = self.run_repro(issue_id, item.get("reproducer"), log_path)
            agg = self.aggregate(row_verdicts)
            row_indices = [r.get("row_idx") for r in item.get("rows", [])] if lane in ("UT", "E2E") else []
            per_issue_rows[issue_id] = {"lane": lane, "rows": row_verdicts, "row_indices": row_indices}
            self.log(f"  -> verdict={agg} rows={row_verdicts}")
            self.summary[issue_id] = {"lane": lane, "rows": row_verdicts, "verdict": agg}
            if item.get("skip_reason"):
                self.summary[issue_id]["reason"] = item["skip_reason"]
        with open(self.log_dir / "run_summary.json", "w") as f:
            json.dump(self.summary, f, indent=2, sort_keys=True)
        self.write_results(per_issue_rows)


def main():
    p = argparse.ArgumentParser(description="Phase 2.5 — Local Case Verification")
    p.add_argument("--xlsx", default=str(DEFAULT_XLSX))
    p.add_argument("--log-dir", default=str(DEFAULT_LOG_DIR))
    p.add_argument("--pytorch-repo", default=str(DEFAULT_PYTORCH_REPO))
    p.add_argument("--skip-env-update", action="store_true",
                   help="DEPRECATED no-op (env update moved to Phase 1.0)")
    p.add_argument("--skip-commit-sync", action="store_true",
                   help="DEPRECATED no-op (commit sync moved to Phase 1.0)")
    p.add_argument("--only-lane", choices=["ut", "e2e", "others"],
                   help="Restrict to a single lane (legacy; prefer --lanes)")
    p.add_argument("--lanes", default="others",
                   help="Comma-separated lanes to run: ut,e2e,others. "
                        "Default: 'others' (Phase 2.5 only verifies the Others "
                        "sheet unless explicitly broadened). Use --all-lanes for ut+e2e+others.")
    p.add_argument("--all-lanes", action="store_true",
                   help="Run all lanes (ut, e2e, others). Overrides --lanes.")
    p.add_argument("--issues", help="Comma-separated issue IDs to restrict run to")
    p.add_argument("--ut-timeout", type=int, default=600)
    p.add_argument("--repro-timeout", type=int, default=1800)
    p.add_argument("--dry-run", action="store_true",
                   help="Plan only — never execute git/pip/pytest, never modify xlsx")
    args = p.parse_args()
    if args.all_lanes:
        args.lanes = "ut,e2e,others"
    driver = Driver(args)
    driver.execute()


if __name__ == "__main__":
    main()
