"""Generate bug_status_report.html from issue-triage orchestrator results.

Scans agent_space/issue_triage_orchestrator/ for completed triage folders,
reads final_output.json (plus sidecar JSONs), and emits a self-contained
interactive HTML report.

Usage:
    python3 gen_bug_status_report.py --repo intel/torch-xpu-ops
    python3 gen_bug_status_report.py --repo CuiYifeng/torch-xpu-ops-sandbox --out /tmp/report.html
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path

# ---- paths ---------------------------------------------------------------

THIS = Path(__file__).resolve()
REPO_ROOT = THIS.parents[5]
AGENT_SPACE = REPO_ROOT / "agent_space" / "issue_triage_orchestrator"
DEFAULT_OUT = AGENT_SPACE / "bug_status_report.html"

CANONICAL_CATEGORIES = [
    "Distributed",
    "Flash Attention",
    "Inductor",
    "TorchAO",
    "Sparse",
    "Torch Ops - gemm",
    "Torch Ops - eltwise",
    "Torch Ops - reduction",
    "Torch Ops - others",
    "Torch Runtime",
    "Others",
]

_CATEGORY_MAP = {
    "distributed": "Distributed",
    "flash attention": "Flash Attention",
    "sdpa": "Flash Attention",
    "inductor": "Inductor",
    "torch.compile": "Inductor",
    "dynamo": "Inductor",
    "triton": "Inductor",
    "aotautograd": "Inductor",
    "torchao": "TorchAO",
    "quantization": "TorchAO",
    "sparse": "Sparse",
    "torch ops": "Torch Ops - others",
    "torch operations": "Torch Ops - others",
    "torch runtime": "Torch Runtime",
    "runtime": "Torch Runtime",
    "others": "Others",
}

_SUBCATEGORY_MAP = {
    "gemm": "Torch Ops - gemm",
    "eltwise": "Torch Ops - eltwise",
    "reduction": "Torch Ops - reduction",
    "others": "Torch Ops - others",
}


def normalize_category(category: str, subcategory: str) -> str:
    """Map raw category+subcategory from triage to canonical bucket."""
    if not category:
        return "Others"

    cat_lower = category.lower().strip()
    sub_lower = (subcategory or "").lower().strip()

    for prefix, canonical in _CATEGORY_MAP.items():
        if cat_lower.startswith(prefix) or prefix in cat_lower:
            if canonical.startswith("Torch Ops") and sub_lower in _SUBCATEGORY_MAP:
                return _SUBCATEGORY_MAP[sub_lower]
            return canonical

    if sub_lower in _SUBCATEGORY_MAP:
        return _SUBCATEGORY_MAP[sub_lower]

    return "Others"


# ---- data loading --------------------------------------------------------

def repo_to_folder_prefix(repo: str) -> str:
    return repo.replace("/", "_") + "_issue_"


def load_issue_data(agent_space: Path, repo: str) -> list[dict]:
    prefix = repo_to_folder_prefix(repo)
    issues = []

    if not agent_space.exists():
        return issues

    for folder in sorted(agent_space.iterdir()):
        if not folder.is_dir():
            continue
        if not folder.name.startswith(prefix):
            continue

        final_output = folder / "final_output.json"
        if not final_output.exists():
            continue

        try:
            data = json.loads(final_output.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        for sidecar in ("update_label_result.json", "step2_reproduce.json",
                        "collect_opens_result.json"):
            key = sidecar.replace(".json", "")
            f = folder / sidecar
            if f.exists() and not data.get(key):
                try:
                    data[key] = json.loads(f.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    pass

        issues.append(data)

    return issues


# ---- data extraction helpers ---------------------------------------------

def get_nested(data: dict, *keys, default=""):
    cur = data
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
        if cur is None:
            return default
    return cur if cur is not None else default


_VERDICT_DISPLAY = {
    "NEED_FIX": "NEED_FIX",
    "NEED_FIX_CASE": "NEED_FIX_CASE",
    "NEED_FIX_3RDPARTY": "NEED_FIX_3RDPARTY",
    "NEED_HUMAN": "NEED_HUMAN",
    "NO_NEED_FIX": "NO_NEED_FIX",
}

_PROSE_TO_VERDICT = {
    "fix required (product code)": "NEED_FIX",
    "fix required (test case)": "NEED_FIX_CASE",
    "blocked — third-party dependency": "NEED_FIX_3RDPARTY",
    "blocked - third-party dependency": "NEED_FIX_3RDPARTY",
    "needs human review": "NEED_HUMAN",
    "inherited from duplicate": "NEED_FIX",
    "n/a — not reproduced": "NO_NEED_FIX",
    "n/a - not reproduced": "NO_NEED_FIX",
}


def _normalize_need_action(verdict: str, data: dict) -> str:
    if verdict and verdict in _VERDICT_DISPLAY:
        return _VERDICT_DISPLAY[verdict]
    notif_na = get_nested(data, "notification", "need_action") or ""
    if notif_na:
        mapped = _PROSE_TO_VERDICT.get(notif_na.lower().strip())
        if mapped:
            return mapped
        return notif_na
    top_na = data.get("need_action", "")
    if top_na and top_na in _VERDICT_DISPLAY:
        return _VERDICT_DISPLAY[top_na]
    return verdict or top_na or ""


def extract_row(data: dict) -> dict:
    issue = data.get("issue") or {}
    extract = data.get("extract_result") or {}
    reproduce = data.get("reproduce_result") or {}
    triage = data.get("triage_result") or {}
    update_label = data.get("update_label_result") or {}
    collect_opens = data.get("collect_opens_result") or {}

    # issue_id/url/title may live under "issue" sub-object or at top level
    issue_id = (issue.get("issue_id")
                or data.get("issue_id")
                or extract.get("issue_id")
                or "")
    url = (issue.get("url")
           or data.get("url")
           or extract.get("url")
           or "")
    if not url and issue_id:
        repo = data.get("repo") or ""
        if repo:
            url = f"https://github.com/{repo}/issues/{issue_id}"
    title = (issue.get("title")
             or data.get("title")
             or extract.get("title")
             or "")
    status = extract.get("status", extract.get("state", ""))

    verdicts = triage.get("verdicts", {}) or {}

    priority = (get_nested(triage, "priority", "priority")
                or get_nested(triage, "priority", "value")
                or get_nested(verdicts, "priority", "priority")
                or get_nested(verdicts, "priority", "value"))

    raw_category = (get_nested(triage, "category", "category")
                    or get_nested(triage, "category", "value")
                    or get_nested(verdicts, "category", "category")
                    or get_nested(verdicts, "category", "value"))
    raw_subcategory = (get_nested(triage, "category", "subcategory")
                       or get_nested(verdicts, "category", "subcategory"))
    category_display = normalize_category(raw_category, raw_subcategory)

    verdict = (get_nested(triage, "target_component", "verdict")
               or get_nested(verdicts, "target_component", "verdict")
               or triage.get("verdict", "")
               or triage.get("overall_verdict", "")
               or data.get("overall_verdict", ""))
    target_component = (get_nested(triage, "target_component", "component")
                        or get_nested(triage, "target_component", "target_component")
                        or get_nested(verdicts, "target_component", "component")
                        or get_nested(verdicts, "target_component", "target_component"))

    need_action = _normalize_need_action(verdict, data)
    need_action_tooltip = _build_verdict_tooltip(triage, data)

    root_cause = _extract_root_cause(triage)

    repro_results = reproduce.get("results", [])
    if repro_results:
        statuses = [r.get("result", "") for r in repro_results]
        if all(s == "PASSED" for s in statuses):
            repro_status = "Not Reproduced"
        elif any(r.get("reproduced") for r in repro_results):
            repro_status = "Reproduced"
        elif any(s == "CANNOT_VERIFY" for s in statuses):
            repro_status = "Cannot Verify"
        else:
            repro_status = "Failed"
    elif reproduce.get("status"):
        repro_status = reproduce["status"]
    else:
        repro_status = "N/A"

    repro_tooltip = _build_reproduce_tooltip(reproduce)

    label_actions = update_label.get("action_requests", [])
    label_display = "; ".join(
        ar.get("AR", "").replace("label_", "") for ar in label_actions
    )
    label_tooltip = "\n".join(
        f"{ar.get('AR', '')}: {ar.get('AR_REASON', '')}" for ar in label_actions
    )

    gh_commands = _build_gh_commands(issue_id, url, label_actions, data)

    dependency = (get_nested(triage, "target_component", "third_party_dependency")
                  or get_nested(verdicts, "target_component", "third_party_dependency"))
    if not dependency:
        dependency = get_nested(extract, "dependency") or ""
    if isinstance(dependency, dict):
        dependency = str(dependency)

    opens_ar = collect_opens.get("AR", "")
    opens_reason = collect_opens.get("AR_REASON", "")

    raw_platform = extract.get("platform", "") or ""
    if isinstance(raw_platform, dict):
        raw_platform = str(raw_platform)
    platform_specific = _extract_platform_specific(raw_platform)
    os_val = extract.get("os", "") or ""
    labels = extract.get("labels", []) or []
    pytorch_ci = "Yes" if "pytorch-ci-failure" in labels else ""

    issue_type = extract.get("issue_type", "") or extract.get("type", "") or ""
    if issue_type:
        issue_type = issue_type.capitalize()
    test_type = extract.get("test_type", "")
    if not test_type:
        if any("module: ut" in l for l in labels):
            test_type = "ut"
    test_type = test_type or ""

    if verdict == "NEED_FIX_3RDPARTY" and dependency:
        target_component = dependency

    return {
        "issue_id": str(issue_id),
        "url": url,
        "title": title,
        "status": status,
        "issue_type": issue_type,
        "test_type": test_type,
        "priority": priority,
        "category": category_display,
        "root_cause": root_cause,
        "target_component": target_component,
        "repro_status": repro_status,
        "repro_tooltip": repro_tooltip,
        "need_action": need_action,
        "need_action_tooltip": need_action_tooltip,
        "label_display": label_display,
        "label_tooltip": label_tooltip,
        "gh_commands": gh_commands,
        "dependency": dependency,
        "opens_ar": opens_ar,
        "opens_reason": opens_reason,
        "platform_specific": platform_specific,
        "os": os_val,
        "pytorch_ci": pytorch_ci,
    }


_GENERIC_PLATFORMS = {
    "", "xpu", "gpu", "intel gpu", "intel xpu", "linux", "windows",
    "x86_64", "x86", "amd64", "aarch64", "arm64",
}


def _extract_platform_specific(raw: str) -> str:
    """Return the platform name if it identifies a specific GPU, else blank."""
    if raw.lower().strip() in _GENERIC_PLATFORMS:
        return ""
    return raw


def _extract_root_cause(triage: dict) -> str:
    """Pull the best root-cause string from various triage JSON schemas."""
    verdicts = triage.get("verdicts", {}) or {}
    rc = triage.get("root_cause", "")
    if not rc:
        rc = triage.get("summary", "")
    if not rc:
        rc = (get_nested(triage, "target_component", "root_cause")
              or get_nested(triage, "target_component", "root_cause_hypothesis")
              or get_nested(verdicts, "target_component", "root_cause_hypothesis")
              or get_nested(verdicts, "target_component", "evidence"))
    if not rc:
        rc = (get_nested(triage, "target_component", "reason")
              or get_nested(triage, "target_component", "evidence")
              or get_nested(verdicts, "target_component", "evidence"))
    if not rc:
        rc = (get_nested(triage, "category_analysis", "reason")
              or get_nested(triage, "priority_analysis", "reason"))
    if not rc:
        rc = triage.get("short_circuit_reason", "")
    if isinstance(rc, dict):
        rc = rc.get("reason", "") or str(rc)
    return str(rc) if rc else ""


def _build_verdict_tooltip(triage: dict, data: dict) -> str:
    """Build a brief tooltip for the Need Action column."""
    verdicts = triage.get("verdicts", {}) or {}
    parts = []

    verdict = (get_nested(triage, "target_component", "verdict")
               or get_nested(verdicts, "target_component", "verdict")
               or triage.get("verdict", "")
               or triage.get("overall_verdict", "")
               or data.get("overall_verdict", ""))
    if verdict:
        parts.append(f"Verdict: {verdict}")

    if triage.get("short_circuit"):
        reason = triage.get("short_circuit_reason", "")
        if reason:
            parts.append(f"Reason: {reason}")
        return "\n".join(parts)

    component = (get_nested(triage, "target_component", "component")
                 or get_nested(verdicts, "target_component", "component"))
    if component:
        parts.append(f"Component: {component}")

    dep = (get_nested(triage, "target_component", "third_party_dependency")
           or get_nested(verdicts, "target_component", "third_party_dependency"))
    if dep:
        parts.append(f"Dependency: {dep}")

    rc = _extract_root_cause(triage)
    if rc:
        brief = rc[:200] + ("..." if len(rc) > 200 else "")
        parts.append(f"Root cause: {brief}")

    return "\n".join(parts) if parts else ""


def _build_reproduce_tooltip(reproduce: dict) -> str:
    if not reproduce:
        return ""

    parts = []
    torch_ver = reproduce.get("torch_version", "")
    if torch_ver:
        commit = reproduce.get("torch_commit", "")
        parts.append(f"Torch: {torch_ver}" + (f" ({commit[:8]})" if commit else ""))

    test_time = reproduce.get("test_time", "")
    if test_time:
        parts.append(f"Tested: {test_time}")

    results = reproduce.get("results", [])
    if results:
        for r in results:
            tc = r.get("test_case", "unknown")
            res = r.get("result", "N/A")
            reason = r.get("reason", "")
            line = f"{tc}: {res}"
            if reason:
                brief = reason[:120] + ("..." if len(reason) > 120 else "")
                line += f" - {brief}"
            parts.append(line)

    return "\n".join(parts) if parts else ""


def _build_gh_commands(issue_id, url: str, label_actions: list, data: dict) -> str:
    if not label_actions or not url:
        return ""

    m = re.match(r"https://github\.com/([^/]+/[^/]+)/issues/(\d+)", url)
    if not m:
        return ""
    repo = m.group(1)
    iid = m.group(2)

    labels_to_add = []
    for ar in label_actions:
        ar_type = ar.get("AR", "")
        if ar_type == "label_dependency":
            dep = get_nested(data, "triage_result", "target_component", "third_party_dependency")
            if dep:
                labels_to_add.append(f"dependency: {dep}")
        elif ar_type == "label_priority":
            prio = (get_nested(data, "triage_result", "priority", "priority")
                    or get_nested(data, "triage_result", "priority", "value"))
            if prio:
                labels_to_add.append(f"priority: {prio}")
        elif ar_type == "label_type":
            labels_to_add.append("bug")
        elif ar_type == "label_not_target":
            labels_to_add.append("not_target")

    if not labels_to_add:
        return ""
    label_str = ",".join(f'"{l}"' for l in labels_to_add)
    return f"gh issue edit {iid} --repo {repo} --add-label {label_str}"


# ---- HTML generation -----------------------------------------------------

def generate_html(issues: list[dict], repo: str, conda_env: str, pytorch_folder: str) -> str:
    rows = [extract_row(d) for d in issues]
    rows.sort(key=lambda r: int(r["issue_id"]) if r["issue_id"].isdigit() else 0)

    title = f"Bug Status Report - {repo}"
    body = _render_body(rows, repo, conda_env, pytorch_folder)
    return _render_page(body, title)


def _render_body(rows: list[dict], repo: str, conda_env: str, pytorch_folder: str) -> str:
    parts = []

    parts.append('<h1>Bug Status Report</h1>')
    parts.append(f'<p class="meta">Repository: <strong>{html.escape(repo)}</strong> '
                 f'| Issues: <strong>{len(rows)}</strong>')
    if conda_env:
        parts.append(f' | Env: <code>{html.escape(conda_env)}</code>')
    if pytorch_folder:
        parts.append(f' | PyTorch: <code>{html.escape(pytorch_folder)}</code>')
    parts.append('</p>')

    parts.append('<h2>Summary</h2>')
    parts.append(_render_summary(rows))

    parts.append('<h2>Issues</h2>')
    parts.append(_render_table(rows))

    return "\n".join(parts)


def _render_summary(rows: list[dict]) -> str:
    need_actions = {}
    priorities = {}
    categories = {}
    repro_stats = {}
    for r in rows:
        na = r["need_action"] or "(none)"
        need_actions[na] = need_actions.get(na, 0) + 1
        p = r["priority"] or "(none)"
        priorities[p] = priorities.get(p, 0) + 1
        c = r["category"] or "(none)"
        categories[c] = categories.get(c, 0) + 1
        rs = r["repro_status"] or "(none)"
        repro_stats[rs] = repro_stats.get(rs, 0) + 1

    parts = ['<div class="summary-grid">']
    parts.append(_render_stat_box("By Need Action", need_actions))
    parts.append(_render_stat_box("By Priority", priorities))
    parts.append(_render_stat_box("By Reproduce", repro_stats))
    parts.append('</div>')

    parts.append(_render_trend_chart(categories, priorities))

    return "\n".join(parts)


def _render_stat_box(title: str, stats: dict) -> str:
    items = "".join(
        f'<li><strong>{html.escape(str(k))}</strong>: {v}</li>'
        for k, v in sorted(stats.items())
    )
    return f'<div class="stat-box"><h4>{html.escape(title)}</h4><ul>{items}</ul></div>'


def _render_trend_chart(categories: dict, priorities: dict) -> str:
    if not categories:
        return ""

    sorted_cats = sorted(categories.items(), key=lambda x: -x[1])
    max_val = max(categories.values()) if categories else 1
    total = sum(categories.values())

    bars = []
    for i, (cat, count) in enumerate(sorted_cats):
        pct = count / total * 100
        bar_width = count / max_val * 100
        y = i * 28
        color = _category_color(cat)
        bars.append(
            f'<g transform="translate(0,{y})">'
            f'<rect x="160" y="2" width="{bar_width * 2.8:.0f}" height="20" '
            f'fill="{color}" rx="3"/>'
            f'<text x="155" y="16" text-anchor="end" font-size="11" fill="#333">'
            f'{html.escape(cat)}</text>'
            f'<text x="{160 + bar_width * 2.8 + 6:.0f}" y="16" font-size="11" fill="#666">'
            f'{count} ({pct:.0f}%)</text>'
            f'</g>'
        )

    chart_height = len(sorted_cats) * 28 + 10
    svg = (
        f'<svg class="trend-chart" width="100%" height="{chart_height}" '
        f'viewBox="0 0 560 {chart_height}">\n'
        + "\n".join(bars)
        + '\n</svg>'
    )
    return f'<div class="chart-box"><h4>Category Distribution</h4>{svg}</div>'


CATEGORY_COLORS = {
    "Distributed": "#4e79a7",
    "Flash Attention": "#f28e2b",
    "Inductor": "#e15759",
    "TorchAO": "#76b7b2",
    "Sparse": "#59a14f",
    "Torch Ops - gemm": "#edc948",
    "Torch Ops - eltwise": "#b07aa1",
    "Torch Ops - reduction": "#ff9da7",
    "Torch Ops - others": "#9c755f",
    "Torch Runtime": "#bab0ac",
    "Others": "#d4d4d4",
}


def _category_color(cat: str) -> str:
    return CATEGORY_COLORS.get(cat, "#999")


def _render_table(rows: list[dict]) -> str:
    headers = [
        "Done", "Issue", "Title", "Type", "Test Type",
        "Platform Specific", "OS", "PyTorch CI",
        "Priority", "Category", "Root Cause",
        "Reproduce", "Need Action", "Target Component",
        "Other Opens", "Label Refresh", "Update"
    ]

    thead = ['<thead><tr>']
    for h in headers:
        cls = ' class="done-col"' if h == "Done" else ""
        thead.append(f'<th{cls}>{html.escape(h)}</th>')
    thead.append('</tr></thead>')

    tbody = ['<tbody>']
    for row in rows:
        data_attrs = (
            f'data-issue="{html.escape(row["issue_id"], quote=True)}" '
            f'data-priority="{html.escape(row["priority"], quote=True)}" '
            f'data-category="{html.escape(row["category"], quote=True)}" '
            f'data-needaction="{html.escape(row["need_action"], quote=True)}" '
            f'data-repro="{html.escape(row["repro_status"], quote=True)}" '
            f'data-dependency="{html.escape(row["dependency"], quote=True)}" '
            f'data-opens="{html.escape(row["opens_ar"], quote=True)}" '
            f'data-platform="{html.escape(row["platform_specific"], quote=True)}" '
            f'data-pytorchci="{html.escape(row["pytorch_ci"], quote=True)}" '
            f'data-type="{html.escape(row["issue_type"], quote=True)}" '
            f'data-testtype="{html.escape(row["test_type"], quote=True)}" '
            f'data-search="{html.escape((row["title"] + " " + row["root_cause"]).lower(), quote=True)}"'
        )
        tbody.append(f'<tr {data_attrs}>')

        cb_id = f'done-{row["issue_id"]}'
        tbody.append(
            f'<td class="done-col"><input type="checkbox" class="ar-done" '
            f'data-issue="{row["issue_id"]}" id="{cb_id}"></td>'
        )

        if row["url"]:
            tbody.append(
                f'<td><a href="{html.escape(row["url"], quote=True)}" '
                f'target="_blank">#{html.escape(row["issue_id"])}</a></td>'
            )
        else:
            tbody.append(f'<td>#{html.escape(row["issue_id"])}</td>')

        title_short = _truncate(row["title"], 80)
        tbody.append(
            f'<td class="tip-cell" data-tip="{html.escape(row["title"], quote=True)}">'
            f'{html.escape(title_short)}</td>'
        )

        tbody.append(f'<td>{html.escape(row["issue_type"])}</td>')

        tbody.append(f'<td>{html.escape(row["test_type"])}</td>')

        tbody.append(f'<td>{html.escape(row["platform_specific"])}</td>')

        tbody.append(f'<td>{html.escape(row["os"])}</td>')

        ci_cls = "ci-yes" if row["pytorch_ci"] else ""
        tbody.append(f'<td class="{ci_cls}">{html.escape(row["pytorch_ci"])}</td>')

        prio_cls = f'prio-{row["priority"].lower()}' if row["priority"] else ""
        tbody.append(f'<td class="{prio_cls}">{html.escape(row["priority"])}</td>')

        tbody.append(f'<td>{html.escape(row["category"])}</td>')

        rc_short = _truncate(row["root_cause"], 100)
        tbody.append(
            f'<td class="tip-cell" data-tip="{html.escape(row["root_cause"], quote=True)}">'
            f'{html.escape(rc_short)}</td>'
        )

        repro_cls = _repro_class(row["repro_status"])
        tbody.append(
            f'<td class="tip-cell {repro_cls}" '
            f'data-tip="{html.escape(row["repro_tooltip"], quote=True)}">'
            f'{html.escape(row["repro_status"])}</td>'
        )

        na_cls = _need_action_class(row["need_action"])
        tbody.append(
            f'<td class="tip-cell {na_cls}" '
            f'data-tip="{html.escape(row["need_action_tooltip"], quote=True)}">'
            f'{html.escape(row["need_action"])}</td>'
        )

        tbody.append(f'<td>{html.escape(row["target_component"])}</td>')

        opens_short = _truncate(row["opens_ar"], 30)
        tbody.append(
            f'<td class="tip-cell" data-tip="{html.escape(row["opens_reason"], quote=True)}">'
            f'{html.escape(opens_short)}</td>'
        )

        tbody.append(
            f'<td class="tip-cell" data-tip="{html.escape(row["label_tooltip"], quote=True)}">'
            f'{html.escape(row["label_display"])}</td>'
        )

        if row["gh_commands"]:
            cmd_escaped = html.escape(row["gh_commands"], quote=True)
            tbody.append(
                f'<td><button class="update-btn" '
                f'data-cmd="{cmd_escaped}">Apply</button></td>'
            )
        else:
            tbody.append('<td></td>')

        tbody.append('</tr>')
    tbody.append('</tbody>')

    return (
        '<table class="ar-table">\n'
        + "\n".join(thead) + "\n"
        + "\n".join(tbody) + "\n"
        + "</table>"
    )


def _truncate(s: str, n: int) -> str:
    if not s:
        return ""
    if len(s) <= n:
        return s
    return s[:n - 1] + "\u2026"


def _repro_class(status: str) -> str:
    return {
        "Reproduced": "repro-yes",
        "Not Reproduced": "repro-no",
        "Cannot Verify": "repro-unknown",
        "N/A": "repro-na",
    }.get(status, "")


def _need_action_class(na: str) -> str:
    if not na:
        return ""
    lower = na.lower()
    if "fix required" in lower or "need_fix" in lower:
        return "verdict-fix"
    if "3rdparty" in lower or "blocked" in lower or "third" in lower:
        return "verdict-3rd"
    if "human" in lower:
        return "verdict-human"
    if "no_need" in lower or "n/a" in lower:
        return "verdict-ok"
    return ""


# ---- CSS -----------------------------------------------------------------

CSS = """
:root {
  --bg: #f8f9fa; --fg: #212529; --muted: #6c757d;
  --border: #dee2e6; --accent: #0066cc; --done-bg: #e9ecef; --done-fg: #adb5bd;
  --filter-bg: #ffffff; --hl: #fff3cd;
}
* { box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  font-size: 13px; line-height: 1.5; color: var(--fg); background: var(--bg);
  margin: 0; padding: 0; }
h1, h2, h3, h4 { line-height: 1.2; margin-top: 1.5em; }
h1 { font-size: 1.8em; } h2 { font-size: 1.4em; border-bottom: 2px solid var(--border); padding-bottom: .3em; }
h4 { font-size: 1em; color: var(--muted); margin: .5em 0 .3em; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
code { background: #f1f3f5; padding: 1px 4px; border-radius: 3px; font-size: .9em; }
.meta { color: var(--muted); font-size: .9em; margin: .3em 0; }
.content { max-width: 100%; padding: 1em 1.5em 4em; }

.summary-grid { display: flex; gap: 1em; flex-wrap: wrap; margin: .5em 0 1em; }
.stat-box { background: white; border: 1px solid var(--border); border-radius: 6px;
  padding: .6em 1em; min-width: 180px; flex: 1; }
.stat-box h4 { margin: 0 0 .3em; font-size: .95em; }
.stat-box ul { margin: 0; padding-left: 1.2em; font-size: .9em; }
.stat-box li { margin: 2px 0; }
.chart-box { background: white; border: 1px solid var(--border); border-radius: 6px;
  padding: .8em 1em; margin: .5em 0 1.5em; }
.chart-box h4 { margin: 0 0 .5em; font-size: .95em; }

.filter-bar {
  position: sticky; top: 0; z-index: 100;
  background: var(--filter-bg); border-bottom: 1px solid var(--border);
  padding: .6em 1em; display: flex; flex-wrap: wrap; gap: .8em; align-items: center;
  box-shadow: 0 1px 3px rgba(0,0,0,.06);
}
.filter-bar > label { font-size: 12px; color: var(--muted); display: flex; flex-direction: column; gap: 2px; }
.filter-bar input[type=text] {
  font-size: 13px; padding: 3px 6px; border: 1px solid var(--border); border-radius: 3px;
  background: white; min-width: 200px;
}
.filter-bar button {
  font-size: 12px; padding: 4px 10px; border: 1px solid var(--border); border-radius: 3px;
  background: white; cursor: pointer;
}
.filter-bar button:hover { background: #f1f3f5; }
.filter-bar .stats { margin-left: auto; color: var(--muted); font-size: 12px; }

table.ar-table { border-collapse: collapse; width: 100%; margin: .5em 0 1em; font-size: 12px; }
table.ar-table th, table.ar-table td { border: 1px solid var(--border); padding: 4px 6px; vertical-align: top; text-align: left; }
table.ar-table thead th { background: #e7eaf0; position: sticky; top: 56px; z-index: 50; white-space: nowrap; }
table.ar-table td.done-col, table.ar-table th.done-col { width: 40px; text-align: center; }
table.ar-table tr:nth-child(even) td { background: #fafbfc; }
table.ar-table tr.done td { background: var(--done-bg) !important; color: var(--done-fg); }
table.ar-table tr.done td a { color: var(--done-fg); }
table.ar-table tr.done td:not(.done-col) { text-decoration: line-through; }
table.ar-table tr.hidden { display: none; }

.tip-cell { cursor: help; }

.tip-popup {
  display: none; position: fixed; z-index: 9999; max-width: 600px;
  background: linear-gradient(180deg, #2b2f3a 0%, #1f2330 100%);
  color: #f5f7fa; padding: 10px 14px; border-radius: 6px;
  font-size: 12.5px; line-height: 1.5; white-space: pre-wrap; word-wrap: break-word;
  pointer-events: none; box-shadow: 0 6px 20px rgba(0,0,0,.35);
  border: 1px solid rgba(255,255,255,.08);
  opacity: 0; transform: translateY(-2px);
  transition: opacity .12s ease-out, transform .12s ease-out;
}
.tip-popup.visible { display: block; opacity: 1; transform: translateY(0); }

.status-open { color: #d63384; font-weight: 600; }
.status-closed { color: #198754; }
.ci-yes { color: #dc3545; font-weight: 600; }
.prio-p0 { background: #f8d7da !important; font-weight: 700; }
.prio-p1 { background: #fff3cd !important; font-weight: 600; }
.prio-p2 { background: #e2e3f1 !important; }
.prio-p3 { color: var(--muted); }
.repro-yes { color: #dc3545; font-weight: 600; }
.repro-no { color: #198754; }
.repro-unknown { color: #fd7e14; }
.repro-na { color: var(--muted); }
.verdict-fix { color: #dc3545; font-weight: 600; }
.verdict-3rd { color: #6f42c1; }
.verdict-human { color: #d63384; }
.verdict-ok { color: #198754; }

.update-btn {
  font-size: 11px; padding: 2px 8px; border: 1px solid var(--accent);
  border-radius: 3px; background: white; color: var(--accent);
  cursor: pointer; white-space: nowrap;
}
.update-btn:hover { background: var(--accent); color: white; }
.update-btn.copied { background: #198754; border-color: #198754; color: white; }

.ms-dd { position: relative; display: inline-block; }
.ms-dd-btn {
  font-size: 13px; padding: 3px 24px 3px 6px; border: 1px solid var(--border);
  border-radius: 3px; background: white; cursor: pointer; min-width: 130px;
  text-align: left; position: relative; white-space: nowrap; overflow: hidden;
  text-overflow: ellipsis; max-width: 200px;
}
.ms-dd-btn::after {
  content: "\\25BE"; position: absolute; right: 6px; top: 50%;
  transform: translateY(-50%); color: var(--muted); font-size: 10px;
}
.ms-dd-panel {
  display: none; position: absolute; top: calc(100% + 2px); left: 0;
  background: white; border: 1px solid var(--border); border-radius: 3px;
  box-shadow: 0 2px 8px rgba(0,0,0,.12); z-index: 200;
  max-height: 320px; overflow-y: auto; min-width: 200px; padding: 4px 0;
}
.ms-dd.open .ms-dd-panel { display: block; }
.ms-dd-search {
  width: calc(100% - 12px); margin: 4px 6px; padding: 3px 5px;
  border: 1px solid var(--border); border-radius: 3px; font-size: 12px;
}
.ms-dd-item {
  display: flex; align-items: center; gap: 4px;
  padding: 3px 10px; cursor: pointer; font-size: 12px; user-select: none;
}
.ms-dd-item:hover { background: #f1f3f5; }
.ms-dd-item input[type=checkbox] { margin: 0; width: 14px; height: 14px; accent-color: var(--accent); }
.ms-dd-item .label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 180px; }
.ms-dd-item .count { color: var(--muted); font-size: 11px; background: #f1f3f5; padding: 0 6px; border-radius: 8px; margin-left: auto; }
.ms-dd-actions {
  display: flex; gap: 4px; padding: 4px 6px; border-top: 1px solid var(--border);
  margin-top: 2px; position: sticky; bottom: 0; background: white;
}
.ms-dd-actions button {
  flex: 1; font-size: 11px; padding: 2px 4px; border: 1px solid var(--border);
  border-radius: 3px; background: white; cursor: pointer;
}
.ms-dd-actions button:hover { background: #f1f3f5; }
"""

# ---- JS -----------------------------------------------------------------

JS = """
const STORAGE_PREFIX = 'bug_status_done_';

function loadDoneState() {
  document.querySelectorAll('input.ar-done').forEach(cb => {
    const id = cb.dataset.issue;
    if (!id) return;
    if (localStorage.getItem(STORAGE_PREFIX + id) === '1') {
      cb.checked = true;
      cb.closest('tr').classList.add('done');
    }
  });
}

function onDoneToggle(e) {
  const cb = e.target;
  const id = cb.dataset.issue;
  if (!id) return;
  const tr = cb.closest('tr');
  if (cb.checked) {
    localStorage.setItem(STORAGE_PREFIX + id, '1');
    tr.classList.add('done');
  } else {
    localStorage.removeItem(STORAGE_PREFIX + id);
    tr.classList.remove('done');
  }
}

function initTooltips() {
  const popup = document.createElement('div');
  popup.className = 'tip-popup';
  document.body.appendChild(popup);

  document.addEventListener('mouseover', e => {
    const cell = e.target.closest('.tip-cell[data-tip]');
    if (!cell) { popup.classList.remove('visible'); return; }
    const tip = cell.dataset.tip;
    if (!tip) { popup.classList.remove('visible'); return; }
    popup.textContent = tip;
    popup.classList.add('visible');
    const rect = cell.getBoundingClientRect();
    let left = rect.left;
    let top = rect.bottom + 4;
    if (left + 620 > window.innerWidth) left = window.innerWidth - 630;
    if (top + 200 > window.innerHeight) top = rect.top - popup.offsetHeight - 4;
    popup.style.left = Math.max(0, left) + 'px';
    popup.style.top = top + 'px';
  });
  document.addEventListener('mouseout', e => {
    if (e.target.closest('.tip-cell')) popup.classList.remove('visible');
  });
}

function initUpdateButtons() {
  document.querySelectorAll('.update-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const cmd = btn.dataset.cmd;
      if (!cmd) return;
      navigator.clipboard.writeText(cmd).then(() => {
        btn.textContent = 'Copied!';
        btn.classList.add('copied');
        setTimeout(() => { btn.textContent = 'Apply'; btn.classList.remove('copied'); }, 2000);
      }, () => {
        prompt('Copy this command:', cmd);
      });
    });
  });
}

const FILTER_DIMS = ['type', 'testtype', 'priority', 'category', 'needaction', 'repro', 'dependency', 'platform', 'pytorchci', 'opens'];
const FILTER_LABELS = {
  type: 'Type', testtype: 'Test Type',
  priority: 'Priority', category: 'Category', needaction: 'Need Action',
  repro: 'Reproduce', dependency: 'Dependency', platform: 'Platform',
  pytorchci: 'PyTorch CI', opens: 'Opens'
};
const NONE_TOKEN = '(none)';
const SELECTED = Object.fromEntries(FILTER_DIMS.map(d => [d, new Set()]));

function tokensFor(dim, raw) {
  const r = (raw || '').trim();
  return r ? [r] : [NONE_TOKEN];
}

function collectValues(dim) {
  const counts = new Map();
  document.querySelectorAll('table.ar-table tbody tr[data-issue]').forEach(tr => {
    const val = tr.dataset[dim] || '';
    const tokens = tokensFor(dim, val);
    for (const tok of tokens) counts.set(tok, (counts.get(tok) || 0) + 1);
  });
  return Array.from(counts.entries()).sort((a, b) => {
    if (a[0] === NONE_TOKEN) return 1;
    if (b[0] === NONE_TOKEN) return -1;
    return a[0].localeCompare(b[0], undefined, {numeric: true});
  });
}

function buildMultiSelect(dim, items) {
  const dd = document.createElement('div');
  dd.className = 'ms-dd'; dd.dataset.dim = dim;
  dd.dataset.totalOptions = String(items.length);

  const btn = document.createElement('button');
  btn.type = 'button'; btn.className = 'ms-dd-btn'; btn.textContent = '(all)';
  dd.appendChild(btn);

  const panel = document.createElement('div');
  panel.className = 'ms-dd-panel';
  const search = document.createElement('input');
  search.type = 'text'; search.className = 'ms-dd-search'; search.placeholder = 'filter...';
  panel.appendChild(search);
  const list = document.createElement('div');
  list.className = 'ms-dd-list';
  panel.appendChild(list);

  for (const [tok, count] of items) {
    const label = document.createElement('label');
    label.className = 'ms-dd-item';
    const cb = document.createElement('input');
    cb.type = 'checkbox'; cb.value = tok; cb.checked = true;
    SELECTED[dim].add(tok);
    cb.addEventListener('change', () => {
      if (cb.checked) SELECTED[dim].add(tok); else SELECTED[dim].delete(tok);
      updateButtonLabel(dd, dim); applyFilters();
    });
    const txt = document.createElement('span');
    txt.className = 'label'; txt.textContent = tok; txt.title = tok;
    const cnt = document.createElement('span');
    cnt.className = 'count'; cnt.textContent = count;
    label.appendChild(cb); label.appendChild(txt); label.appendChild(cnt);
    list.appendChild(label);
  }

  search.addEventListener('input', () => {
    const q = search.value.trim().toLowerCase();
    list.querySelectorAll('.ms-dd-item').forEach(it => {
      it.style.display = (!q || it.querySelector('.label').textContent.toLowerCase().includes(q)) ? '' : 'none';
    });
  });

  const actions = document.createElement('div');
  actions.className = 'ms-dd-actions';
  const allBtn = document.createElement('button'); allBtn.type = 'button'; allBtn.textContent = 'All';
  allBtn.addEventListener('click', () => {
    for (const [tok] of items) SELECTED[dim].add(tok);
    list.querySelectorAll('input[type=checkbox]').forEach(cb => cb.checked = true);
    updateButtonLabel(dd, dim); applyFilters();
  });
  const noneBtn = document.createElement('button'); noneBtn.type = 'button'; noneBtn.textContent = 'None';
  noneBtn.addEventListener('click', () => {
    SELECTED[dim].clear();
    list.querySelectorAll('input[type=checkbox]').forEach(cb => cb.checked = false);
    updateButtonLabel(dd, dim); applyFilters();
  });
  const closeBtn = document.createElement('button'); closeBtn.type = 'button'; closeBtn.textContent = 'Close';
  closeBtn.addEventListener('click', () => dd.classList.remove('open'));
  actions.appendChild(allBtn); actions.appendChild(noneBtn); actions.appendChild(closeBtn);
  panel.appendChild(actions);
  dd.appendChild(panel);

  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    const wasOpen = dd.classList.contains('open');
    document.querySelectorAll('.ms-dd.open').forEach(d => d.classList.remove('open'));
    if (!wasOpen) dd.classList.add('open');
  });
  panel.addEventListener('click', (e) => e.stopPropagation());
  updateButtonLabel(dd, dim);
  return dd;
}

function updateButtonLabel(dd, dim) {
  const btn = dd.querySelector('.ms-dd-btn');
  const sel = SELECTED[dim];
  const total = parseInt(dd.dataset.totalOptions || '0', 10);
  if (sel.size === 0) btn.textContent = '(none)';
  else if (sel.size === total) btn.textContent = '(all)';
  else if (sel.size === 1) btn.textContent = Array.from(sel)[0];
  else btn.textContent = sel.size + ' of ' + total;
}

function buildFilterBar() {
  const bar = document.createElement('div');
  bar.className = 'filter-bar';

  for (const dim of FILTER_DIMS) {
    const wrap = document.createElement('label');
    wrap.textContent = FILTER_LABELS[dim];
    const dd = buildMultiSelect(dim, collectValues(dim));
    dd.id = 'filter-' + dim;
    wrap.appendChild(dd);
    bar.appendChild(wrap);
  }

  const searchWrap = document.createElement('label');
  searchWrap.textContent = 'Search';
  const search = document.createElement('input');
  search.type = 'text'; search.id = 'filter-search';
  search.placeholder = 'title / root cause';
  search.addEventListener('input', applyFilters);
  searchWrap.appendChild(search);
  bar.appendChild(searchWrap);

  const hideWrap = document.createElement('label');
  hideWrap.style.flexDirection = 'row'; hideWrap.style.alignItems = 'center'; hideWrap.style.gap = '4px';
  const hideCb = document.createElement('input');
  hideCb.type = 'checkbox'; hideCb.id = 'filter-hide-done';
  hideCb.addEventListener('change', applyFilters);
  hideWrap.appendChild(hideCb);
  hideWrap.appendChild(document.createTextNode('Hide Done'));
  bar.appendChild(hideWrap);

  const reset = document.createElement('button');
  reset.textContent = 'Reset';
  reset.addEventListener('click', () => {
    for (const dim of FILTER_DIMS) {
      const dd = document.getElementById('filter-' + dim);
      SELECTED[dim].clear();
      dd.querySelectorAll('input[type=checkbox]').forEach(cb => { cb.checked = true; SELECTED[dim].add(cb.value); });
      updateButtonLabel(dd, dim);
    }
    document.getElementById('filter-search').value = '';
    document.getElementById('filter-hide-done').checked = false;
    applyFilters();
  });
  bar.appendChild(reset);

  const exp = document.createElement('button');
  exp.textContent = 'Export Done';
  exp.addEventListener('click', () => {
    const ids = [];
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (k.startsWith(STORAGE_PREFIX) && localStorage.getItem(k) === '1')
        ids.push(k.slice(STORAGE_PREFIX.length));
    }
    ids.sort((a, b) => Number(a) - Number(b));
    navigator.clipboard.writeText(ids.join(',')).then(
      () => alert('Copied ' + ids.length + ' done IDs'),
      () => prompt('Done IDs:', ids.join(','))
    );
  });
  bar.appendChild(exp);

  const stats = document.createElement('span');
  stats.className = 'stats'; stats.id = 'filter-stats';
  bar.appendChild(stats);

  document.body.insertBefore(bar, document.body.firstChild);
  document.addEventListener('click', () => {
    document.querySelectorAll('.ms-dd.open').forEach(d => d.classList.remove('open'));
  });
}

function applyFilters() {
  const search = document.getElementById('filter-search').value.trim().toLowerCase();
  const hideDone = document.getElementById('filter-hide-done').checked;
  let total = 0, visible = 0;

  document.querySelectorAll('table.ar-table tbody tr').forEach(tr => {
    total++;
    let show = true;
    for (const dim of FILTER_DIMS) {
      const sel = SELECTED[dim];
      if (sel.size === 0) { show = false; break; }
      const val = tr.dataset[dim] || '';
      const tokens = tokensFor(dim, val);
      let match = false;
      for (const t of tokens) { if (sel.has(t)) { match = true; break; } }
      if (!match) { show = false; break; }
    }
    if (show && search) show = (tr.dataset.search || '').includes(search);
    if (show && hideDone && tr.classList.contains('done')) show = false;
    tr.classList.toggle('hidden', !show);
    if (show) visible++;
  });
  document.getElementById('filter-stats').textContent = visible + ' / ' + total + ' rows';
}

document.addEventListener('DOMContentLoaded', () => {
  loadDoneState();
  document.querySelectorAll('input.ar-done').forEach(cb => cb.addEventListener('change', onDoneToggle));
  buildFilterBar();
  applyFilters();
  initTooltips();
  initUpdateButtons();
});
"""


# ---- page assembly -------------------------------------------------------

def _render_page(body_html: str, title: str) -> str:
    return (
        '<!doctype html>\n'
        '<html lang="en">\n<head>\n'
        '<meta charset="utf-8">\n'
        f'<title>{html.escape(title)}</title>\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'<style>{CSS}</style>\n'
        '</head>\n<body>\n'
        f'<div class="content">\n{body_html}\n</div>\n'
        f'<script>{JS}</script>\n'
        '</body>\n</html>\n'
    )


# ---- main ----------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(
        description="Generate bug_status_report.html from issue-triage results."
    )
    p.add_argument("--repo", required=True,
                   help="Repository slug (e.g. intel/torch-xpu-ops).")
    p.add_argument("--conda-env", default="",
                   help="Conda environment name (informational).")
    p.add_argument("--pytorch-folder", default="",
                   help="PyTorch source folder path (informational).")
    p.add_argument("--out", type=Path, default=None,
                   help="Output HTML path.")
    p.add_argument("--agent-space", type=Path, default=None,
                   help="Path to agent_space/issue_triage_orchestrator/.")
    args = p.parse_args()

    agent_space = args.agent_space or AGENT_SPACE
    out_path = args.out or DEFAULT_OUT

    if not agent_space.exists():
        print(f"ERROR: agent_space not found at {agent_space}", file=sys.stderr)
        return 1

    issues = load_issue_data(agent_space, args.repo)
    if not issues:
        print(f"ERROR: No triage results found for repo '{args.repo}' "
              f"in {agent_space}", file=sys.stderr)
        print(f"  Expected folder pattern: {repo_to_folder_prefix(args.repo)}<id>/final_output.json",
              file=sys.stderr)
        return 2

    print(f"Found {len(issues)} triaged issues for {args.repo}")

    page = generate_html(issues, args.repo, args.conda_env, args.pytorch_folder)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(page, encoding="utf-8")
    print(f"Wrote {out_path} ({out_path.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
