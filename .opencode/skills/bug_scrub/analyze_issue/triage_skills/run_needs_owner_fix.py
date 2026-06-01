"""Fix NEEDS_OWNER mis-classification for issues that already have an owner.

Rules:
  owner ∈ {Triage, unassigned}           → keep NEEDS_OWNER (real owner still needed)
  real owner + pure NEEDS_OWNER          → reclassify to ROOT_CAUSE
  real owner + IMPLEMENT+NEEDS_OWNER     → drop NEEDS_OWNER (keep IMPLEMENT)
"""
import json
import sys
from pathlib import Path

import openpyxl

COMMON_DIR = Path(__file__).resolve().parents[2] / "_common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))
from header_utils import cell_by_name, write_by_name  # type: ignore[reportMissingImports] # noqa: E402
from paths import RESULT_DIR  # type: ignore[reportMissingImports] # noqa: E402

EXCEL = str(RESULT_DIR / "torch_xpu_ops_issues.xlsx")
STUB_OWNERS = {"triage", "unassigned", "none"}


def clean(v) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s.lower() == "none" else s


def main() -> None:
    wb = openpyxl.load_workbook(EXCEL)
    ws = wb["Issues"]

    n_root = n_impl = n_keep = 0
    for row in ws.iter_rows(min_row=2):
        row_idx = row[0].row
        at = clean(cell_by_name(ws, row_idx, "action_Type").value)
        parts = at.split("+") if at else []
        if "NEEDS_OWNER" not in parts:
            continue
        owner = clean(cell_by_name(ws, row_idx, "Assignee").value) or clean(cell_by_name(ws, row_idx, "owner_transferred").value)
        if not owner or owner.lower() in STUB_OWNERS:
            n_keep += 1
            continue

        if parts == ["NEEDS_OWNER"]:
            # pure NEEDS_OWNER with real owner → ROOT_CAUSE
            write_by_name(ws, row_idx, "action_Type", "ROOT_CAUSE")
            write_by_name(ws, row_idx, "action_TBD", json.dumps(
                [f"Assignee @{owner} to investigate"]))
            write_by_name(ws, row_idx, "action_reason", json.dumps(
                [f"Issue already assigned to @{owner}; owner to lead root-cause."]))
            n_root += 1
        elif "IMPLEMENT" in parts:
            # drop NEEDS_OWNER, keep IMPLEMENT
            new_parts = [p for p in parts if p != "NEEDS_OWNER"]
            write_by_name(ws, row_idx, "action_Type", "+".join(new_parts))
            write_by_name(ws, row_idx, "action_TBD", json.dumps(
                [f"Owner @{owner} to file fix PR"]))
            write_by_name(ws, row_idx, "action_reason", json.dumps(
                [f"Issue assigned to @{owner}; owner to implement fix."]))
            n_impl += 1
        else:
            n_keep += 1

    wb.save(EXCEL)
    print(f"ROOT_CAUSE reassigned:          {n_root}")
    print(f"NEEDS_OWNER dropped (IMPLEMENT): {n_impl}")
    print(f"Kept (stub owner):              {n_keep}")


if __name__ == "__main__":
    main()
