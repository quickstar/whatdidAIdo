# ActivityWatch Worklog Analyzer - Agent Guide

This repository contains tools for turning ActivityWatch data into worklog summaries.

## Purpose

When the user asks questions such as "what did I do today?", "what did I do yesterday?", or asks for a worklog for a specific date, run the ActivityWatch analysis script and interpret its output into a concise worklog.

## Important Files

- `worklog_db.py` - Primary script. Reads the ActivityWatch SQLite database directly.
- `worklog.py` - Legacy script that reads `aw-buckets-export.json`.
- `config.json` - Local configuration for database path, clients, contacts, correlations, projects, and ticket metadata.
- `config.example.json` - Template for new setups.
- `aw-buckets-export.json` - Legacy ActivityWatch JSON export for `worklog.py`.

## Required Workflow

1. Read `config.json` first to understand clients, contacts, ticket prefixes, project mappings, known tickets, personal filters, and context hints.
2. Run the primary script for the requested date:

   ```bash
   python worklog_db.py <date> --ai
   ```

   On this Windows machine, use `python`, not `python3`.

3. Interpret the script output. Raw ticket detections are signals, not final billable time.
4. Cross-check relevant git history for the date when estimating development time.
5. Return the final worklog as a markdown table with clickable Jira links.

## Command Rules

- Assume you are already in the correct working directory unless the user explicitly says otherwise.
- Do not prefix shell commands with `cd`.
- Do not use `git -C <path>`.
- If your execution environment supports a separate working-directory setting, use that setting when you need to run a command from another repository, then run the command itself directly.
- Apply these rules to spawned subagents as well.

## Date Inputs

Accepted date inputs include:

- `today`
- `yesterday`
- `27.01.2026`
- `2026-01-27`
- `27/01/2026`

Examples:

```bash
python worklog_db.py today --ai
python worklog_db.py yesterday --ai
python worklog_db.py 27.01.2026 --ai
```

## Script Output

The `--ai` output is compact and designed for interpretation by an AI agent.

It usually includes:

- Categorized summary with raw detection times.
- Meetings grouped by client using `contacts` and `correlations` from `config.json`.
- Jira tickets detected from browser URLs, window titles, and git branches.
- App times for IDEs, terminals, browsers, Teams, git tools, and other apps.
- Git branch activity with ticket extraction.
- Window context for files, features, PRs, tickets, and browser pages.
- Activity window, total active time, and breaks.

## Time Estimation Rules

Ticket times marked as raw are detection times only. Do not treat them as final work durations.

Estimate development time from:

- IDE/editor time: `rider64.exe`, `Cursor.exe`, `Code.exe`.
- Terminal time when it belongs to the same development session.
- Git tool time such as `GitExtensions.exe`.
- Active git branch names and ticket IDs.
- Window titles, file names, and surrounding context.

General rules:

- `ITEM-*` tickets are usually feature development. Attribute the relevant development session time to the dominant active ticket.
- `ROMSD-*` tickets are usually bug investigation or support. Use raw detection time only when the surrounding app/window context does not show a larger development session.
- If one ticket branch dominates a development session, assign the IDE, terminal, and git time for that session to that ticket.
- If activity is ambiguous during an otherwise clear coding session, treat technical browsing as work-related.
- Billable time is usually around 85 percent of total active time unless the evidence suggests otherwise.

Example:

```text
Raw output shows: ITEM-3049: 26m raw
App Times show: rider64.exe 1.3h, Cursor.exe 21m, Terminal 2.6h, Git 33m
Git Branches show: ITEM-3049 branch was active
Window Context shows: Translation Caching work

Estimate: ITEM-3049 = 4.5h
```

## Git History Cross-Check

Always check git history for repositories that are relevant to the detected work, especially repositories under `D:\git`.

Use the command from inside the target repository directory:

```bash
git log --all --after="YYYY-MM-DD 00:00" --before="YYYY-MM-DD 23:59" --author="Lukas" --format="%h %ad %s" --date=format:"%H:%M"
```

Use commit times this way:

- Map each commit to an ActivityWatch activity window.
- Commits inside a break period usually mean ActivityWatch missed work while the screen was idle.
- Commits after the last ActivityWatch window indicate a separate untracked session.
- Group commits by topic or ticket and add estimated session time to the matching worklog item.

Known repository to check often:

- `D:\git\rooms` - 3V-ROOMS main product.

## Output Format

The final answer must begin with a day summary line followed immediately by a markdown table. Do not wrap the table in a code block.

Use short category labels so the table renders cleanly:

- Dev
- Bug
- Review
- Mtg
- Support
- Infra
- Admin

Summary line format:

```text
08:30 - 17:15 (7.5h active) | Lunch: 12:00 - 12:30 (30m)
```

Table format:

```markdown
| Cat | Client/Ticket | Description | Time |
|-----|---------------|-------------|------|
| Dev | [ITEM-1234](https://3volutions.atlassian.net/browse/ITEM-1234) | Feature description | 4.5h |
| Bug | [ROMSD-5678](https://3volutions.atlassian.net/browse/ROMSD-5678) | Issue description | 30m |
| Mtg | Client (Contact) | Meeting topic | 1h |
```

Additional notes or uncertainty can follow after the table, but the table must come first.

## Categories

- Dev - ITEM tickets, feature work, IDE sessions.
- Bug - ROMSD tickets, incident analysis, bug fixes.
- Review - GitHub PR review, code review, merge checks.
- Mtg - Teams meetings and calls.
- Support - ScreenConnect, remote desktop, customer support.
- Infra - ArgoCD, Azure, deployments, infrastructure work.
- Admin - Email, MOCO, Quickticket, planning, ticket triage.

## Context Interpretation

Browser activity requires surrounding context. Do not blindly mark browser activity as personal.

| Activity | Could be Work | Could be Personal |
|----------|---------------|-------------------|
| YouTube | Tech tutorials, conference talks, debugging videos | Entertainment, music |
| t3.chat | Coding questions, architecture discussion | Personal chat |
| Google Search | Error messages, API docs, how-to research | Random browsing |
| GitHub | 3volutionsAG repos, PR reviews | Personal projects |

How to decide:

- Check surrounding activity. Coding immediately before or after browser activity usually means research.
- Read window titles for technical terms, ticket IDs, repo names, and file names.
- Consider the time of day and session context.
- If ambiguous during heavy coding activity, assume work-related.

## Local Context

Client and environment mappings come from `config.json`.

Common environments:

- `vnext.book.3vrooms.local` - Local development.
- `vnext.book.3vrooms.app` - VNext staging.
- `localhost:4200` - Local frontend.
- `deploy.3vrooms.app` - ArgoCD deployments.

Common projects:

- `rooms` - 3V-ROOMS main product.
- `quickrooms` - Quickrooms Node.js frontend.
- `ngx-rooms-lib` - Angular rooms library.
- `argocd-config` - Infrastructure and GitOps.

Common apps:

- `Cursor.exe` / `Code.exe` - Code editors.
- `rider64.exe` - JetBrains Rider.
- `datagrip64.exe` - DataGrip.
- `GitExtensions.exe` - Git operations.
- `msedge.exe` / `zen.exe` - Browsers.
- `ms-teams.exe` - Meetings and chat.
- `mstsc.exe` - Remote Desktop.
- `ScreenConnect.WindowsClient.exe` - Remote support.
- `Signal.exe` - Messaging, work or personal depending on context.

## Improving Results

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
