# Agent Knowledge Guide

> This document consolidates knowledge about OpenCode, Oh-My-OpenAgent, Agentic AI, and how to write reproducible skills.

---

## Table of Contents

1. [Oh My OpenAgent (OmO)](#oh-my-openagent-omo)
2. [OpenCode - Detailed Introduction](#opencode---detailed-introduction)
3. [What is Agentic AI](#what-is-agentic-ai)
4. [How to Write Skills That Reproduce Agent Work](#how-to-write-skills-that-reproduce-agent-work)

---

## Oh My OpenAgent (OmO)

**Oh My OpenAgent** is an **agent harness/orchestration plugin** for OpenCode that provides multi-model AI agent coordination.

### Core Concept

It's a **plugin for OpenCode** (similar to how oh-my-zsh enhances zsh) that orchestrates multiple AI agents working together:

```
┌─────────────────────────────────────────────────────────────────┐
│                         ULTRAWORK                               │
│              (One command activates everything)                 │
├─────────────────────────────────────────────────────────────────┤
│                     DISCIPLINE AGENTS                           │
├──────────────┬──────────────┬──────────────┬───────────────────┤
│  Sisyphus    │  Prometheus  │  Hephaestus  │  Oracle/Librarian │
│  (Opus/K2.6) │  (Planner)   │  (GPT-5.5)   │  (Specialists)    │
│  Orchestrator│  Interview + │  Deep Worker │  Debug/Docs/Grep  │
│              │  Plan        │  Autonomous  │                   │
└──────────────┴──────────────┴──────────────┴───────────────────┘
```

### Key Mechanisms

| Component | How It Works |
|-----------|--------------|
| **Category Routing** | Agent says "I need `visual-engineering`" → harness picks GPT-5.5; says `ultrabrain` → routes to best logic model |
| **Hash-Anchored Edits** | Every line tagged with content hash (`11#VK\|`). Edit references hash → validates before applying |
| **Skill-Embedded MCPs** | Skills bring their own MCP servers, spin up on demand, context stays clean |
| **Background Agents** | Run 5+ specialists in parallel, merge results |
| **Team Mode (v4.0)** | Lead agent + 8 parallel members, real-time tmux visualization |

### Agent Workflow

```
User: "ultrawork" or "ulw"
        │
        ▼
┌───────────────────┐
│    Sisyphus       │ ──► Plans task breakdown
│   (Orchestrator)  │
└─────────┬─────────┘
          │ Delegates by CATEGORY (not model)
          ▼
    ┌─────┴─────┬─────────────┬────────────┐
    │           │             │            │
    ▼           ▼             ▼            ▼
 Prometheus  Hephaestus    Oracle     Librarian
 (Planning)  (Deep Work)  (Debug)    (Research)
    │           │             │            │
    └───────────┴─────────────┴────────────┘
                     │
                     ▼
           Results merged by Sisyphus
           Ralph Loop: repeat until 100% done
```

### Skills Architecture

```
.opencode/skills/*/SKILL.md    # Project-specific
~/.config/opencode/skills/*/   # User-wide

Each SKILL.md brings:
  • Domain-tuned system instructions
  • Embedded MCP servers (on-demand)
  • Scoped permissions
```

### Key Features

| Feature | Description |
|---------|-------------|
| 🤖 Discipline Agents | Sisyphus orchestrates Hephaestus, Oracle, Librarian, Explore |
| 👥 Team Mode (v4.0) | Lead agent + up to 8 parallel members, real-time tmux visualization |
| ⚡ ultrawork / ulw | One word. Every agent activates. Doesn't stop until done |
| 🚪 IntentGate | Analyzes true user intent before classifying or acting |
| 🔗 Hash-Anchored Edit Tool | LINE#ID content hash validates every change. Zero stale-line errors |
| 🛠️ LSP + AST-Grep | Workspace rename, pre-build diagnostics, AST-aware rewrites |
| 🧠 Background Agents | Fire 5+ specialists in parallel. Context stays lean |
| 📚 Built-in MCPs | Exa (web search), Context7 (official docs), Grep.app (GitHub search) |
| 🔁 Ralph Loop / /ulw-loop | Self-referential loop. Doesn't stop until 100% done |
| ✅ Todo Enforcer | Agent goes idle? System yanks it back |
| 🔌 Claude Code Compatible | Your hooks, commands, skills, MCPs, and plugins all work |

### Reference

- GitHub: https://github.com/code-yeongyu/oh-my-openagent
- Docs: https://omo.vibetip.help/docs

---

## OpenCode - Detailed Introduction

**OpenCode** is an **open-source AI coding agent** available as terminal TUI, desktop app, and IDE extension. Used by 7.5M+ developers monthly with 160K+ GitHub stars.

### Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           OPENCODE                                       │
│         Terminal TUI / Desktop App / IDE Extension                       │
├─────────────────────────────────────────────────────────────────────────┤
│                          AGENTS                                          │
│  ┌──────────────────────────────┐  ┌──────────────────────────────────┐ │
│  │     PRIMARY AGENTS            │  │       SUBAGENTS                   │ │
│  │  • Build (default, full tools)│  │  • General (multi-step tasks)    │ │
│  │  • Plan (read-only analysis)  │  │  • Explore (fast codebase grep)  │ │
│  │  • Custom agents...           │  │  • Scout (external docs research)│ │
│  └──────────────────────────────┘  └──────────────────────────────────┘ │
├─────────────────────────────────────────────────────────────────────────┤
│                          TOOLS                                           │
│  File: read, write, edit, apply_patch, glob, grep, list                 │
│  System: bash, webfetch, websearch, lsp                                  │
│  Task: task (subagent), skill, todowrite, question                       │
├─────────────────────────────────────────────────────────────────────────┤
│                       PROVIDERS (75+)                                    │
│  Claude | GPT | Gemini | Copilot | Bedrock | Groq | Local Models...     │
└─────────────────────────────────────────────────────────────────────────┘
```

### 1. Agents System

| Agent Type | Name | Purpose |
|------------|------|---------|
| **Primary** | `Build` | Default agent with all tools enabled for development |
| **Primary** | `Plan` | Read-only analysis, no file modifications (Tab to switch) |
| **Subagent** | `General` | Complex multi-step tasks, full tool access |
| **Subagent** | `Explore` | Fast read-only codebase exploration |
| **Subagent** | `Scout` | External docs and dependency research |
| **Hidden** | `Compaction/Title/Summary` | Auto-runs for context management |

**Invoking subagents:**
```
@explore find all authentication handlers in src/
@general help me refactor this module
```

**Custom agent configuration** (`.opencode/agents/review.md`):
```yaml
---
description: Reviews code for quality and best practices
mode: subagent
model: anthropic/claude-sonnet-4-20250514
temperature: 0.1
permission:
  edit: deny
  bash: deny
---

You are in code review mode. Focus on:
- Code quality and best practices
- Potential bugs and edge cases
```

### 2. Built-in Tools

| Tool | Function | Permission Key |
|------|----------|----------------|
| `bash` | Execute shell commands | `bash` |
| `read` | Read file contents | `read` |
| `write` | Create/overwrite files | `edit` |
| `edit` | Modify existing files (string replacement) | `edit` |
| `apply_patch` | Apply diffs to files | `edit` |
| `grep` | Search file contents with regex | `grep` |
| `glob` | Find files by pattern (`**/*.ts`) | `glob` |
| `webfetch` | Fetch web page content | `webfetch` |
| `websearch` | Search the web (Exa AI) | `websearch` |
| `lsp` | Language Server Protocol operations | `lsp` |
| `skill` | Load a SKILL.md instruction file | `skill` |
| `task` | Invoke subagents | `task` |
| `todowrite` | Track progress on multi-step tasks | `todowrite` |
| `question` | Ask user clarifying questions | `question` |

### 3. Skills System

Skills are **reusable domain-specific instructions** loaded on-demand:

**Location:**
```
.opencode/skills/<name>/SKILL.md     # Project-specific
~/.config/opencode/skills/<name>/    # Global
.claude/skills/<name>/SKILL.md       # Claude-compatible
```

**Example SKILL.md:**
```yaml
---
name: git-release
description: Create consistent releases and changelogs
---

## What I do
- Draft release notes from merged PRs
- Propose a version bump
- Provide a `gh release create` command

## When to use me
Use this when preparing a tagged release.
```

### 4. Key Features

| Feature | Description |
|---------|-------------|
| **Multi-session** | Run multiple agents in parallel on the same project |
| **LSP Integration** | Auto-loads language servers for code intelligence |
| **MCP Servers** | Model Context Protocol for external tool integration |
| **Auto Compact** | Automatically summarizes long conversations at 95% context |
| **Share Links** | `/share` creates shareable conversation links |
| **Permission System** | Fine-grained `allow/ask/deny` per tool |
| **GitHub/GitLab Integration** | Native git workflow support |
| **Custom Commands** | Project/user commands in `.opencode/commands/` |
| **Undo/Redo** | `/undo` and `/redo` for change management |

### 5. Permission System

```json
{
  "permission": {
    "edit": "ask",
    "bash": {
      "*": "ask",
      "git status *": "allow",
      "rm -rf *": "deny"
    },
    "mymcp_*": "deny"
  }
}
```

### 6. Workflow Modes

| Mode | Keybind | Purpose |
|------|---------|---------|
| **Build** | Default | Make code changes |
| **Plan** | `Tab` | Analysis/planning without modifications |

**Typical workflow:**
1. `/init` - Analyze project, create `AGENTS.md`
2. `Tab` → Plan mode → Describe feature
3. Review plan, iterate
4. `Tab` → Build mode → "Go ahead and make the changes"
5. `/undo` if needed

### 7. Configuration

```json
{
  "$schema": "https://opencode.ai/config.json",
  "providers": {
    "anthropic": { "apiKey": "sk-..." }
  },
  "agent": {
    "build": {
      "model": "anthropic/claude-sonnet-4-20250514",
      "permission": { "edit": "allow" }
    }
  },
  "mcpServers": {
    "github": { "type": "stdio", "command": "gh-mcp" }
  },
  "lsp": {
    "go": { "command": "gopls" },
    "typescript": { "command": "typescript-language-server", "args": ["--stdio"] }
  }
}
```

### Reference

- Website: https://opencode.ai/
- Docs: https://opencode.ai/docs
- GitHub: https://github.com/anomalyco/opencode

---

## What is Agentic AI

**Agentic AI** refers to AI systems that can **autonomously plan, decide, and act** to accomplish goals, rather than just responding to single prompts.

### Core Characteristics

| Characteristic | Description |
|----------------|-------------|
| **Goal-Oriented** | Given a high-level objective, figures out sub-tasks itself |
| **Autonomous Action** | Executes actions (file edits, commands, API calls) without step-by-step human instruction |
| **Tool Use** | Invokes external tools (bash, web search, APIs) to accomplish tasks |
| **Iterative Reasoning** | Plans → Acts → Observes results → Adjusts → Repeats until done |
| **Multi-Step Execution** | Handles complex workflows spanning many operations |

### Agentic AI vs Traditional AI

```
Traditional LLM (Chat):
  User: "How do I fix this bug?"
  AI: "You should modify line 42 to add null checking..."
  User: [manually edits file]
  User: "Now what?"
  AI: "Next, run the tests..."

Agentic AI:
  User: "Fix the null pointer bug in auth.py"
  AI: [reads auth.py]
      [identifies the bug at line 42]
      [edits the file to add null check]
      [runs pytest]
      [sees test failure]
      [adjusts fix]
      [runs pytest again]
      [tests pass]
      "Done. Fixed null check at line 42, all tests passing."
```

### The Agent Loop

```
┌─────────────────────────────────────────────────┐
│                 USER GOAL                        │
│        "Add authentication to /settings"         │
└─────────────────────┬───────────────────────────┘
                      │
                      ▼
         ┌────────────────────────┐
         │      PLAN              │ ◄─────────────┐
         │  Break down into steps │               │
         └───────────┬────────────┘               │
                     ▼                            │
         ┌────────────────────────┐               │
         │      ACT               │               │
         │  Execute tool calls    │               │
         │  (read, edit, bash...) │               │
         └───────────┬────────────┘               │
                     ▼                            │
         ┌────────────────────────┐               │
         │      OBSERVE           │               │
         │  Check results/errors  │               │
         └───────────┬────────────┘               │
                     ▼                            │
         ┌────────────────────────┐    Not Done   │
         │      REFLECT           │ ──────────────┘
         │  Goal achieved?        │
         └───────────┬────────────┘
                     │ Done
                     ▼
         ┌────────────────────────┐
         │      REPORT            │
         │  Summarize results     │
         └────────────────────────┘
```

### Examples of Agentic AI

| System | How It's Agentic |
|--------|------------------|
| **OpenCode** | Plans, reads code, edits files, runs tests autonomously |
| **Claude Code** | Multi-step coding with bash, file operations |
| **Devin** | End-to-end software engineering tasks |
| **AutoGPT** | Goal decomposition and autonomous execution |
| **Bug Scrub Workflow** | 5-phase pipeline that analyzes, decides, acts, generates reports |

### Bug Scrub as Agentic Example

```
Goal: "Find Action Reason for issue #3727"
       │
       ▼
┌──────────────────────────────────────────┐
│ PLAN: Try Path 0 → Path 1 → Path 2       │
└──────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────┐
│ ACT: `gh pr list --search "3727"`        │  ← Tool use
│      Read PR body, check for "fixes #"    │
└──────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────┐
│ OBSERVE: Found PR #3729 with "fixes #"   │
└──────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────┐
│ ACT: `gh pr view 3729 --json state`      │  ← Another tool
└──────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────┐
│ REFLECT: PR merged? ✓ 4 gates pass?      │
│          → ACCEPT as Action Reason       │
└──────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────┐
│ OUTPUT: action_reason = "PR #3729"       │
└──────────────────────────────────────────┘
```

### Key Enablers of Agentic AI

1. **Tool Calling** - LLMs that can invoke functions/tools
2. **Reasoning Models** - Claude, GPT-4, etc. with planning capabilities
3. **Orchestration Frameworks** - LangGraph, AutoGen, OpenCode
4. **Memory/Context** - Session persistence, AGENTS.md, SKILL.md
5. **Feedback Loops** - Observe results, adjust strategy

**In summary**: Agentic AI = LLM + Tools + Autonomous Loop + Goal Persistence

---

## How to Write Skills That Reproduce Agent Work

A well-written skill acts as a **reproducible playbook** that any agent can follow to achieve consistent results.

### 1. Skill Structure Template

```markdown
---
name: my-workflow
description: One-line description of what this skill does (max 1024 chars)
---

## Purpose
What problem does this skill solve?

## Prerequisites
- Environment requirements
- Required tools/access
- Input data format

## Workflow Steps
### Step 1: [Action Name]
**Method**: Rule-based | LLM | Tool
**Input**: What the agent receives
**Action**: Exact steps to perform
**Output**: Expected result format
**Decision Gate**: When to proceed vs. fail

### Step 2: ...

## Decision Logic
Flowchart or rules for branching decisions

## Output Schema
Exact format of final output

## Examples
### Example 1: [Scenario Name]
Input: ...
Expected Output: ...

## Anti-Patterns
What NOT to do and why
```

### 2. Key Principles for Reproducibility

#### A. Be Explicit About Methods

```markdown
❌ Vague:
"Analyze the PR to see if it fixes the issue"

✅ Explicit:
**Method**: Script (regex) → LLM fallback
1. Parse PR body for pattern: `fixes #\d+`, `closes #\d+`, `resolves #\d+`
2. If found → extract issue number → VERIFIED
3. If not found → invoke Explore Agent for semantic analysis
```

#### B. Define Decision Gates

```markdown
### Gate: PR Verification
| Condition | Action |
|-----------|--------|
| `state == "MERGED"` AND `issue_ref matches` | → ACCEPT |
| `state == "OPEN"` AND `issue_ref matches` | → MONITOR |
| `state == "CLOSED"` (not merged) | → REJECT |
| No explicit reference found | → Continue to Path 2 |
```

#### C. Specify Tool Invocations

```markdown
### Step: Fetch PR State
**Tool**: bash
**Command**:
```bash
gh pr view ${PR_NUMBER} --repo intel/torch-xpu-ops --json state,mergedAt,body
```
**Parse Output**:
```json
{
  "state": "MERGED",
  "mergedAt": "2026-05-20T10:30:00Z",
  "body": "..."
}
```
**Next**: If `state == "MERGED"`, proceed to Gate 2
```

#### D. Include Fallback Paths

```markdown
### Path Priority
1. **Path 0 (Fastest)**: Check `action_TBD` field for existing PR reference
   - If found → verify PR state → done
2. **Path 1 (Script)**: Search PR body for explicit issue reference
   - `gh pr list --search "fixes #${ISSUE}" --json number,state`
3. **Path 2 (LLM Fallback)**: Semantic content-match analysis
   - Only invoke when Path 0 and Path 1 fail
   - Use Explore Agent with specific prompt
```

### 3. Real Example: Bug Scrub Skill Structure

```markdown
---
name: bug-scrub
description: 5-phase pipeline for triaging intel/torch-xpu-ops issues
---

## Phase 4b: Get AR from Related PRs

### Purpose
Find action reasons (AR) for issues by discovering related PRs

### Input
| Field | Type | Description |
|-------|------|-------------|
| issue_number | int | GitHub issue number |
| ci_failures | list | Failed CI jobs |
| test_module | str | Affected test file |

### Verification Paths

#### Path 0: Explicit Reference (Script)
```bash
# Check if action_TBD already contains PR reference
echo "${action_TBD}" | grep -oE '#[0-9]+|pull/[0-9]+'
```
**If found**: Extract PR number → verify state → output

#### Path 1: PR Body Search (Script)  
```bash
gh pr list --repo intel/torch-xpu-ops \
  --search "${ISSUE_NUMBER} in:body" \
  --json number,title,body,state
```
**Parse**: Look for `fixes #N`, `closes #N`, `resolves #N`

#### Path 2: Content-Match (LLM)
**Trigger**: Paths 0-1 fail
**Agent**: Explore
**Prompt**:
```
Analyze if PR #${PR} addresses Issue #${ISSUE}:
1. File overlap: Does PR touch files mentioned in issue?
2. Symptom overlap: Does PR fix the error described?
3. Timing: Was PR created after issue was opened?

Output JSON:
{
  "file_overlap": true/false,
  "symptom_match": true/false,
  "timing_valid": true/false,
  "confidence": "high|medium|low",
  "verdict": "ACCEPT|REJECT|UNCERTAIN"
}
```

### 4-Gate Verification
| Gate | Check | Pass Criteria |
|------|-------|---------------|
| G1 | Issue reference | PR body contains `#${ISSUE}` |
| G2 | State | `state == "MERGED"` |
| G3 | CI overlap | PR affects same test module |
| G4 | Timing | `mergedAt > issue.created_at` |

**Rule**: 4/4 gates → ACCEPT, 3/4 → MONITOR, <3 → REJECT

### Output Schema
```json
{
  "issue": 3727,
  "action_reason": "PR #3729 merged, fixes test_tanh failures",
  "confidence": "high",
  "verification_path": "path_1",
  "gates_passed": ["G1", "G2", "G3", "G4"]
}
```

### Anti-Patterns
| Don't | Why |
|-------|-----|
| Accept unmerged PRs as fixes | Issue isn't actually resolved |
| Skip Gate 4 (timing) | PR might predate the regression |
| Use LLM when script works | Wastes tokens, slower |
```

### 4. Making Skills Agent-Agnostic

Write skills so **any agent** (Claude, GPT, Gemini, human) can execute them:

```markdown
## Tool Abstraction
Instead of: "Use `gh` CLI"
Write:

### Tool: Fetch PR Data
**Interface**: 
- Input: `{repo: string, pr_number: int}`
- Output: `{state: string, body: string, merged_at: timestamp}`

**Implementations**:
- CLI: `gh pr view ${pr_number} --repo ${repo} --json state,body,mergedAt`
- API: `GET /repos/${repo}/pulls/${pr_number}`
- Manual: Open `https://github.com/${repo}/pull/${pr_number}`
```

### 5. Skill File Locations (OpenCode Compatible)

```
.opencode/skills/bug-scrub/SKILL.md     # Project-specific
.claude/skills/bug-scrub/SKILL.md       # Claude-compatible
~/.config/opencode/skills/my-skill/     # Global (all projects)
```

### 6. Testing Your Skill

Add a **test section** to validate reproducibility:

```markdown
## Test Cases

### Test 1: Explicit PR Reference
**Input**:
```json
{"issue": 3727, "action_TBD": "PR #3729"}
```
**Expected Path**: Path 0
**Expected Output**: 
```json
{"action_reason": "PR #3729", "verification_path": "path_0"}
```

### Test 2: No Explicit Reference
**Input**:
```json
{"issue": 3723, "action_TBD": ""}
```
**Expected Path**: Path 1 → Path 2
**Expected**: Agent invokes Explore for semantic analysis
```

### 7. Checklist for Reproducible Skills

- [ ] **Clear scope**: What does this skill do/not do?
- [ ] **Explicit methods**: Script vs LLM vs Tool for each step
- [ ] **Decision gates**: Precise conditions for branching
- [ ] **Tool commands**: Exact syntax, not pseudocode
- [ ] **Output schema**: JSON/structured format
- [ ] **Examples**: At least 2 real scenarios
- [ ] **Anti-patterns**: Common mistakes to avoid
- [ ] **Fallback paths**: What if primary method fails?
- [ ] **Test cases**: Input/output pairs for validation

---

## Summary

| Topic | Key Takeaway |
|-------|--------------|
| **Oh-My-OpenAgent** | Plugin that adds multi-agent orchestration (Sisyphus, Prometheus, Hephaestus) to OpenCode |
| **OpenCode** | Open-source agent with TUI/desktop/IDE, 75+ providers, primary + subagents |
| **Agentic AI** | AI that autonomously plans → acts → observes → adjusts in a loop until goal is achieved |
| **Writing Skills** | Be explicit about methods, define decision gates, specify tool commands, include examples |

---

*Generated: May 23, 2026*
