# whatdidAIdo

> You know what you did. Your AI does too.

An AI-powered worklog generator built on [ActivityWatch](https://activitywatch.net/). Open [Claude Code](https://docs.anthropic.com/en/docs/claude-code) in the repo, ask *"what did I do yesterday?"*, and get a clean worklog table — no manual time tracking needed.

## How it works

```
ActivityWatch  →  SQLite DB  →  Claude Code  →  Worklog
(tracks your      (raw           (runs script     (ready to
 activity)         events)        & interprets)    submit)
```

1. **ActivityWatch** silently tracks your window activity, browser tabs, and AFK status
2. **Claude Code** runs the analysis script, reads your `CLAUDE.md` instructions, and interprets the raw data
3. You get a **formatted worklog** with estimated times, categorized by client and ticket

Just ask in natural language:
- *"What did I do today?"*
- *"Give me yesterday's worklog"*
- *"What did I work on last Friday?"*

## Features

- **JIRA ticket detection** — Finds ticket IDs from browser URLs, window titles, and git branch names
- **Client detection** — Maps domains and keywords to clients automatically
- **Meeting grouping** — Correlates Teams meetings with contacts and clients
- **Git branch tracking** — Knows which ticket you were working on based on your active branch
- **Break detection** — Identifies gaps in activity (lunch, coffee, etc.)
- **Smart context** — Distinguishes work YouTube (tutorials) from personal YouTube based on surrounding activity

## Quick Start

### Prerequisites

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (CLI)
- Python 3
- [ActivityWatch](https://activitywatch.net/) running and collecting data

### Setup

```bash
git clone https://github.com/quickstar/whatdidAIdo.git
cd whatdidAIdo
cp config.example.json config.json
```

Edit `config.json` with your details:
- Set your `database` path to the ActivityWatch SQLite database
- Add your `clients`, `contacts`, and `correlations`
- Add `known_tickets` for better descriptions

### Usage

Open Claude Code in the repo directory and just ask:

```
> What did I do today?
> Give me yesterday's worklog
> What did I work on on 24.02.2026?
```

Claude reads the `CLAUDE.md` instructions, runs the script, interprets the raw data, and outputs a formatted worklog table.

You can also run the script directly:

```bash
python worklog_db.py today --ai       # AI-friendly compact output
python worklog_db.py yesterday --ai   # Yesterday's activity
python worklog_db.py 24.02.2026 --ai  # Specific date
python worklog_db.py today            # Detailed raw output
```

### Date formats

All of these work: `24.02.2026`, `2026-02-24`, `24/02/2026`, `today`, `yesterday`

## Configuration

`config.json` controls how activities are categorized:

| Section | Purpose |
|---------|---------|
| `database` | Path to your ActivityWatch SQLite DB |
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

**08:30 - 17:15 (7.5h active) | Lunch: 12:00 - 12:30 (30m)**

| Cat | Client/Ticket | Description | Time |
|-----|---------------|-------------|------|
| Dev | PROJ-1234 | Implement user authentication flow | 4.5h |
| Bug | BUG-5678 | Fix session timeout on login page | 45m |
| Mtg | Acme (Jane Doe) | Sprint planning | 1h |
| Review | PR #42 | Review payment integration | 30m |
| Admin | — | Email, ticket triage | 30m |

## How AI time estimation works

Raw detection times (how long a browser tab or window was in focus) don't equal actual work time. The AI uses multiple signals:

1. **App times** — Total time in IDEs, terminals, git tools = actual dev time
2. **Git branches** — Which ticket branch was active = where dev time goes
3. **Window context** — File names and titles confirm what was being worked on
4. **Meeting duration** — Teams/calendar events = meeting time

A ticket might show 20 minutes of raw browser time, but if the IDE was open for 4 hours on that ticket's branch, the real dev time is ~4 hours.

## Database Location

The script looks for the database in this order:

1. `--db` CLI argument
2. `AW_DATABASE` environment variable
3. `database` field in `config.json`

## License

MIT
