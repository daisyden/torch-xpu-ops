---
name: bug-status-report
description: Generate an interactive HTML report from issue-triage orchestrator results. Reads all final_output.json files under agent_space/issue_triage_orchestrator/ folders, aggregates triage verdicts, reproduce results, and label actions into a single self-contained HTML dashboard with filters, tooltips, and update buttons. Use when asked to generate a triage status report, view issue triage results, or produce a bug status dashboard from previously triaged issues.
---

# Bug Status Report Skill

## Overview

Generates a self-contained interactive HTML report (`bug_status_report.html`)
from the issue-triage orchestrator output files stored in
`agent_space/issue_triage_orchestrator/`. Each issue folder contains JSON
artifacts from the triage pipeline (extract, reproduce, triage, update_label).

The report follows the same visual design and interactivity as
`bug_scrub_highlight.html` (from the generate_html_report skill), with
adaptations for issue-triage data:

- **Root Cause** column (replaces "Fix Approach") showing the triage root cause
- **Reproduce** column with hover tooltip showing full reproduce details
- **Add Label** column showing pending label actions from `update_label_result.json`
- **Update** column with a button to apply labels/status via `gh` CLI

## When to Use

Run after `issue-triage` or `batch-issue-triage` has completed for one or more
issues. The script scans `agent_space/issue_triage_orchestrator/` for folders
matching `<repo_slug>_issue_<id>` and reads their `final_output.json`.

## Inputs

| Parameter | Required | Description |
|---|---|---|
| `--repo` | Yes | Repository slug filter (e.g. `intel/torch-xpu-ops`). Only folders matching this repo are included. |
| `--conda-env` | No | Conda environment name (informational, shown in report header) |
| `--pytorch-folder` | No | PyTorch source folder path (informational) |
| `--out` | No | Output HTML path (default: `agent_space/issue_triage_orchestrator/bug_status_report.html`) |

## Outputs

| Path | Description |
|---|---|
| `agent_space/issue_triage_orchestrator/bug_status_report.html` | Self-contained HTML report |

## Scripts

| Script | Purpose |
|---|---|
| [`gen_bug_status_report.py`](./gen_bug_status_report.py) | Scans issue folders, aggregates JSON data, emits the HTML report with filters, tooltips, Done checkboxes, and Update buttons. |

## Execution

```bash
python3 .opencode/skills/validation/issue-triage/bug_status_report/gen_bug_status_report.py \
    --repo intel/torch-xpu-ops
```

## Features

### Table Columns

| Column | Source |
|---|---|
| Issue | `final_output.json` → `issue.issue_id` + `issue.url` |
| Title | `final_output.json` → `issue.title` |
| Status | `final_output.json` → `extract_result.status` (OPEN/CLOSED) |
| Priority | `triage_result.priority.priority` |
| Category | `triage_result.category.category` + subcategory |
| Root Cause | `triage_result.root_cause` or `triage_result.target_component.root_cause` |
| Reproduce | Compact status; full details on hover from `reproduce_result` |
| Verdict | `triage_result.target_component.verdict` |
| Add Label | Pending label actions from `update_label_result.json` |
| Update | Button to apply labels via gh CLI command |

### Filter Bar (sticky)

Multi-select dropdowns for: Priority, Category, Verdict, Reproduce Status.
Free-text search across Title + Root Cause. Hide Done toggle.

### Done Checkbox

Per-row checkbox persisted in browser localStorage. Same behavior as
bug_scrub_highlight.html.

### Update Button

Generates and displays the `gh` CLI commands needed to apply pending labels.
Clicking copies the command to clipboard.

### Self-contained

CSS and JS inlined. No CDN, no external assets. Works offline.
