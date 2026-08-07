# whatdidAIdo

> You know what you did. Your AI does too.

An AI-powered worklog generator built on [ActivityWatch](https://activitywatch.net/). Open an AI coding agent such as Codex or Claude Code in the repo, ask *"what did I do yesterday?"*, and get a clean worklog table — no manual time tracking needed.

## How it works

```
ActivityWatch ─┐
Codex history ─┼→ Evidence audit → AI Agent → Worklog → MOCO sync
GitHub + git ──┘     (complete)    (interprets)  (approved) (idempotent)
```

1. **ActivityWatch** silently tracks your window activity, browser tabs, and AFK status
2. **Codex history** contributes root-task titles, repositories, branches, tickets, and outcomes when available
3. **GitHub and local git** contribute every discovered repository, commit diff, push/rewrite, PR, review, and unpublished commit
4. **Your AI agent** interprets the combined evidence using deterministic session rules
5. An approved worklog can be synchronized to **MOCO** without duplicates and with native Jira links

Just ask in natural language:
- *"What did I do today?"*
- *"Give me yesterday's worklog"*
- *"What did I work on last Friday?"*

## Features

- **JIRA ticket detection** — Finds ticket IDs from browser URLs, window titles, and git branch names
- **Client detection** — Maps domains and keywords to clients automatically
- **Meeting grouping** — Correlates Teams meetings with contacts and clients
- **Git branch tracking** — Knows which ticket you were working on based on your active branch
- **Complete GitHub audit** — Combines commit search, events, PRs, push comparisons, and local repositories
- **Rewrite awareness** — Inspects rebases/squashes without double-counting the replacement commit
- **Codex task context** — Reads local root-task history while excluding delegated subagents
- **Idempotent MOCO sync** — Preserves existing entries by default, keeps durable local identity for untagged work, verifies totals, and checks Jira links after writes
- **Break detection** — Identifies gaps in activity (lunch, coffee, etc.)
- **Smart context** — Distinguishes work YouTube (tutorials) from personal YouTube based on surrounding activity

## Quick Start

### Prerequisites

- An AI coding agent that can run shell commands and read repository files, such as Codex or [Claude Code](https://docs.anthropic.com/en/docs/claude-code)
- Python 3
- [ActivityWatch](https://activitywatch.net/) running and collecting data
- [GitHub CLI](https://cli.github.com/) authenticated for the repositories to audit

### Setup

Clone this repository and open its directory in your terminal or AI agent, then create a local config:

```bash
cp config.example.json config.json
```

Edit `config.json` with your details:
- Set your `database` path to the ActivityWatch SQLite database
- Optionally set `codex_home`; otherwise `CODEX_HOME` or `~/.codex` is used
- Add your `clients`, `contacts`, and `correlations`
- Add `known_tickets` for better descriptions
- Configure `github.repositories_root` and author aliases
- Add confirmed year-specific MOCO project/task mappings when MOCO sync is used

### Usage

Open your AI coding agent in the repo directory and just ask:

```
> What did I do today?
> Give me yesterday's worklog
> What did I work on on 24.02.2026?
```

Agents should read `AGENTS.md`. Claude Code can use `CLAUDE.md`, which imports the same shared instructions. The agent runs the script, interprets the raw data, and outputs a formatted worklog table.

You can also run the script directly:

```bash
python worklog.py today --ai       # AI-friendly compact output
python worklog.py yesterday --ai   # Yesterday's activity
python worklog.py 24.02.2026 --ai  # Specific date
python worklog.py today            # Detailed raw output
python worklog.py today --no-codex # ActivityWatch only
python github_audit.py today --ai     # GitHub, PR, push/rewrite, and local git evidence
```

To synchronize an approved JSON worklog, copy `worklog.example.json` to
`approved-worklog-YYYY-MM-DD.json`, fill in the evidence-derived customer and
billability values, set `approved_total_hours`, dry-run it, and then apply:

```bash
python moco_sync.py approved-worklog-2026-07-28.json
python moco_sync.py approved-worklog-2026-07-28.json --apply
python moco_sync.py approved-worklog-2026-07-28.json --refresh-state # local ledger only
```

Existing entries are preserved unless `--update-existing --apply` is explicitly
used. `MOCO_API_KEY` is read from the process or Windows User/Machine environment
and is never printed. Non-ticket meetings or administrative entries are also
supported when they provide a stable `sync_key` for duplicate detection. MOCO
IDs for those entries are retained in the ignored local `.moco-sync-state.json`
ledger. The sync key is not written as a visible MOCO tag; only Jira-backed
activities are tagged. Dry-run and apply output include approved, desired, and
effective stored totals so protected existing differences remain visible.

### Date formats

All of these work: `24.02.2026`, `2026-02-24`, `24/02/2026`, `today`, `yesterday`

## Configuration

`config.json` controls how activities are categorized:

| Section | Purpose |
|---------|---------|
| `database` | Path to your ActivityWatch SQLite DB |
| `codex_home` | Optional Codex data directory; defaults to `CODEX_HOME` or `~/.codex` |
| `github` | Login, timezone, local repository root, and git author aliases |
| `moco.customer_projects` | Confirmed year-specific customer project/task IDs for synchronization |
| `clients` | Keyword → client name mapping (e.g. `"acme": "Acme Corp"`) |
| `contacts` | Person → company mapping for meeting grouping |
| `correlations` | Links clients to contacts for meeting attribution |
| `ticket_prefixes` | JIRA project prefixes to detect (e.g. `"PROJ"`, `"BUG"`) |
| `known_tickets` | Ticket ID → description for better summaries |
| `projects` | Repository/project name mappings |
| `context_hints` | Help AI interpret ambiguous sites (YouTube, GitHub, etc.) |
| `likely_personal` | Keywords to filter out personal activity |

See [`config.example.json`](config.example.json) for a full template.

## Output Example

The `--ai` flag produces a compact summary that an AI can interpret into a worklog like this:

**Observed 08:30 - 17:15 | ActivityWatch interaction: 2.7h | GitHub activity through 21:27**

| Cat | Client/Ticket | Description | Time |
|-----|---------------|-------------|------|
| Dev | PROJ-1234 | Implement user authentication flow | 4.5h |
| Bug | BUG-5678 | Fix session timeout on login page | 45m |
| Mtg | Acme (Jane Doe) | Sprint planning | 1h |
| Review | PR #42 | Review payment integration | 30m |
| Admin | — | Email, ticket triage | 30m |

## How AI time estimation works

Raw detection times (how long a browser tab or window was in focus) don't equal actual work time. The AI uses multiple signals:

1. **App times** — Foreground IDE, terminal, and git-tool intervals provide session evidence
2. **Git branches** — Which ticket branch was active = where dev time goes
3. **Window context** — File names and titles confirm what was being worked on
4. **Codex tasks** — Root-task titles, repositories, branches, tickets, and outcomes explain the work
5. **GitHub audit** — Commit diffs, push/rewrite events, PRs, and reviews reveal sessions ActivityWatch missed
6. **Meeting duration** — Explicitly supplied calendar evidence can establish attended meeting time

A ticket might show 20 minutes of raw browser time while a coherent foreground,
Codex, and commit sequence supports a longer development session. An application
merely remaining open is never enough to count the intervening gap.

### Codex history and time

When local Codex state is available, the analyzer reads `state_5.sqlite` and the referenced rollout JSONL files. It includes user-owned root tasks, including older tasks reopened on the requested date, and excludes subagents and automations to avoid obvious double counting.

Codex spans are semantic evidence, not billable durations. Tasks can run concurrently or continue in the background, so the output labels both the task span and its overlap with merged ActivityWatch `not-afk` intervals. The analyzer also prints a conservative ActivityWatch + qualifying-Codex evidence-union candidate; the agent validates its extensions against git, review, build, foreground, and outcome evidence before using it. The agent splits unsupported gaps, unions accepted intervals to prevent double counting, rounds only after attribution, and labels medium/low-confidence estimates. Contractual billability comes from Jira/MOCO rules—never a percentage of ActivityWatch time. Only compact task and outcome summaries are printed; raw prompts and tool output are not dumped.

## Database Location

The script looks for the ActivityWatch database in this order:

1. `--db` CLI argument
2. `AW_DATABASE` environment variable
3. `database` field in `config.json`

It resolves the Codex data directory separately in this order:

1. `--codex-home` CLI argument
2. `CODEX_HOME` environment variable
3. `codex_home` field in `config.json`
4. `~/.codex`

## License

MIT
