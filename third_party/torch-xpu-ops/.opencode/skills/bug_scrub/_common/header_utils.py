from openpyxl.styles import Font, PatternFill
from typing import Any


HEADER_FONT = Font(bold=True, color="FFFFFF")
HEADER_FILL = PatternFill(start_color="366092", end_color="366092", fill_type="solid")


def header_index(ws: Any) -> dict[str, int]:
    return {cell.value: idx for idx, cell in enumerate(ws[1], start=1) if cell.value is not None}


def get_col(ws: Any, name: str) -> int | None:
    return header_index(ws).get(name)


def ensure_col(ws: Any, name: str) -> int:
    col = get_col(ws, name)
    if col is not None:
        return col
    col = max(header_index(ws).values(), default=0) + 1
    cell = ws.cell(row=1, column=col, value=name)
    cell.font = HEADER_FONT
    cell.fill = HEADER_FILL
    return col


def cell_by_name(ws: Any, row: int, name: str) -> Any:
    col = get_col(ws, name)
    if col is None:
        raise KeyError(f"Missing column {name!r} in sheet {ws.title!r}")
    return ws.cell(row=row, column=col)


def write_by_name(ws: Any, row: int, name: str, value: Any) -> Any:
    return ws.cell(row=row, column=ensure_col(ws, name), value=value)


def row_dict(ws: Any, row: int) -> dict[str, Any]:
    headers = header_index(ws)
    return {name: ws.cell(row=row, column=col).value for name, col in headers.items()}
