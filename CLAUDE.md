# ActivityWatch Worklog Analyzer

This directory contains tools for analyzing ActivityWatch data exports to generate worklog summaries.

## Files

- `worklog_db.py` - **Primary script** - Queries SQLite database directly (fast, real-time)
- `worklog.py` - Legacy script using JSON export
- `config.json` - Configuration for clients, projects, context hints
- `aw-buckets-export.json` - ActivityWatch data export (for legacy script)

## Quick Usage

When the user asks "what did I do on [date]?" or needs a worklog for a specific date:

```bash
python3 D:/work/worklog_db.py <date> --ai
```

### What the script outputs

1. **Categorized Summary** - Raw detection times (marked with "raw")
   - Meetings grouped by client using `contacts` and `correlations` from config.json
   - JIRA tickets with raw browser/branch detection time (NOT actual work time)

2. **Raw Data** - For estimating actual work time:
   - App Times (rider64.exe, Cursor.exe, WindowsTerminal.exe, etc.)
   - Git Branches (with ticket extraction)
   - Window Context (what files/features were being worked on)

### How to interpret and estimate time

**IMPORTANT:** Ticket times marked "(raw)" are detection times, not actual work time!

To estimate actual development time for a ticket:
1. Look at **App Times** - total IDE + terminal + git time
2. Look at **Git Branches** - which ticket branch was active
3. Look at **Window Context** - confirms what was being worked on
4. Attribute the dev app time to the dominant ticket

**Example:**
```
Raw output shows: ITEM-3049: 26m (raw)
App Times show: rider64.exe: 1.3h, Cursor.exe: 21m, Terminal: 2.6h, Git: 33m
Git Branches show: ITEM-3049 branch was active
Window Context shows: Translation Caching work

→ Estimate: ITEM-3049 = 4.5h (sum of dev app times)
```

### Output Format

Output the final worklog as a table with clickable JIRA links:

| Category | Client/Ticket | Description | Time |
|----------|---------------|-------------|------|
| Development | [ITEM-1234](https://3volutions.atlassian.net/browse/ITEM-1234) | Feature description | 4.5h |
| Bug Investigation | [ROMSD-5678](https://3volutions.atlassian.net/browse/ROMSD-5678) | Issue description | 30m |
| Meeting | Client (Contact) | Meeting topic | 1h |

### Shortcuts
```bash
python3 D:/work/worklog_db.py today --ai      # Today's activity
python3 D:/work/worklog_db.py yesterday --ai  # Yesterday's activity
```

### Database Location

Configured in `config.json`:
```json
{
  "database": "C:\\Users\\Lukas\\OneDrive\\ActivityWatchSync\\andromeda\\...\\test.db"
}
```

Or via environment variable:
```bash
set AW_DATABASE=C:\path\to\test.db
```

Or CLI argument:
```bash
python3 worklog_db.py 27.01.2026 --ai --db "C:\path\to\test.db"
```

### Date formats accepted
- `27.01.2026` (European)
- `2026-01-27` (ISO)
- `27/01/2026`

## Worklog Interpretation Guidelines

### Time Estimation Rules

1. **Development time** = IDE apps + Terminal (if dev session) + Git tools
   - `rider64.exe`, `Cursor.exe`, `Code.exe` = direct coding
   - `WindowsTerminal.exe` = dev-related if IDE time is significant
   - `GitExtensions.exe` = version control work

2. **Attribute dev time to tickets based on:**
   - Active git branch (most reliable)
   - Window titles mentioning ticket numbers
   - File names matching ticket work

3. **ROMSD tickets** = Bug investigation time (usually just the raw detection time)
4. **ITEM tickets** = Feature development (attribute full dev session time)

### Categories

- **Development** - ITEM tickets, IDE time, feature work
- **Bug Fix / Investigation** - ROMSD tickets
- **Code Review** - GitHub PRs
- **Meeting** - Teams (grouped by client via correlations)
- **Support** - ScreenConnect, remote desktop sessions
- **Infrastructure** - ArgoCD, Azure, deployments
- **Administrative** - Email, MOCO, Quickticket

### Billable Time

Usually ~85% of total active time is billable.

## Context Interpretation (Important!)

**Browser activity requires context analysis - don't blindly categorize as personal:**

| Activity | Could be Work | Could be Personal |
|----------|--------------|-------------------|
| YouTube | Tech tutorials, conference talks, debugging videos | Entertainment, music |
| t3.chat | Coding questions, architecture discussions | Personal chat |
| Google Search | Error messages, API docs, how-to | Random browsing |
| GitHub | 3volutionsAG repos, PR reviews | Personal projects |

**How to determine:**
- Check surrounding activity (coding in Cursor/Rider = likely research)
- Look at window titles for technical terms
- Consider time of day and work session context
- If ambiguous during heavy coding session, assume work-related

## Clients (from config.json)

Detected automatically from domains:
- `roche.*.3vrooms.app` → Roche
- `uzh.*.3vrooms.app` → UZH
- `enbw.*.3vrooms.app` → EnBW
- `stgag` / `STG-*` → STGAG
- `sales.*.3vrooms.app` → Sales/Demo

## Environments

- `vnext.book.3vrooms.local` - Local development
- `vnext.book.3vrooms.app` - VNext staging
- `localhost:4200` - Local frontend
- `deploy.3vrooms.app` - ArgoCD deployments

## Projects

- `rooms` - 3V-ROOMS main product (.NET)
- `quickrooms` - Quickrooms Node.js frontend
- `ngx-rooms-lib` - Angular rooms library
- `argocd-config` - Infrastructure/GitOps

## Common Apps

- `Cursor.exe` / `Code.exe` - Code editors (AI-assisted coding)
- `rider64.exe` - JetBrains Rider (.NET development)
- `datagrip64.exe` - DataGrip (database work)
- `GitExtensions.exe` - Git operations
- `msedge.exe` / `zen.exe` - Browsers
- `ms-teams.exe` - Meetings/chat
- `mstsc.exe` - Remote Desktop
- `ScreenConnect.WindowsClient.exe` - Remote support sessions
- `Signal.exe` - Messaging (could be work or personal)

## Adding Known Tickets

To improve summaries, add ticket descriptions to `config.json`:

```json
{
  "known_tickets": {
    "ROMSD-6232": "Outlook Add-In room booking sync issue",
    "ROMSD-6237": "Booking save error",
    "ITEM-3496": "Outlook series recurrence handling"
  }
}
```
