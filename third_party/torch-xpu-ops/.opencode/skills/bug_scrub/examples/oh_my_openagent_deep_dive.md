# Oh My OpenAgent - Deep Dive

> Detailed explanation of Oh My OpenAgent's key mechanisms and architecture.

---

## Table of Contents

1. [Category Routing](#1-category-routing)
2. [Hash-Anchored Edits](#2-hash-anchored-edits)
3. [Skill-Embedded MCPs](#3-skill-embedded-mcps)
4. [Background Agents](#4-background-agents)
5. [Team Mode (v4.0)](#5-team-mode-v40)

---

## 1. Category Routing

### What It Is

Category Routing is **model selection abstraction** - agents don't pick specific models, they declare **what kind of work** they need done, and the harness automatically routes to the optimal model.

### How It Works

```
┌─────────────────────────────────────────────────────────────────┐
│                    AGENT REQUEST                                 │
│  "I need to do visual-engineering work on this React component" │
└─────────────────────────────┬───────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                  CATEGORY EXTRACTION                             │
│  Detected category: "visual-engineering"                        │
└─────────────────────────────┬───────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                  CATEGORY → MODEL MAPPING                        │
│  visual-engineering → GPT-5.5 (best for UI/UX)                  │
│  deep → GPT-5.5 (autonomous research)                           │
│  quick → Claude Haiku (fast, cheap)                             │
│  ultrabrain → Claude Opus / Kimi K2.6 (complex logic)           │
└─────────────────────────────┬───────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                  MODEL INVOCATION                                │
│  → Routes to GPT-5.5 for the visual-engineering task            │
└─────────────────────────────────────────────────────────────────┘
```

### Category Types

| Category | Best For | Default Model |
|----------|----------|---------------|
| `visual-engineering` | Frontend, UI/UX, design, CSS, React | GPT-5.5 |
| `deep` | Autonomous research + execution, exploration | GPT-5.5 |
| `quick` | Single-file changes, typos, simple fixes | Claude Haiku |
| `ultrabrain` | Hard logic, architecture, complex reasoning | Claude Opus / Kimi K2.6 |
| `business-logic` | Backend logic, algorithms | Configurable |

### Why This Matters

```
❌ WITHOUT Category Routing:
   Agent: "Use claude-sonnet-4 for this task"
   Problem: Agent must know which model is best for each task type
   Problem: Model names change, capabilities evolve
   Problem: Different users have different model access

✅ WITH Category Routing:
   Agent: "This is visual-engineering work"
   Harness: Automatically picks GPT-5.5 (or user's configured alternative)
   Benefit: Agent logic stays stable even as models change
   Benefit: Users can customize model mappings without changing agent code
```

### Configuration

```jsonc
// .opencode/oh-my-openagent.jsonc
{
  "category_routing": {
    "visual-engineering": "openai/gpt-5.5",
    "deep": "openai/gpt-5.5",
    "quick": "anthropic/claude-haiku-4",
    "ultrabrain": "anthropic/claude-opus-4"
  }
}
```

---

## 2. Hash-Anchored Edits

### The Problem: "The Harness Problem"

Traditional file editing in AI agents is **fragile**:

```
Traditional Edit Tool:
1. Agent reads file, sees line 42: "function hello() {"
2. Agent outputs: "Replace 'function hello() {' with 'async function hello() {'"
3. Problem: If file changed since read, edit may apply to wrong line
4. Problem: Agent must perfectly reproduce whitespace, indentation
5. Problem: Ambiguous if same text appears multiple times
```

This is called **"The Harness Problem"** - most agent failures aren't the model's fault, they're the **edit tool's fault**.

### Hash-Anchored Solution

Every line the agent reads comes back **tagged with a content hash**:

```
Agent sees:
┌────────────────────────────────────────────────┐
│ 11#VK| function hello() {                      │
│ 22#XJ|   return "world";                       │
│ 33#MB| }                                       │
└────────────────────────────────────────────────┘

Where:
  11     = Line number
  #VK    = Content hash (computed from line content)
  |      = Separator
  ...    = Actual line content
```

### How Edits Work

```
Agent wants to edit line 11:

┌─────────────────────────────────────────────────────────────────┐
│ EDIT REQUEST                                                     │
│ Line: 11#VK                                                     │
│ New Content: "async function hello() {"                         │
└─────────────────────────────┬───────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ VALIDATION                                                       │
│ 1. Read current line 11                                         │
│ 2. Compute hash of current content                              │
│ 3. Compare: current_hash == "VK" ?                              │
└─────────────────────────────┬───────────────────────────────────┘
                              │
              ┌───────────────┴───────────────┐
              │                               │
         Hash Match ✓                    Hash Mismatch ✗
              │                               │
              ▼                               ▼
┌─────────────────────────┐    ┌─────────────────────────────────┐
│ APPLY EDIT              │    │ REJECT EDIT                     │
│ Line 11 updated safely  │    │ File changed since last read    │
└─────────────────────────┘    │ Agent must re-read before edit  │
                               └─────────────────────────────────┘
```

### Benefits

| Problem | Hash-Anchored Solution |
|---------|------------------------|
| Stale edits | Hash mismatch → edit rejected before corruption |
| Whitespace errors | Agent references hash, not content reproduction |
| Ambiguous edits | Line number + hash = unique identifier |
| Concurrent edits | Automatic conflict detection |

### Real Impact

> "Grok Code Fast 1: **6.7% → 68.3%** success rate, just from changing the edit tool."

The same model, with hash-anchored edits instead of traditional edits, improved **10x** in accuracy.

### Inspired By

This feature was inspired by [oh-my-pi](https://github.com/can1357/oh-my-pi) by Can Bölük, who wrote about [The Harness Problem](https://blog.can.ac/2026/02/12/the-harness-problem/).

---

## 3. Skill-Embedded MCPs

### The Problem: MCP Context Bloat

MCP (Model Context Protocol) servers provide external tools to agents. But:

```
Traditional MCP Setup:
┌─────────────────────────────────────────────────────────────────┐
│ AGENT CONTEXT WINDOW                                             │
├─────────────────────────────────────────────────────────────────┤
│ System prompt: 2,000 tokens                                     │
│ MCP: Playwright tools: 5,000 tokens (always loaded)             │
│ MCP: Database tools: 3,000 tokens (always loaded)               │
│ MCP: AWS tools: 4,000 tokens (always loaded)                    │
│ MCP: Kubernetes tools: 3,000 tokens (always loaded)             │
├─────────────────────────────────────────────────────────────────┤
│ Total overhead: 17,000 tokens                                   │
│ User's actual task: 1,000 tokens                                │
│                                                                 │
│ Problem: 15,000 tokens wasted on unused tools!                  │
└─────────────────────────────────────────────────────────────────┘
```

### Skill-Embedded MCP Solution

Skills **bring their own MCP servers** that spin up **only when the skill is loaded**:

```
With Skill-Embedded MCPs:
┌─────────────────────────────────────────────────────────────────┐
│ AGENT CONTEXT WINDOW                                             │
├─────────────────────────────────────────────────────────────────┤
│ System prompt: 2,000 tokens                                     │
│ Available skills: 200 tokens (just names + descriptions)        │
├─────────────────────────────────────────────────────────────────┤
│ Total baseline: 2,200 tokens                                    │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ User asks for browser automation
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ SKILL LOAD: playwright                                           │
├─────────────────────────────────────────────────────────────────┤
│ 1. Load SKILL.md content: 1,500 tokens                          │
│ 2. Start Playwright MCP server (background process)             │
│ 3. Inject Playwright tools: 3,000 tokens                        │
├─────────────────────────────────────────────────────────────────┤
│ Context now: 2,200 + 1,500 + 3,000 = 6,700 tokens               │
│ (Only Playwright tools, not all MCPs!)                          │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ Task complete
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ SKILL UNLOAD                                                     │
├─────────────────────────────────────────────────────────────────┤
│ 1. Stop Playwright MCP server                                   │
│ 2. Remove Playwright tools from context                         │
│ 3. Back to baseline: 2,200 tokens                               │
└─────────────────────────────────────────────────────────────────┘
```

### How Skills Define Embedded MCPs

```markdown
<!-- .opencode/skills/playwright/SKILL.md -->
---
name: playwright
description: Browser automation for testing and scraping
mcp:
  server: "npx playwright-mcp-server"
  tools:
    - navigate
    - click
    - screenshot
    - fill_form
---

## Instructions for browser automation...
```

### Lifecycle

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│ Skill Load   │ ──► │ MCP Starts   │ ──► │ Tools Ready  │
│ skill(name)  │     │ (background) │     │ (in context) │
└──────────────┘     └──────────────┘     └──────────────┘
       │                                          │
       │         Task execution...                │
       │                                          │
       ▼                                          ▼
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│ Task Done    │ ──► │ MCP Stops    │ ──► │ Context Free │
│              │     │ (cleanup)    │     │ (back to     │
│              │     │              │     │  baseline)   │
└──────────────┘     └──────────────┘     └──────────────┘
```

### Benefits

| Benefit | Explanation |
|---------|-------------|
| **Lean context** | Only load MCP tools when needed |
| **Fast startup** | Don't wait for all MCPs to initialize |
| **Clean separation** | Each skill manages its own dependencies |
| **Resource efficient** | MCP processes only run when needed |

---

## 4. Background Agents

### What It Is

Background Agents allow the **main agent to spawn multiple specialist agents** that run **in parallel**, each working on a different aspect of the task.

### How It Works

```
┌─────────────────────────────────────────────────────────────────┐
│ USER REQUEST                                                     │
│ "Review this PR for security, performance, and code quality"   │
└─────────────────────────────┬───────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ MAIN AGENT (Sisyphus)                                            │
│ "I'll spawn 3 specialists to handle this in parallel"           │
└─────────────────────────────┬───────────────────────────────────┘
                              │
          ┌───────────────────┼───────────────────┐
          │                   │                   │
          ▼                   ▼                   ▼
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│ BACKGROUND #1   │ │ BACKGROUND #2   │ │ BACKGROUND #3   │
│ Security Audit  │ │ Performance     │ │ Code Quality    │
│                 │ │ Analysis        │ │ Review          │
│ - SQL injection │ │ - N+1 queries   │ │ - Code style    │
│ - XSS vectors   │ │ - Memory leaks  │ │ - Best practices│
│ - Auth flaws    │ │ - Bottlenecks   │ │ - Readability   │
└────────┬────────┘ └────────┬────────┘ └────────┬────────┘
         │                   │                   │
         │    Running in parallel (async)        │
         │                   │                   │
         └───────────────────┴───────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ MAIN AGENT - RESULT MERGER                                       │
│ Collects all 3 results, synthesizes into unified review         │
└─────────────────────────────────────────────────────────────────┘
```

### Key Characteristics

| Aspect | Detail |
|--------|--------|
| **Parallelism** | 5+ agents can run simultaneously |
| **Isolation** | Each agent has its own context, no interference |
| **Asynchronous** | Main agent doesn't block waiting for each one |
| **Result merging** | Main agent synthesizes findings from all specialists |
| **Resource limits** | Configurable concurrency per provider/model |

### Configuration

```jsonc
// .opencode/oh-my-openagent.jsonc
{
  "background_agents": {
    "max_concurrent": 5,
    "timeout_seconds": 300,
    "per_provider_limits": {
      "openai": 3,
      "anthropic": 2
    }
  }
}
```

### Use Cases

| Task | Background Agent Strategy |
|------|---------------------------|
| **Code Review** | Security + Performance + Style agents |
| **Research** | Multiple search angles simultaneously |
| **Refactoring** | Analyze dependencies, find usages, check tests in parallel |
| **Documentation** | API docs, README, inline comments agents |

### Context Efficiency

```
WITHOUT Background Agents:
┌─────────────────────────────────────────────────────────────────┐
│ SINGLE AGENT                                                     │
│ Context: Growing with every step (60K+ tokens)                  │
│ - Read security docs                                            │
│ - Analyze security                                              │
│ - Read perf docs                                                │
│ - Analyze performance                                           │
│ - Read style guide                                              │
│ - Analyze style                                                 │
│ → Context bloat, slower, risk of context overflow               │
└─────────────────────────────────────────────────────────────────┘

WITH Background Agents:
┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐
│ Security Agent   │ │ Perf Agent       │ │ Style Agent      │
│ Context: 15K     │ │ Context: 15K     │ │ Context: 15K     │
│ (only security)  │ │ (only perf)      │ │ (only style)     │
└──────────────────┘ └──────────────────┘ └──────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ Main Agent: Just merge 3 concise reports (5K total)             │
└─────────────────────────────────────────────────────────────────┘
```

---

## 5. Team Mode (v4.0)

### What It Is

Team Mode transforms Oh My OpenAgent from "one agent with subagents" into a **real multi-agent system** with:
- A **lead agent** that orchestrates
- Up to **8 parallel team members**
- **Dedicated communication tools** (`team_create`, `team_send_message`, etc.)
- **Real-time tmux visualization**

### Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         TEAM MODE                                │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                    LEAD AGENT                            │   │
│  │              (Orchestrator/Sisyphus)                     │   │
│  │                                                          │   │
│  │  Responsibilities:                                       │   │
│  │  - Decompose task into subtasks                         │   │
│  │  - Assign work to team members                          │   │
│  │  - Monitor progress                                      │   │
│  │  - Resolve conflicts                                     │   │
│  │  - Synthesize final result                              │   │
│  └─────────────────────────────┬───────────────────────────┘   │
│                                │                                │
│            ┌───────────────────┼───────────────────┐           │
│            │         Team Communication            │           │
│            │    team_create, team_send_message,    │           │
│            │    team_task_create, team_status      │           │
│            ▼                   ▼                   ▼           │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐           │
│  │ Team Member 1│ │ Team Member 2│ │ Team Member N│           │
│  │ (Frontend)   │ │ (Backend)    │ │ (Testing)    │           │
│  │              │ │              │ │              │           │
│  │ Specialist   │ │ Specialist   │ │ Specialist   │           │
│  │ in React/CSS │ │ in APIs/DB   │ │ in QA/Tests  │           │
│  └──────────────┘ └──────────────┘ └──────────────┘           │
│         │                │                │                    │
│         └────────────────┴────────────────┘                    │
│                          │                                     │
│                    ┌─────┴─────┐                               │
│                    │   TMUX    │                               │
│                    │  Display  │                               │
│                    │           │                               │
│                    │ Real-time │                               │
│                    │ view of   │                               │
│                    │ all agents│                               │
│                    └───────────┘                               │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Team Communication Tools

| Tool | Purpose |
|------|---------|
| `team_create` | Spawn a new team member with specific role |
| `team_send_message` | Send message/instruction to a team member |
| `team_task_create` | Assign a specific task to a team member |
| `team_status` | Check status of all team members |
| `team_collect` | Gather results from all members |

### TMUX Visualization

When Team Mode is enabled, you see **all agents working in real-time**:

```
┌─────────────────────────────────────────────────────────────────┐
│ TMUX SESSION: oh-my-openagent-team                              │
├──────────────────────────────┬──────────────────────────────────┤
│ [LEAD] Sisyphus              │ [1] Frontend Agent               │
│                              │                                  │
│ Planning task breakdown...   │ Working on Header.tsx            │
│ Assigning work to members... │ Updating CSS styles...           │
│                              │                                  │
├──────────────────────────────┼──────────────────────────────────┤
│ [2] Backend Agent            │ [3] Testing Agent                │
│                              │                                  │
│ Creating API endpoint        │ Writing unit tests               │
│ /api/users/profile           │ for UserService                  │
│                              │                                  │
└──────────────────────────────┴──────────────────────────────────┘
```

### Built-in Team Skills

| Skill | Team Composition | Purpose |
|-------|------------------|---------|
| `hyperplan` | 5 hostile critics | Tear apart your plan from orthogonal angles before coding |
| `security-research` | 3 hunters + 2 PoC engineers | Parallel security audit with exploitability verification |

### Configuration

```jsonc
// .opencode/oh-my-openagent.jsonc
{
  "team_mode": {
    "enabled": true,
    "max_parallel_members": 4,
    "tmux_visualization": true,
    "communication_style": "structured"  // or "freeform"
  }
}
```

### Team Mode vs Background Agents

| Aspect | Background Agents | Team Mode |
|--------|-------------------|-----------|
| **Communication** | Fire-and-forget | Bidirectional messaging |
| **Coordination** | Results merged at end | Ongoing coordination |
| **Visualization** | None | Real-time tmux |
| **Interaction** | Independent tasks | Can collaborate |
| **Use case** | Parallel analysis | Complex collaborative work |

### Example: Building a Feature with Team Mode

```
User: "Build a user profile page with API and tests"

Lead Agent:
├── team_create("frontend", "Build React profile UI")
├── team_create("backend", "Create /api/profile endpoint")  
├── team_create("testing", "Write tests for profile feature")
│
│   [All 3 working in parallel, visible in tmux]
│
├── team_status() → Check progress
├── team_send_message("frontend", "Backend API is ready, here's the schema...")
├── team_send_message("testing", "Components done, you can add integration tests")
│
├── team_collect() → Gather all results
│
└── Synthesize: "Feature complete. Files created: ..."
```

---

## Summary Comparison

| Feature | Purpose | Key Benefit |
|---------|---------|-------------|
| **Category Routing** | Abstract model selection | Future-proof agent logic |
| **Hash-Anchored Edits** | Reliable file editing | 10x better edit accuracy |
| **Skill-Embedded MCPs** | On-demand tool loading | Lean context window |
| **Background Agents** | Parallel specialists | Faster, isolated analysis |
| **Team Mode** | Coordinated multi-agent | Complex collaborative tasks |

---

## References

- Oh My OpenAgent: https://github.com/code-yeongyu/oh-my-openagent
- The Harness Problem: https://blog.can.ac/2026/02/12/the-harness-problem/
- oh-my-pi (Hash edit inspiration): https://github.com/can1357/oh-my-pi
- Team Mode Documentation: https://omo.vibetip.help/docs/team-mode

---

*Generated: May 23, 2026*
