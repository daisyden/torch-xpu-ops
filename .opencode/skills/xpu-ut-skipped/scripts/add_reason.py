#!/usr/bin/env python3
"""Add a Reason column to ut_regression_pvc_cuda_skipped.xlsx.

Reason is derived from portability + the status of any related issue:

  * Not Applicable -- the test is fundamentally CUDA/NVIDIA/ROCm-specific and
    cannot run on XPU (NCCL, cuDNN/FA2 attention, ROCm CK/AOTriton SDPA, or a
    skip whose reason says CUDA-only / requires CUDA / not supported on this
    system).
  * Known issue -- portable AND skipped by a tracked intel/torch-xpu-ops issue
    that is still OPEN (or whose state could not be determined).
  * TBD -- portable AND either no tracked issue, or the tracked issue is already
    closed/fixed (a stale skip that is a candidate to re-enable).

Issue states are read from the gh cache populated by skipped_report.py and
topped up here for any issue referenced only inside Detailed Reason.

Usage:
    python add_reason.py --xlsx ut_regression_pvc_cuda_skipped.xlsx [--no-gh]
"""
import argparse
import json
import os
import re
import subprocess

from openpyxl import load_workbook

REPO = "intel/torch-xpu-ops"
CACHE = "/tmp/opencode/xpu_ops_issue_cache.json"
ISSUE_RE = re.compile(r"(?:issues/|#)(\d+)")

# skip-reason text that means the test is CUDA/NVIDIA/ROCm-only -> Not Applicable
NA_TEXT = (
    "cuda-only", "cuda specific", "cuda-specific", "requires cuda",
    "only on cuda", "cuda only", "not supported on this system", "cudnn",
    "rocm", "sm_", "nvidia", "magma",
)


def load_cache():
    if os.path.exists(CACHE):
        with open(CACHE) as fh:
            return json.load(fh)
    return {}


def save_cache(c):
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "w") as fh:
        json.dump(c, fh)


def issue_state(num, cache, enable_gh):
    key = str(num)
    if key in cache and cache[key] is not None:
        return cache[key].get("state")
    if not enable_gh:
        return None
    try:
        out = subprocess.run(
            ["gh", "api", f"repos/{REPO}/issues/{num}",
             "--jq", "{title: .title, state: .state}"],
            capture_output=True, text=True, timeout=30)
        cache[key] = json.loads(out.stdout) if out.returncode == 0 else None
    except Exception:  # noqa: BLE001
        cache[key] = None
    return cache[key].get("state") if cache.get(key) else None


def _base(name):
    return re.split(r"_(cuda|float|int|bool|device|dtype|\d)", name)[0]


def load_wontfix(enable_gh):
    """Return {test_token: (number, state)} from intel/torch-xpu-ops wontfix issues."""
    if not enable_gh:
        return {}
    try:
        out = subprocess.run(
            ["gh", "issue", "list", "--repo", REPO, "--label", "wontfix",
             "--state", "all", "--limit", "500", "--json", "number,title,state"],
            capture_output=True, text=True, timeout=60)
        issues = json.loads(out.stdout) if out.returncode == 0 else []
    except Exception:  # noqa: BLE001
        return {}
    idx = {}
    for i in issues:
        for t in re.findall(r"test_[a-zA-Z0-9_]{4,}", i.get("title", "")):
            idx.setdefault(t, (i["number"], i["state"]))
    return idx


def wontfix_hit(name, wf_idx):
    """A wontfix-tracked test is NA regardless of open/closed state."""
    b = _base(name)
    for t, hit in wf_idx.items():
        if t == name or name.startswith(t + "_") or t == b:
            return hit
    return None


# cat4 GPU-generic clusters that are actually portable on BMG -> TBD, not NA.
# (HAS_GPU / GPU_TYPE / requires_cuda_and_triton on a device-generic test, or
#  a pure PLATFORM_SUPPORTS_FLASH_ATTENTION_XPU flag gate.)
def tbd_gpu_generic(name, file):
    fl = file.lower()
    b = _base(name)
    if "test_ctx_manager" in fl and b.startswith("test_autocast"):
        return True
    if "inductor/test_indexing" in fl or "inductor.test_indexing" in fl:
        if any(k in name for k in ("masked_load", "combo_reduction",
                                   "explicit_dense", "reduction_invariant")):
            return True
    if "test_activation_checkpointing" in fl and name.endswith("_xpu"):
        return True
    return False


def is_not_applicable(name, klass, file, detail):
    if "NCCL" in klass or "nccl" in file.lower():
        return True
    if "backend_fa2" in name:
        return True
    if "sdpa_backend_aotriton" in name or "sdpa_backend_ck" in name:
        return True
    d = detail.lower()
    # op_db xpu xfail/skip means a real XPU bug -> portable, not NA
    if "device_type=xpu" in d:
        return False
    # Verified GPU-generic clusters run on BMG even though the skip text names
    # cuda -> keep as candidate (TBD), not NA.
    if tbd_gpu_generic(name, file):
        return False
    return any(t in d for t in NA_TEXT)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xlsx", default="ut_regression_pvc_cuda_skipped.xlsx")
    ap.add_argument("--no-gh", action="store_true")
    a = ap.parse_args()

    wb = load_workbook(a.xlsx)
    ws = wb.active
    H = [c.value for c in ws[1]]
    idx = {h: i for i, h in enumerate(H)}
    di, ni, ci, fi, Ii = (idx["Detailed Reason"], idx["XPU Test Name"],
                          idx["XPU Test Class"], idx["XPU Test File"],
                          idx["Issue"])
    reason_col = len(H) + 1
    ws.cell(row=1, column=reason_col, value="Reason")

    cache = load_cache()
    wf_idx = load_wontfix(not a.no_gh)
    counts = {"Not Applicable": 0, "Known issue": 0, "TBD": 0}
    for ri, row in enumerate(ws.iter_rows(min_row=2), start=2):
        vals = [c.value for c in row]
        detail = str(vals[di] or "")
        name = str(vals[ni] or "")
        klass = str(vals[ci] or "")
        file = str(vals[fi] or "")
        issue_col = str(vals[Ii] or "").strip()

        if is_not_applicable(name, klass, file, detail):
            reason = "Not Applicable"
        else:
            wf = wontfix_hit(name, wf_idx)
            if wf is not None:
                # wontfix-tracked (open or closed) -> Not Applicable, cite issue
                reason = "Not Applicable"
                if not issue_col:
                    row[Ii].value = f"#{wf[0]}"
            else:
                nums = set(ISSUE_RE.findall(detail))
                if issue_col:
                    nums.add(issue_col.lstrip("#"))
                if not nums:
                    reason = "TBD"
                else:
                    states = [issue_state(n, cache, not a.no_gh) for n in nums]
                    # open (or unknown) tracked issue -> Known issue;
                    # all closed/fixed -> stale skip -> TBD
                    if any(s in (None, "open") for s in states):
                        reason = "Known issue"
                    else:
                        reason = "TBD"
        ws.cell(row=ri, column=reason_col, value=reason)
        counts[reason] += 1

    save_cache(cache)
    wb.save(a.xlsx)
    print("Reason distribution:", counts)


if __name__ == "__main__":
    main()
