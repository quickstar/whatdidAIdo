# whatdidAIdo

> You know what you did. Your AI does too.

A CLI tool that reads your [ActivityWatch](https://activitywatch.net/) data and uses AI to turn a day's worth of window switches, browser tabs, git branches, and meetings into a clean worklog table — so you don't have to remember what you did.

## How it works

```
ActivityWatch  →  SQLite DB  →  whatdidAIdo  →  AI  →  Worklog
 (tracks your      (raw          (extracts &      (interprets     (ready to
  activity)         events)       categorizes)      & estimates)    submit)
```

The script queries your ActivityWatch database, extracts activities (apps, browser history, git branches, meetings), and outputs a structured summary. Feed that to an AI (Claude, ChatGPT, etc.) and get back a formatted worklog with time estimates.

## Features

- **JIRA ticket detection** — Finds ticket IDs from browser URLs, window titles, and git branch names
- **Client detection** — Maps domains and keywords to clients automatically
- **Meeting grouping** — Correlates Teams meetings with contacts and clients
- **Git branch tracking** — Knows which ticket you were working on based on your active branch
- **Break detection** — Identifies gaps in activity (lunch, coffee, etc.)
- **Smart context** — Distinguishes work YouTube (tutorials) from personal YouTube based on surrounding activity

## Quick Start

### Prerequisites

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

```bash
# Today's worklog (AI-friendly output)
python worklog_db.py today --ai

# Yesterday
python worklog_db.py yesterday --ai

# Specific date
python worklog_db.py 24.02.2026 --ai

# Detailed output (without AI formatting)
python worklog_db.py today
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
