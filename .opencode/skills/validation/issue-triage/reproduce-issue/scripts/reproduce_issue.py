# Copyright 2020-2025 Intel Corporation
# Licensed under the Apache License, Version 2.0

# pyright: reportUnusedImport=false, reportUnusedParameter=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnusedVariable=false, reportImplicitStringConcatenation=false

import re
import json
import sys
import os
import argparse
import subprocess
import signal
import time
import shlex


# Test types that indicate a unit-test style case. Compared case-insensitively
# where used (callers should lowercase the value before membership checks).
UT_TEST_TYPES = {"ut", "op_ut", "op_extend", "op_extended", "test_xpu"}

# Lines that are build/install commands rather than test-run commands. Defined
# here for later tasks that filter reproduce steps; matches leading command.
BUILD_INSTALL_RE = re.compile(
    r'^\s*(?:'
    r'pip3?\s+install'
    r'|python\s+setup\.py'
    r'|setup\.py'
    r'|cmake'
    r'|ninja'
    r'|conda\s+install'
    r')\b',
    re.IGNORECASE,
)


def normalize_test_file(test_file):
    """Normalize a polymorphic test_file to a forward-slash relative path.

    Accepts bare basenames, test/xpu/... paths, torch-xpu-ops/test/... paths,
    and Windows paths (drive letters, backslashes). Returns the path from the
    first test/xpu/ segment onward, else from the first test/ segment, else the
    bare basename. The result never contains a backslash or a drive prefix.
    """
    if not test_file:
        return ""
    path = test_file.replace('\\', '/')
    marker = 'test/xpu/'
    idx = path.find(marker)
    if idx != -1:
        return path[idx:]
    parts = path.split('/')
    for i, seg in enumerate(parts):
        if seg == 'test':
            return '/'.join(parts[i:])
    return parts[-1]


def decide_repo(case):
    """Return 'torch-xpu-ops' or 'pytorch' for a parsed case dict.

    Honors an explicit source when it is one of the two known repos. Otherwise
    infers from the test file: an _xpu stem basename, a torch-xpu-ops path
    segment, or a UT-type case with an _xpu basename map to torch-xpu-ops.
    Tolerates missing/null source, test_type, and test_file.
    """
    source = case.get("source")
    if source in ("torch-xpu-ops", "pytorch"):
        return source

    test_file = case.get("test_file") or ""
    norm = normalize_test_file(test_file)
    base = norm.rsplit('/', 1)[-1]
    stem = base[:-3] if base.endswith('.py') else base

    test_type = (case.get("test_type") or "")
    is_ut = test_type.lower() in UT_TEST_TYPES

    if stem.endswith('_xpu'):
        return "torch-xpu-ops"
    if 'torch-xpu-ops' in norm:
        return "torch-xpu-ops"
    if is_ut and base.endswith('_xpu'):
        return "torch-xpu-ops"
    return "pytorch"


def resolve_on_disk(pytorch_folder, repo, norm_file):
    """Resolve a normalized test file to an absolute on-disk path.

    Returns (abs_path_or_None, testdir_abs, rel_for_pytest_or_None).

    For torch-xpu-ops the primary test dir is
    <pf>/third_party/torch-xpu-ops/test/xpu; if that directory does not exist
    but <pf>/test/xpu does (standalone torch-xpu-ops checkout), the standalone
    dir is used. For pytorch the test dir is <pf>/test. A leading test/xpu/ or
    test/ segment is stripped from norm_file before joining. If the direct join
    misses, the testdir is walked for a basename match. If the chosen testdir
    does not exist, or the file is not found, returns (None, testdir, None).
    """
    norm_file = norm_file or ""

    if repo == "torch-xpu-ops":
        testdir = os.path.join(pytorch_folder, "third_party", "torch-xpu-ops", "test", "xpu")
        if not os.path.isdir(testdir):
            standalone = os.path.join(pytorch_folder, "test", "xpu")
            if os.path.isdir(standalone):
                testdir = standalone
        sub = norm_file
        if sub.startswith("test/xpu/"):
            sub = sub[len("test/xpu/"):]
    else:
        testdir = os.path.join(pytorch_folder, "test")
        sub = norm_file
        if sub.startswith("test/"):
            sub = sub[len("test/"):]

    if not os.path.isdir(testdir):
        return None, testdir, None

    candidate = os.path.join(testdir, sub) if sub else ""
    if candidate and os.path.isfile(candidate):
        abs_path = os.path.abspath(candidate)
        rel = os.path.relpath(abs_path, testdir)
        return abs_path, testdir, rel

    basename = sub.rsplit('/', 1)[-1] if sub else ""
    if basename:
        for root, _dirs, files in os.walk(testdir):
            if basename in files:
                abs_path = os.path.abspath(os.path.join(root, basename))
                rel = os.path.relpath(abs_path, testdir)
                return abs_path, testdir, rel

    return None, testdir, None


# Canonical error-line detector, copied verbatim from extract_basic_info.py
# (matches a final `ExceptionType: message` line). Kept identical for symmetry.
ERROR_LINE_RE = re.compile(
    r'^\s*(?:[A-Za-z_][\w\.]*(?:Error|Exception|Warning)|RuntimeError|AssertionError|'
    r'ValueError|TypeError|IndexError|KeyError|ImportError|NotImplementedError|'
    r'AttributeError|InductorError):\s*.+',
    re.MULTILINE,
)

# Path-like runs: optional drive letter then a slash/backslash and non-space run.
_PATH_RE = re.compile(r'(?:[A-Za-z]:)?[\\/][^\s]+')
_HEX_RE = re.compile(r'0x[0-9a-fA-F]+')
_LINE_NO_RE = re.compile(r'line \d+')
_WS_RE = re.compile(r'\s+')


def normalize_err(msg):
    """Strip volatile bits from an error message for robust comparison.

    Removes hex addresses, path-like tokens, and `line N` references, collapses
    whitespace, lowercases, and trims to ~200 chars. Safe on empty/None input.
    """
    if not msg:
        return ""
    s = str(msg)
    s = _HEX_RE.sub('', s)
    s = _PATH_RE.sub('', s)
    s = _LINE_NO_RE.sub('', s)
    s = _WS_RE.sub(' ', s)
    s = s.strip().lower()
    return s[:200]


# Status words that appear on pytest -v per-test result lines.
_STATUS_WORDS = ("PASSED", "FAILED", "ERROR", "SKIPPED", "XFAIL")


def _status_from_word(word):
    """Map a raw pytest status token to our outcome vocabulary."""
    w = word.upper()
    if w == "XFAIL":
        return "FAILED"
    return w


def parse_pytest_outcome(output, target_case, nodeid=None):
    """Parse pytest -v output for the outcome of a single test case.

    Returns (outcome, actual_error) where outcome is one of
    PASSED|FAILED|ERROR|SKIPPED|NOT_FOUND. XFAIL is treated as FAILED (the
    issue reproduces). actual_error is the last ERROR_LINE_RE match, stripped.
    """
    output = output or ""

    outcome = None

    # Prefer an exact nodeid match when provided.
    if nodeid:
        for line in output.splitlines():
            if nodeid in line:
                for w in _STATUS_WORDS:
                    if w in line:
                        outcome = _status_from_word(w)
                        break
            if outcome is not None:
                break

    # Else match a -v result line containing the exact target_case token,
    # bounded so test_foo does not match test_foo_bar. Prefer an exact
    # `::target_case`-terminated match over a param/space-terminated one.
    if outcome is None and target_case:
        exact_re = re.compile(
            r'::' + re.escape(target_case) + r'(?=[\s\[])'
        )
        loose_re = re.compile(
            r'(?:::' + re.escape(target_case) + r'(?=[\s\[])'
            r'|\b' + re.escape(target_case) + r'\b(?=\s))'
        )
        exact_match = None
        loose_match = None
        for line in output.splitlines():
            status = None
            for w in _STATUS_WORDS:
                if w in line:
                    status = _status_from_word(w)
                    break
            if status is None:
                continue
            if exact_re.search(line):
                exact_match = status
                break
            if loose_match is None and loose_re.search(line):
                loose_match = status
        if exact_match is not None:
            outcome = exact_match
        elif loose_match is not None:
            outcome = loose_match

    if outcome is None:
        # No per-test result line found. Distinguish an explicit no-collection
        # signal from the generic safe default; both currently map to NOT_FOUND.
        low = output.lower()
        if ("no tests ran" in low or "deselected" in low) and (
            "0 selected" in low or "collected 0 items" in low
        ):
            outcome = "NOT_FOUND"
        else:
            outcome = "NOT_FOUND"

    # Pytest prefixes assertion-detail lines with "E   "; strip that framing so
    # the canonical ERROR_LINE_RE can match the exception line beneath it.
    deframed = re.sub(r'(?m)^E {2,}', '', output)
    matches = ERROR_LINE_RE.findall(deframed)
    actual_error = matches[-1].strip() if matches else ""

    return outcome, actual_error


def extract_expected_signature(expected_traceback):
    """Extract (exc_type, key_msg) from a reference traceback.

    Uses the last ERROR_LINE_RE match. exc_type is the token before the first
    colon; key_msg is normalize_err of the text after the first colon. Returns
    ("", "") when no error line is present.
    """
    if not expected_traceback:
        return "", ""
    matches = ERROR_LINE_RE.findall(expected_traceback)
    if not matches:
        return "", ""
    last = matches[-1].strip()
    head, sep, tail = last.partition(":")
    if not sep:
        return "", ""
    return head.strip(), normalize_err(tail)


def judge(outcome, actual_error, expected_traceback):
    """Decide whether an outcome reproduces the referenced failure.

    Returns (reproduced, matched_error, reason). A failure/error with no
    reference traceback counts as reproduced on failure state only. With a
    reference, the exception types must match (case-insensitive) and the
    normalized key messages must overlap.
    """
    if outcome == "PASSED":
        return False, False, "test passed; issue not reproduced"
    if outcome == "SKIPPED":
        return False, False, "test skipped; needs skip removal to confirm"
    if outcome == "NOT_FOUND":
        return False, False, "no matching test found"

    if outcome in ("FAILED", "ERROR"):
        if not expected_traceback:
            return (
                True,
                False,
                "test failed; no reference traceback to compare "
                "(matched on failure state only)",
            )
        exc_type_exp, key_exp = extract_expected_signature(expected_traceback)
        exc_type_act, key_act = "", ""
        act_matches = ERROR_LINE_RE.findall(actual_error or "")
        if act_matches:
            last = act_matches[-1].strip()
            head, sep, tail = last.partition(":")
            if sep:
                exc_type_act = head.strip()
                key_act = normalize_err(tail)
        type_match = (
            bool(exc_type_exp)
            and bool(exc_type_act)
            and exc_type_exp.lower() == exc_type_act.lower()
        )
        msg_match = (
            (key_exp in key_act)
            or (key_act in key_exp)
            or key_exp == ""
        )
        if type_match and msg_match:
            return True, True, "same failure signature reproduced"
        return (
            False,
            False,
            "different failure: expected {}: {}, got {}: {}".format(
                exc_type_exp, key_exp[:80], exc_type_act, key_act[:80]
            ),
        )

    return False, False, "no matching test found"


def strip_build_install(steps_text):
    if not steps_text:
        return "", []
    kept = []
    skipped = []
    for line in str(steps_text).splitlines():
        if BUILD_INSTALL_RE.search(line):
            skipped.append(line)
        else:
            kept.append(line)
    return "\n".join(kept), skipped


def _clean_step_line(line):
    s = (line or "").strip()
    s = s.strip("`")
    s = re.sub(r'^(?:[-*+]\s+|\d+[.)]\s+)', '', s)
    return s.strip().strip("`")


def _extract_env_parts(line):
    parts = []
    for part in line.split(" && "):
        part = _clean_step_line(part)
        if not part or BUILD_INSTALL_RE.search(part):
            continue
        if part.startswith("source ") or part.startswith("export "):
            parts.append(part)
            continue
        tokens = part.split()
        env_tokens = []
        for token in tokens:
            if re.match(r'^[A-Z_][A-Z0-9_]*=\S+', token):
                env_tokens.append(token)
            else:
                break
        if env_tokens:
            parts.append(" ".join(env_tokens))
    return parts


def extract_env_setup(reproduce_steps):
    if not reproduce_steps:
        return []
    env_setup = []
    seen = set()
    for raw_line in str(reproduce_steps).splitlines():
        for part in _extract_env_parts(raw_line):
            if part not in seen:
                seen.add(part)
                env_setup.append(part)
    return env_setup


def apply_env_prefix(env_setup_lines):
    if not env_setup_lines:
        return ""
    return " && ".join(env_setup_lines)


class SetupError(Exception):
    """Run-level setup failure (bad conda env, torch import, no XPU device).

    The caller maps this to a CANNOT_VERIFY verdict with exit code 1.
    """
    pass


def run_subprocess(cmd, cwd, env=None, timeout=900, shell=False):
    """Run a command in its own process group and return (rc, output, timed_out).

    cmd is an argv list when shell is False, or a string when shell is True.
    start_new_session=True puts the child in a fresh process group so that on a
    timeout the whole group can be killed (prevents orphaned children). output
    is combined stdout+stderr as text. On timeout the process group is killed
    with SIGKILL, every member is reaped, and timed_out is True.

    Child output is redirected to a temporary file rather than a pipe. A pipe
    would spawn communicate() reader threads that keep the descriptor open, and
    a surviving descendant (e.g. an exec'd sleep) inheriting the pipe write-end
    can wedge the reap and any parent reading our own stdout. A file has no
    reader thread and no shared write-end to leak, so the reap is deterministic.
    """
    import tempfile

    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as outf:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            env=env,
            stdout=outf,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
            shell=shell,
        )
        # Capture the process-group id right after spawn (start_new_session
        # makes the child a group leader with pgid == pid). Capturing now avoids
        # losing the group id to a race where the child exits before the kill.
        try:
            pgid = os.getpgid(proc.pid)
        except (ProcessLookupError, OSError):
            pgid = proc.pid

        timed_out = False
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            # SIGKILL the whole group (SIGKILL cannot be caught, so one signal
            # terminates every member), then reap the direct child and poll the
            # group until no unreaped member remains. os.killpg(pgid, 0) probes
            # liveness: it raises ProcessLookupError once the group is empty.
            try:
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
            deadline = time.time() + 5.0
            while time.time() < deadline:
                try:
                    os.killpg(pgid, 0)
                except (ProcessLookupError, OSError):
                    break
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    break
                time.sleep(0.1)

        try:
            outf.seek(0)
            output = outf.read() or ""
        except Exception:
            output = ""

    return proc.returncode, (output or ""), timed_out


def probe_torch(conda_env):
    """Probe a conda env for torch version/commit and XPU availability.

    Runs a short `python -c` import inside the env from a neutral cwd (/tmp),
    never inside a pytorch checkout. Raises SetupError when the env is missing,
    torch fails to import, or no XPU device is present. On success returns
    {"torch_version", "torch_commit", "xpu_available": True}.
    """
    probe = (
        "import torch,sys; "
        "print(torch.__version__); "
        "print(getattr(torch.version,'git_version','') or ''); "
        "print(torch.xpu.is_available())"
    )
    cmd = [
        "conda", "run", "--no-capture-output", "-n", conda_env,
        "python", "-c", probe,
    ]
    rc, output, timed_out = run_subprocess(cmd, cwd="/tmp", timeout=120)

    if timed_out:
        raise SetupError(
            "torch probe timed out in env {}; CANNOT verify".format(conda_env)
        )

    low = output.lower()
    missing_env = (
        "environmentlocationnotfound" in low
        or "could not find conda environment" in low
        or "envlocationnotfound" in low
    )

    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    if rc != 0 or len(lines) < 3:
        if missing_env:
            raise SetupError(
                "conda environment {} not found (missing env); "
                "CANNOT verify".format(conda_env)
            )
        raise SetupError(
            "torch import/probe failed in env {} (rc={}); output tail: {}".format(
                conda_env, rc, output[-500:]
            )
        )

    # The 3 relevant values are the last 3 non-empty lines (conda run may emit
    # banner lines before them depending on configuration).
    torch_version, torch_commit, xpu_line = lines[-3], lines[-2], lines[-1]
    xpu_available = xpu_line.strip() == "True"
    if not xpu_available:
        raise SetupError(
            "no XPU device (torch.xpu.is_available() is False) in env {}".format(
                conda_env
            )
        )

    return {
        "torch_version": torch_version,
        "torch_commit": torch_commit,
        "xpu_available": True,
    }


def run_ut_case(
    case, pytorch_folder, repo, resolved, conda_env, timeout,
    pytest_timeout_ok=False,
):
    """Run a single unit-test case via pytest inside the conda env.

    resolved is the (abs, testdir, rel) tuple from resolve_on_disk. When abs is
    None the test file was not found on disk and a NO_TEST_FOUND dict is
    returned. Builds a pytest nodeid (with test_class/test_case) or a bare file
    plus -k selector, applies any env prefix extracted from reproduce_steps via
    a `bash -lc` wrapper, and runs with cwd set to the test dir.

    If the case carries `op_db_override_plugin_dir` /
    `op_db_override_plugin_module` (set by the agent after generating a
    remove-xpu-skips P7 non-editable-install fallback plugin via
    generate_op_db_override.py), the plugin's directory is put on
    PYTHONPATH and `-p <module>` is added so the in-memory op_db skip
    removal is loaded before pytest collects the test.
    """
    abs_path = resolved[0]
    if abs_path is None:
        return {
            "outcome": "NO_TEST_FOUND",
            "actual_error": "",
            "command": "",
            "exit_code": None,
            "duration_s": 0.0,
            "raw_tail": "",
            "skipped_build_lines": [],
            "timed_out": False,
            "reason": "test file not resolved on disk",
        }

    testdir = resolved[1]
    rel = resolved[2]
    test_case = case.get("test_case") or ""
    test_class = case.get("test_class") or ""

    pytest_args = []
    nodeid = None
    if test_class:
        target = "{}::{}".format(rel, test_class)
        if test_case:
            target = "{}::{}".format(target, test_case)
            nodeid = "{}::{}::{}".format(rel, test_class, test_case)
        pytest_args.append(target)
    else:
        pytest_args.append(rel)
        if test_case:
            pytest_args += ["-k", test_case]
    pytest_args.append("-v")
    if pytest_timeout_ok:
        pytest_args += ["--timeout", str(timeout)]

    plugin_module = case.get("op_db_override_plugin_module") or ""
    plugin_dir = case.get("op_db_override_plugin_dir") or ""
    if plugin_module:
        pytest_args += ["-p", plugin_module]

    env_setup = extract_env_setup(case.get("reproduce_steps") or "")
    if plugin_dir:
        # export (not a bare assignment) is required here: env_setup entries
        # are joined with " && " below, so an unexported "VAR=value" would
        # only set a shell variable in this chain, not propagate to the
        # exec'd pytest subprocess.
        env_setup = list(env_setup) + [
            'export PYTHONPATH="{}:$PYTHONPATH"'.format(plugin_dir)
        ]
    prefix = apply_env_prefix(env_setup)
    _, skipped_build = strip_build_install(case.get("reproduce_steps") or "")

    if prefix:
        script = "{} && pytest {}".format(prefix, shlex.join(pytest_args))
        cmd = [
            "conda", "run", "--no-capture-output", "-n", conda_env,
            "bash", "-lc", script,
        ]
    else:
        cmd = [
            "conda", "run", "--no-capture-output", "-n", conda_env, "pytest",
        ] + pytest_args

    start = time.time()
    rc, output, timed_out = run_subprocess(cmd, cwd=testdir, timeout=timeout)
    duration_s = time.time() - start

    command = shlex.join(cmd)
    raw_tail = output[-4000:]

    if timed_out:
        return {
            "outcome": "TIMEOUT",
            "actual_error": "",
            "command": command,
            "exit_code": rc,
            "duration_s": duration_s,
            "raw_tail": raw_tail,
            "skipped_build_lines": skipped_build,
            "timed_out": True,
        }

    outcome, actual_error = parse_pytest_outcome(
        output, test_case, nodeid=nodeid
    )
    return {
        "outcome": outcome,
        "actual_error": actual_error,
        "command": command,
        "exit_code": rc,
        "duration_s": duration_s,
        "raw_tail": raw_tail,
        "skipped_build_lines": skipped_build,
        "timed_out": False,
    }


def run_non_ut_case(case, pytorch_folder, conda_env, timeout):
    """Run a non-UT case's reproduce_steps verbatim inside the conda env.

    The reproduce_steps are executed as-is via `bash -lc` with cwd set to the
    pytorch folder. Outcome is FAILED on non-zero exit, PASSED otherwise, or
    TIMEOUT on timeout. actual_error is the last ERROR_LINE_RE match in output.
    """
    steps = case.get("reproduce_steps") or ""
    if not steps:
        return {
            "outcome": "CANNOT_VERIFY",
            "reason": "no reproduce_steps for non-ut",
            "actual_error": "",
            "command": "",
            "exit_code": None,
            "duration_s": 0.0,
            "raw_tail": "",
            "timed_out": False,
        }

    cmd = [
        "conda", "run", "--no-capture-output", "-n", conda_env,
        "bash", "-lc", steps,
    ]
    start = time.time()
    rc, output, timed_out = run_subprocess(
        cmd, cwd=pytorch_folder, timeout=timeout
    )
    duration_s = time.time() - start

    command = shlex.join(cmd)
    raw_tail = output[-4000:]

    if timed_out:
        return {
            "outcome": "TIMEOUT",
            "actual_error": "",
            "command": command,
            "exit_code": rc,
            "duration_s": duration_s,
            "raw_tail": raw_tail,
            "timed_out": True,
        }

    outcome = "FAILED" if rc != 0 else "PASSED"
    matches = ERROR_LINE_RE.findall(output)
    actual_error = matches[-1].strip() if matches else ""
    return {
        "outcome": outcome,
        "actual_error": actual_error,
        "command": command,
        "exit_code": rc,
        "duration_s": duration_s,
        "raw_tail": raw_tail,
        "timed_out": False,
    }


# Top-level key in final_output.json holding the reproduce report.
REPRODUCE_RESULT_KEY = "reproduce_result"


def _backup_file(path):
    """Copy path to path + '.bak' (best effort); ignore failures."""
    try:
        with open(path, "r", encoding="utf-8") as src:
            data = src.read()
        with open(path + ".bak", "w", encoding="utf-8") as dst:
            dst.write(data)
    except OSError:
        pass


def resolve_agent_space_dir(agent_space_dir, agent_space_root, repo, issue_id):
    """Resolve the per-issue agent_space directory to update, or None.

    An explicit --agent-space-dir wins. Otherwise, when both a root and an
    issue id are given, the conventional folder name
    '<repo_with_slashes_as_underscores>_issue_<id>' is joined onto the root.
    Returns the directory path (which may not exist) or None when unresolvable.
    """
    if agent_space_dir:
        return agent_space_dir
    if agent_space_root and issue_id:
        slug = str(repo or "").replace("/", "_")
        name = "{}_issue_{}".format(slug, issue_id) if slug else "issue_{}".format(issue_id)
        return os.path.join(agent_space_root, name)
    return None


def update_agent_space_json(agent_space_dir, report):
    """Update reproduce info in existing agent_space JSON files, in place.

    Only touches files that already exist; never creates them. Each modified
    file is first copied to a '.bak' sibling.

      - step2_reproduce.json: the reproduce report IS the whole file, so it is
        overwritten with `report`.
      - final_output.json: only the top-level REPRODUCE_RESULT_KEY is replaced
        with `report`; every other key is left byte-for-byte untouched.

    Returns a list of (path, action) tuples describing what was done.
    """
    actions = []
    if not agent_space_dir or not os.path.isdir(agent_space_dir):
        if agent_space_dir:
            actions.append((agent_space_dir, "skipped (directory not found)"))
        return actions

    step2_path = os.path.join(agent_space_dir, "step2_reproduce.json")
    if os.path.isfile(step2_path):
        _backup_file(step2_path)
        with open(step2_path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        actions.append((step2_path, "overwrote reproduce report"))

    final_path = os.path.join(agent_space_dir, "final_output.json")
    if os.path.isfile(final_path):
        try:
            with open(final_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError):
            data = None
        if isinstance(data, dict):
            _backup_file(final_path)
            data[REPRODUCE_RESULT_KEY] = report
            with open(final_path, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
            actions.append((final_path, "updated {}".format(REPRODUCE_RESULT_KEY)))
        else:
            actions.append(
                (final_path, "skipped (not a JSON object / unparseable)")
            )

    return actions


def normalize_cases(input_obj):
    """Normalize a polymorphic input object to a list of case dicts.

    Accepts one of:
      (a) a single case dict (has "test_case" or "test_file");
      (b) an extract-basic-info object with "test_cases": [...] (top-level
          "traceback" and "reproduce_steps" are read too);
      (c) a bare list of case dicts.

    Returns (cases_list, top_traceback_str, top_reproduce_steps_str). Missing
    or null fields are tolerated; the two top-level strings default to "".
    """
    if isinstance(input_obj, list):
        cases = [c for c in input_obj if isinstance(c, dict)]
        return cases, "", ""

    if isinstance(input_obj, dict):
        if "test_cases" in input_obj:
            raw = input_obj.get("test_cases") or []
            cases = [c for c in raw if isinstance(c, dict)]
            top_tb = input_obj.get("traceback") or ""
            top_steps = input_obj.get("reproduce_steps") or ""
            return cases, str(top_tb), str(top_steps)
        if ("test_case" in input_obj) or ("test_file" in input_obj):
            return [input_obj], "", ""
        # Unknown dict shape: treat as a single (possibly empty) case.
        return [input_obj], "", ""

    return [], "", ""


def build_report(
    cases, env_info, run_case_fn, top_traceback, top_reproduce_steps,
    conda_env, pytorch_folder,
):
    """Assemble the full report dict from cases and a runner callback.

    run_case_fn(case) -> runner_dict is the single injection point: in main it
    is a closure that decides repo, resolves on disk, and dispatches to
    run_ut_case / run_non_ut_case; in tests it is mocked. The runner_dict may
    carry an "is_ut" bool, "test_repo", and "resolved_rel" that the closure
    computed; build_report falls back to per-case heuristics when absent.
    """
    single = len(cases) == 1
    top_traceback = top_traceback or ""
    top_reproduce_steps = top_reproduce_steps or ""

    results = []
    reproduced_count = 0
    cannot_verify_count = 0
    needs_skip_count = 0

    for case in cases:
        case = case if isinstance(case, dict) else {}
        test_case = case.get("test_case") or ""
        test_class = case.get("test_class") or ""
        test_file = case.get("test_file") or ""

        # Inherit top-level traceback/reproduce_steps into a case lacking its
        # own, but only when unambiguous: a single case, or the case name is
        # named in the top-level text.
        expected_traceback = case.get("traceback") or ""
        if not expected_traceback:
            if single or (test_case and test_case in top_traceback):
                expected_traceback = top_traceback

        reproduce_steps = case.get("reproduce_steps") or ""
        if not reproduce_steps:
            if single or (test_case and test_case in top_reproduce_steps):
                reproduce_steps = top_reproduce_steps
        # Feed the inherited reproduce_steps back so the runner can build env
        # prefixes from them without mutating the caller's original dict.
        if reproduce_steps and not case.get("reproduce_steps"):
            case = dict(case)
            case["reproduce_steps"] = reproduce_steps

        runner = run_case_fn(case) or {}
        is_ut = bool(runner.get("is_ut"))
        test_repo = runner.get("test_repo") or ""
        resolved_rel = runner.get("resolved_rel")

        outcome = runner.get("outcome") or ""
        actual_error = runner.get("actual_error") or ""
        command = runner.get("command") or ""
        exit_code = runner.get("exit_code")
        duration_s = runner.get("duration_s") or 0.0
        raw_tail = runner.get("raw_tail") or ""
        skipped_build_lines = runner.get("skipped_build_lines") or []
        runner_reason = runner.get("reason") or ""

        # Map runner outcome to the result enum and derive reproduced/matched.
        if outcome == "TIMEOUT":
            result = "CANNOT_VERIFY"
            reproduced, matched_error = False, False
            reason = runner_reason or "timeout"
        elif outcome == "CANNOT_VERIFY":
            result = "CANNOT_VERIFY"
            reproduced, matched_error = False, False
            reason = runner_reason or "cannot verify"
        elif outcome == "NO_TEST_FOUND":
            result = "NO_TEST_FOUND"
            reproduced, matched_error = False, False
            reason = runner_reason or "no matching test found"
        else:
            # PASSED / FAILED / ERROR / SKIPPED pass through as the result.
            result = outcome
            reproduced, matched_error, reason = judge(
                outcome, actual_error, expected_traceback
            )

        resolved_test_path = resolved_rel if resolved_rel else ""

        needs_skip_removal = bool(is_ut and result == "SKIPPED")
        if needs_skip_removal:
            skip_removal_request = {
                "test_file": resolved_rel if resolved_rel else test_file,
                "test_class": test_class,
                "target_method": test_case,
                "conda_env": conda_env,
                "pytorch_folder": pytorch_folder,
            }
        else:
            skip_removal_request = None

        entry = {
            "test_file": test_file,
            "test_case": test_case,
            "test_class": test_class,
            "test_repo": test_repo,
            "resolved_test_path": resolved_test_path,
            "result": result,
            "reproduced": reproduced,
            "matched_error": matched_error,
            "reason": reason,
            "command": command,
            "exit_code": exit_code,
            "actual_error": actual_error,
            "skipped": result == "SKIPPED",
            "duration_s": duration_s,
            "raw_tail": raw_tail,
            "skipped_build_lines": skipped_build_lines,
            "needs_skip_removal": needs_skip_removal,
            "skip_removal_request": skip_removal_request,
            "skip_removal_attempted": False,
            "skip_removal_result": "",
        }
        results.append(entry)

        if reproduced:
            reproduced_count += 1
        if result == "CANNOT_VERIFY":
            cannot_verify_count += 1
        if needs_skip_removal:
            needs_skip_count += 1

    total = len(results)
    not_reproduced = total - reproduced_count - cannot_verify_count

    return {
        "torch_version": env_info.get("torch_version", ""),
        "torch_commit": env_info.get("torch_commit", ""),
        "xpu_available": env_info.get("xpu_available", False),
        "conda_env": conda_env,
        "pytorch_folder": pytorch_folder,
        "summary": {
            "total": total,
            "reproduced": reproduced_count,
            "not_reproduced": not_reproduced,
            "cannot_verify": cannot_verify_count,
            "needs_skip_removal": needs_skip_count,
        },
        "results": results,
    }


def _case_is_ut(case, resolved):
    """UT if test_type is a UT type, or (no test_type but the file resolves)."""
    test_type = str(case.get("test_type") or "").lower()
    if test_type in UT_TEST_TYPES:
        return True
    if not test_type:
        has_target = bool(case.get("test_case") or case.get("test_file"))
        if has_target and resolved and resolved[0] is not None:
            return True
    return False


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Reproduce one or more torch-xpu-ops issue test cases and "
        "emit a JSON verdict report."
    )
    parser.add_argument(
        "--input",
        help="JSON string (single case, extract-basic-info object, or list). "
        "If omitted, read JSON from stdin.",
    )
    parser.add_argument("--conda-env", required=True, help="conda env name")
    parser.add_argument(
        "--pytorch-folder", required=True, help="path to the pytorch checkout"
    )
    parser.add_argument(
        "--timeout", type=int, default=600, help="per-case timeout in seconds"
    )
    parser.add_argument("--output", help="Optional path to also write the JSON")
    parser.add_argument(
        "--rerun",
        action="store_true",
        help="re-run a single case after skip removal; suppresses further "
        "skip-removal handoff.",
    )
    parser.add_argument(
        "--agent-space-dir",
        help="If set, update reproduce info in existing JSON files in this "
        "per-issue agent_space directory (never creates files).",
    )
    parser.add_argument(
        "--agent-space-root",
        help="Root holding per-issue agent_space folders; combined with "
        "--issue-id/--repo to locate the folder when --agent-space-dir is "
        "omitted.",
    )
    parser.add_argument(
        "--issue-id",
        help="Issue id used to derive the agent_space folder name under "
        "--agent-space-root.",
    )
    parser.add_argument(
        "--repo",
        default="intel/torch-xpu-ops",
        help="Repo slug used to derive the agent_space folder name.",
    )
    args = parser.parse_args(argv)

    conda_env = args.conda_env
    pytorch_folder = args.pytorch_folder
    timeout = args.timeout

    if not pytorch_folder or not os.path.isdir(pytorch_folder):
        print(
            "pytorch-folder not found or not a directory: {}".format(
                pytorch_folder
            ),
            file=sys.stderr,
        )
        sys.exit(1)

    raw = args.input if args.input is not None else sys.stdin.read()
    try:
        input_obj = json.loads(raw)
    except json.JSONDecodeError as err:
        print("invalid input JSON: {}".format(err), file=sys.stderr)
        sys.exit(2)

    cases, top_traceback, top_reproduce_steps = normalize_cases(input_obj)

    try:
        env_info = probe_torch(conda_env)
    except SetupError as err:
        report = {
            "conda_env": conda_env,
            "pytorch_folder": pytorch_folder,
            "result": "CANNOT_VERIFY",
            "reason": str(err),
            "results": [],
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        sys.exit(1)

    def run_case_fn(case):
        repo = decide_repo(case)
        norm = normalize_test_file(case.get("test_file") or "")
        resolved = resolve_on_disk(pytorch_folder, repo, norm)
        is_ut = _case_is_ut(case, resolved)
        if is_ut:
            runner = run_ut_case(
                case, pytorch_folder, repo, resolved, conda_env, timeout
            )
        else:
            runner = run_non_ut_case(case, pytorch_folder, conda_env, timeout)
        runner = dict(runner)
        runner["is_ut"] = is_ut
        runner["test_repo"] = repo
        runner["resolved_rel"] = resolved[2]
        return runner

    report = build_report(
        cases, env_info, run_case_fn, top_traceback, top_reproduce_steps,
        conda_env, pytorch_folder,
    )

    # --rerun: a single case is re-run fresh after remove-xpu-skips. If it is
    # still SKIPPED, do NOT loop again: report SKIPPED with a "maintained"
    # reason and clear the handoff flag. Carry the agent's skip_removal_*
    # markers from the input case into the emitted result.
    if args.rerun and report["results"]:
        in_case = cases[0] if cases else {}
        attempted = bool(in_case.get("skip_removal_attempted"))
        skip_result = in_case.get("skip_removal_result") or ""
        for entry in report["results"]:
            entry["skip_removal_attempted"] = attempted
            entry["skip_removal_result"] = skip_result
            if entry["result"] == "SKIPPED":
                entry["reason"] = (
                    "skip_maintained (open issue or reverted by "
                    "remove-xpu-skips)"
                )
            if entry["needs_skip_removal"]:
                entry["needs_skip_removal"] = False
                entry["skip_removal_request"] = None
        # Recompute the needs_skip_removal summary count after suppression.
        report["summary"]["needs_skip_removal"] = sum(
            1 for e in report["results"] if e["needs_skip_removal"]
        )

    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")

    agent_space_dir = resolve_agent_space_dir(
        args.agent_space_dir, args.agent_space_root, args.repo, args.issue_id
    )
    if agent_space_dir:
        for path, action in update_agent_space_json(agent_space_dir, report):
            print("agent_space: {}: {}".format(action, path), file=sys.stderr)

    sys.exit(0)


if __name__ == "__main__":
    main()
