"""Regression test for SKILL.md detector/classifier/path-rewrite rules (v1.6+).

Extractor-side tests (test_fix1) were removed in v1.6 when
`extract_reproducer_from_issue` left the runner. The remaining tests
cover behaviour that still lives in `run_local_verification.py`:
heredoc detection, absolute python-path detection, "no such file"
classifier output, and torch-xpu-ops path rewrite.
"""
import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL_DIR = HERE.parent.parent
sys.path.insert(0, str(SKILL_DIR))
import run_local_verification as M


def _build_driver(tmp_log_dir, pytorch_repo, xlsx_path):
    args = argparse.Namespace(
        lanes=["others"], only_lane=None, dry_run=True,
        issues=None, repro_timeout=60, no_color=True,
        ut_timeout=60,
        xlsx=str(xlsx_path),
        log_dir=str(tmp_log_dir),
        pytorch_repo=str(pytorch_repo),
    )
    d = M.Driver(args)
    d.issue_cache = {}
    return d


def test_fix2_heredoc_repro_detected_as_bash(d):
    body = (
        "cat > 'repro_embedding.py' <<'PYREPRO'\n"
        "import torch\n"
        "print(torch.xpu.is_available())\n"
        "PYREPRO\n"
        f"{sys.executable} repro_embedding.py\n"
    )
    kind = d._detect_repro_kind(body)
    assert kind == "bash", \
        f"Fix 2 broken: heredoc body classified as {kind!r}, want 'bash'"


def test_fix3_absolute_python_path_detected_as_bash(d):
    body = "/home/user/conda/envs/foo/bin/python script.py --device xpu\n"
    kind = d._detect_repro_kind(body)
    assert kind == "bash", \
        f"Fix 3 broken: absolute python path classified as {kind!r}, want 'bash'"

    body2 = "'/opt/intel/python3.10' another_script.py\n"
    kind2 = d._detect_repro_kind(body2)
    assert kind2 == "bash", \
        f"Fix 3 broken: quoted abs python3.10 classified as {kind2!r}, want 'bash'"


def test_fix4_classifier_missing_script_is_noreproducer(d):
    output_missing_py = (
        "/home/user/conda/bin/python: can't open file "
        "'/home/user/upstream/pytorch/test/missing.py': [Errno 2] "
        "No such file or directory\n"
    )
    v = d._classify_repro(2, output_missing_py)
    assert v == "noreproducer", \
        f"Fix 4 broken: missing script -> {v!r}, want 'noreproducer'"

    output_compileall_missing = (
        "Listing 'test/xpu/test_torch_xpu.py'...\n"
        "Can't list 'test/xpu/test_torch_xpu.py'\n"
    )
    v2 = d._classify_repro(0, output_compileall_missing)
    assert v2 == "noreproducer", \
        f"Fix 4 broken: compileall 'Can't list' -> {v2!r}, want 'noreproducer'"


def test_fix4_classifier_real_failure_stays_fail(d):
    output_traceback = (
        "Traceback (most recent call last):\n"
        "  File 'script.py', line 1, in <module>\n"
        "RuntimeError: XPU device error\n"
    )
    v = d._classify_repro(1, output_traceback)
    assert v == "fail", \
        f"Regression: real RuntimeError -> {v!r}, want 'fail' (Fix 4 must not over-mask)"


def test_fix5_path_rewrite_for_torch_xpu_ops(d, pytorch_repo):
    target = (
        pytorch_repo
        / "third_party"
        / "torch-xpu-ops"
        / "test"
        / "xpu"
        / "test_torch_xpu.py"
    )
    if not target.is_file():
        print(f"  SKIP fix5: {target} not present on this checkout")
        return
    body = "python -m compileall test/xpu/test_torch_xpu.py\n"

    cwd = pytorch_repo / "test"
    rewritten = body
    for path_match in re.findall(
        r"(?<![A-Za-z0-9/_-])((?:test|tests|benchmarks)/[A-Za-z0-9_./-]+\.py)",
        body,
    ):
        if (cwd / path_match).is_file():
            continue
        resolved = d._resolve_test_file(path_match)
        if resolved is not None:
            rewritten = rewritten.replace(path_match, str(resolved))
    assert str(target) in rewritten, \
        f"Fix 5 broken: path not rewritten to torch-xpu-ops absolute. got:\n{rewritten}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--pytorch-repo",
        default=str(Path.home() / "upstream" / "pytorch"),
    )
    parser.add_argument(
        "--xlsx",
        default=str(SKILL_DIR.parents[3] / "result" / "torch_xpu_ops_issues.xlsx"),
    )
    args = parser.parse_args()
    pytorch_repo = Path(args.pytorch_repo)
    xlsx_path = Path(args.xlsx)
    tmp_log_dir = HERE / "_runs" / "latest_log"
    tmp_log_dir.mkdir(parents=True, exist_ok=True)

    d = _build_driver(tmp_log_dir, pytorch_repo, xlsx_path)

    tests = [
        ("fix2_heredoc_repro_detected_as_bash",
         lambda: test_fix2_heredoc_repro_detected_as_bash(d)),
        ("fix3_absolute_python_path_detected_as_bash",
         lambda: test_fix3_absolute_python_path_detected_as_bash(d)),
        ("fix4_classifier_missing_script_is_noreproducer",
         lambda: test_fix4_classifier_missing_script_is_noreproducer(d)),
        ("fix4_classifier_real_failure_stays_fail",
         lambda: test_fix4_classifier_real_failure_stays_fail(d)),
        ("fix5_path_rewrite_for_torch_xpu_ops",
         lambda: test_fix5_path_rewrite_for_torch_xpu_ops(d, pytorch_repo)),
    ]
    fails = []
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS {name}")
        except AssertionError as e:
            print(f"  FAIL {name}: {e}")
            fails.append(name)
        except Exception as e:
            print(f"  ERROR {name}: {type(e).__name__}: {e}")
            fails.append(name)
    print()
    print(f"summary: passed={len(tests)-len(fails)}/{len(tests)}, failed={len(fails)}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
