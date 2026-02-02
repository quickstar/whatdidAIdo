# ActivityWatch Worklog Analyzer

This directory contains tools for analyzing ActivityWatch data exports to generate worklog summaries.

## Files

- `aw-buckets-export.json` - ActivityWatch data export (large JSON file)
- `worklog.py` - Python script to analyze activity data
- `config.json` - Configuration for clients, projects, context hints

## Quick Usage

When the user asks "what did I do on [date]?" or needs a worklog for a specific date:

```bash
python3 D:/work/worklog.py <date> --ai
```

Then interpret the output into reportable worklog buckets with time estimates.

### Date formats accepted
- `27.01.2026` (European)
- `2026-01-27` (ISO)
- `27/01/2026`

## Worklog Interpretation Guidelines

When creating worklog summaries:

1. **Group by JIRA ticket** - Primary work items should map to tickets
   - `ROMSD-*` = Support/Bug tickets
   - `ITEM-*` = Feature/Task tickets

2. **Estimate billable time** - Usually ~85% of active time

3. **Identify categories:**
   - Development (IDE time, specific files)
   - Bug Fix (ROMSD tickets)
   - Code Review (GitHub PRs)
   - Testing/QA (test environments)
   - Meetings (Teams)
   - Support (ScreenConnect, remote desktop)
   - Infrastructure (ArgoCD, Azure, deployments)
   - Documentation
   - Administrative (MOCO, Quickticket)

4. **Look for context in window titles** - They reveal what tickets/features were being worked on

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
