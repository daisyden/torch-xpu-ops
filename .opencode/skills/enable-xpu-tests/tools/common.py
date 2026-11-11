"""Shared constants and small helpers for the enable-xpu-tests tools."""
import os
import re

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REFS_DIR = os.path.join(SKILL_DIR, "references")

WS = os.path.abspath(os.path.expanduser(os.environ.get("XPU_ENABLE_WS", "~/xpu_enable_ws")))
CLONE = os.path.join(WS, "pytorch")
ENV_DIR = os.path.join(WS, "env")
REPORT = os.path.join(WS, "xpu_enable_report")
WORKSPACE_JSON = os.path.join(WS, "workspace.json")

ISSUE_REPO = "intel/torch-xpu-ops"
PR_REPO = "pytorch/pytorch"

PORTABILITY = ["Portable", "Known Issue", "Feature gap", "Not Applicable"]
NON_PORTABLE = {"Known Issue", "Feature gap", "Not Applicable"}
REVIEW_CHOICES = ["", "Confirmed", "Needs work", "Rejected"]
RUN_KINDS = ["initial", "gate_removed", "rework"]


def slug(test_file: str) -> str:
    """test/inductor/test_fp8.py or inductor/test_fp8.py -> inductor_test_fp8."""
    tf = test_file.strip()
    if tf.startswith("test/"):
        tf = tf[5:]
    if tf.endswith(".py"):
        tf = tf[:-3]
    return tf.replace("/", "_")


def test_path(test_file: str) -> str:
    """Path of the test file relative to the clone root."""
    tf = test_file.strip()
    return tf if tf.startswith("test/") else "test/" + tf


def python_bin() -> str:
    p = os.path.join(ENV_DIR, "bin", "python")
    return p if os.path.exists(p) else "python"


_DEV_TOKEN_RE = re.compile(r"(?i)(?<![a-z])(cuda|xpu)(?![a-z])")


_DTYPE_SUFFIX = (r"(?:_(?:float|bfloat|complex|int|uint|bool|half|chalf|cfloat|cdouble|double|long|short|"
                 r"byte|char|float8_\w+)\d*)*")


def cuda_to_xpu(s: str) -> str:
    """Map a CUDA class/test id to its XPU instantiation.

    Only the instantiation device token is replaced: the class suffix CUDA and the
    last ``_cuda`` before any dtype suffix (so test_groupnorm_nhwc_cuda_cuda_float16
    -> test_groupnorm_nhwc_cuda_xpu_float16, and test_foo_cuda_fallback_bar stays)."""
    s = s or ""
    s = re.sub(r"CUDA$", "XPU", s)
    m = re.search(rf"_cuda({_DTYPE_SUFFIX})$", s)
    if m:
        return s[:m.start()] + "_xpu" + m.group(1)
    return s


def canon_dev(s: str) -> str:
    """Collapse every cuda/xpu device token so CUDA and XPU ids compare equal."""
    return _DEV_TOKEN_RE.sub("DEV", s or "")


def issue_url(number, repo: str = ISSUE_REPO) -> str:
    return f"https://github.com/{repo}/issues/{number}"


def parse_ref(ref: str, repo: str, kind: str = "issues") -> tuple[int, str]:
    """'123', '#123' or a GitHub URL -> (number, url)."""
    ref = (ref or "").strip().lstrip("#")
    if ref.startswith("http"):
        num = int(re.search(r"/(\d+)/?$", ref).group(1))
        return num, ref.rstrip("/")
    num = int(ref)
    return num, f"https://github.com/{repo}/{kind}/{num}"
