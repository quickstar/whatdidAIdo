# ActivityWatch Worklog Analyzer - Agent Guide

This repository contains tools for turning ActivityWatch, Codex, and git evidence into worklog summaries.

## Purpose

When the user asks questions such as "what did I do today?", "what did I do yesterday?", or asks for a worklog for a specific date, run the ActivityWatch analysis script and interpret its output into a concise worklog.

## Important Files

- `worklog.py` - Primary script. Reads ActivityWatch plus local Codex root-task history.
- `github_audit.py` - Audits GitHub, PR, push/rewrite, and local-git evidence.
- `moco_sync.py` - Idempotently dry-runs/applies an approved JSON worklog to MOCO.
- `worklog.example.json` - Input structure for `moco_sync.py`.
- `config.json` - Local configuration for database path, clients, contacts, correlations, projects, and ticket metadata.
- `config.example.json` - Template for new setups.

## Required Workflow

1. Read `config.json` first to understand clients, contacts, ticket prefixes, project mappings, known tickets, personal filters, and context hints.
2. Run the primary script for the requested date:

   ```bash
   python worklog.py <date> --ai
   ```

   On this Windows machine, use `python`, not `python3`.

3. Run the deterministic GitHub and local-git audit:

   ```bash
   python github_audit.py <date> --ai
   ```

   Use `--output <path>.json` when full commit bodies, file lists, patches, and
   PR associations are needed for detailed interpretation. The helper discovers
   all repositories before hydrating commits; do not replace it with a search of
   one known repository.
4. If the GitHub helper reports coverage warnings, investigate only those gaps
   with focused `gh api` or local git calls. Do not repeat successful discovery
   work manually.
5. For suspicious gaps, inspect the raw AFK and foreground-window intervals.
   Calls, reading, reviews, and agent supervision can be real work even when
   there is no recent keyboard or mouse input.
6. Use calendar evidence only when the user explicitly supplies it in the
   request or explicitly asks to use an available calendar. Never assume that a
   calendar is available and never invent meeting times from ActivityWatch.
7. Interpret all evidence together using the deterministic session rules below.
   Raw ticket detections, `not-afk` totals, GitHub event spans, and commit counts
   are signals, not final work duration or billability.
8. Return the final worklog as a markdown table with clickable Jira links.

## MOCO Time Entry Creation

When the user asks to create the interpreted worklog in MOCO:

- Treat the most recently presented worklog table as approved input. Do not
  silently re-estimate its tickets or durations while writing it. If newly read
  Jira or MOCO evidence creates a material conflict, stop and explain it.
- For every Jira activity, inspect the Jira issue before selecting the MOCO project. Use the Jira Service Management Organizations field (`customfield_10002`), reporter/customer name and email domain, description, all comments, and linked issues to determine the customer.
- For linked `ITEM-*` implementation work, inherit the customer attribution when the issue description or links establish that it originated from a customer `ROMSD-*` ticket, pilot, or request.
- Resolve the customer's year-specific project and task from `config.json`
  `moco.customer_projects`. If the mapping is absent or stale, search contracted
  MOCO projects, verify the active task (normally `Entwicklung`), and update the
  local mapping before synchronization.
- Always inspect the Jira issue type and content before setting billability. Customer-reported defects are non-billable: this includes Jira type `Bug`, service-desk `Incident` tickets that describe faulty product behavior, and tickets linked to such a defect. Log the time on the correct customer project but set the MOCO activity to non-billable. A project's default billability and a non-`Bug` Jira type must never override the actual defect classification.
- `INTERN-W&S ROOMS-<year>` is the global fallback only after confirming that the Jira evidence identifies no customer or that MOCO has no matching dedicated W&S project. Never select it merely because a ticket prefix or local config mapping is missing.
- Serialize the approved table using the structure in `worklog.example.json`.
  A ticketed activity must have exactly one Jira ticket. A meeting/admin entry
  without Jira must instead have a stable, descriptive `sync_key`. Every entry
  also needs explicit evidence-derived `customer` and `billable` values,
  description, and `hours` or `seconds`.
- Dry-run the idempotent synchronizer first, then apply the same file:

  ```bash
  python moco_sync.py <approved-worklog>.json
  python moco_sync.py <approved-worklog>.json --apply
  ```

  The helper reads existing activities before writing, matches by
  `date + remote_id` with a tag fallback, creates native Jira links, resolves
  `MOCO_API_KEY` without printing it, and verifies stored values after changes.
  If sandboxing hides the global Windows key or blocks the API, rerun the helper
  with elevated sandbox permission; never switch to browser automation for that
  reason.
- Existing activities are protected by default, even when their fields differ.
  Use `--update-existing --apply` only when the user explicitly requested that
  correction. Never delete and recreate an activity to repair it.
- Never create a MOCO activity shorter than 0.25 hours (900 seconds). Consolidate short related work when project, task, and attribution remain accurate; otherwise omit it or ask rather than creating a sub-15-minute entry.
- One MOCO activity supports one external ticket link. Prefer one activity per Jira ticket when separate ticket attribution is supported by the evidence; do not claim multiple ticket references in one description are all clickable.
- Never print or return the value of `MOCO_API_KEY`.

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
python worklog.py today --ai
python worklog.py yesterday --ai
python worklog.py 27.01.2026 --ai
```

## Script Output

The `--ai` output is compact and designed for interpretation by an AI agent.

It usually includes:

- Categorized summary with raw detection times.
- Meetings grouped by client using `contacts` and `correlations` from `config.json`.
- Jira tickets detected from browser URLs, window titles, and git branches.
- Codex root tasks with workspace, branch, ticket, completion context, task span, and non-AFK overlap.
- App times for IDEs, terminals, browsers, Teams, git tools, and other apps.
- Git branch activity with ticket extraction.
- Window context for files, features, PRs, tickets, and browser pages.
- Activity window, total active time, and breaks.

## Time Estimation Rules

Ticket times marked as raw are detection times only. Do not treat them as final work durations.

### ActivityWatch, Optional Calendar, and AFK Reconciliation

- ActivityWatch `Total Active` and `not-afk` measure recent keyboard/mouse
  interaction. Describe this as observed interaction time, not the duration of
  the full workday.
- In the absence of explicitly supplied calendar evidence, do not report an
  ActivityWatch-only total as a confirmed full-day total. GitHub pushes, commits,
  PR work, Codex history, and local git activity may prove additional sessions,
  but they do not by themselves establish the duration of every gap.
- If the user explicitly supplies calendar evidence, count an attended meeting
  for its full duration even when Teams is not foregrounded or ActivityWatch
  marks the user `afk`. Calculate the corrected baseline as the union of calendar
  meeting intervals and merged ActivityWatch `not-afk` intervals; never add
  overlapping meeting and interaction time twice.
- When using supplied calendar evidence, merge overlapping or back-to-back
  meetings before calculating totals. Work performed during a meeting may be
  mentioned for context, but it does not receive additional time unless
  independent evidence places it outside the meeting interval.
- If raw AFK data contains overlapping heartbeat/history rows, merge intervals
  before calculating overlap. Do not sum duplicate `not-afk` rows.
- A long foreground window with an `afk` status is not automatically work: static
  ChatGPT, Codex, browser, or Git windows may be background activity. Count such
  time only when explicitly supplied calendar evidence, surrounding foreground
  activity, user evidence, or git timing supports an attended work session. A
  visible lock screen remains strong evidence that the user was away.
- When explicitly supplied calendar evidence changes the result materially,
  report both values and explain the reconciliation. When event times are
  inferred from a screenshot, state that they are inferred unless the calendar
  grid makes the start and end times unambiguous.

Estimate development time from:

- IDE/editor time: `rider64.exe`, `Cursor.exe`, `Code.exe`.
- Terminal time when it belongs to the same development session.
- Git tool time such as `GitExtensions.exe`.
- Active git branch names and ticket IDs.
- Window titles, file names, and surrounding context.
- Codex root-task titles and outcomes for semantic attribution.

General rules:

- `ITEM-*` tickets are usually feature development. Attribute the relevant development session time to the dominant active ticket.
- `ROMSD-*` tickets are usually bug investigation or support. Use raw detection time only when the surrounding app/window context does not show a larger development session.
- If one ticket branch dominates a development session, assign the IDE, terminal, and git time for that session to that ticket.
- Codex task spans can overlap or continue in the background. Never sum them as billable time; use their titles/outcomes to identify the work and ActivityWatch `not-afk` plus git evidence to estimate duration.
- The analyzer excludes Codex subagents and automations. Do not reintroduce their durations manually unless independent foreground or git evidence requires it.
- If activity is ambiguous during an otherwise clear coding session, treat technical browsing as work-related.

### Deterministic Session Estimation

1. Build candidate intervals per ticket from merged ActivityWatch `not-afk`
   intervals, ticket-specific foreground windows, non-overlapping Codex root-task
   turns, explicitly supplied meetings, and GitHub/local-git timestamps.
2. Split a candidate session when there is more than 60 minutes with no
   supporting foreground, Codex-turn, git, review, or supplied-calendar evidence.
   Do not bridge the gap merely because an application remained open.
3. A Codex span may support the interval only when it has one dominant ticket and
   does not overlap unrelated user work. Split its internal turns using the same
   60-minute unsupported-gap rule; never count background waiting or overlapping
   tasks twice.
4. Commits, pushes, and review comments are anchors, not durations. Dense anchors
   may confirm continuity inside a candidate session, but a late squash or
   force-push does not make the preceding unsupported gap worked time.
5. Group rebased, amended, superseded, and squash commits into one logical
   outcome. Analyze every distinct SHA, but never allocate time again for the
   replacement commit.
6. Take the union of accepted intervals across tickets so the daily estimate
   cannot double-count concurrent activity. Round final ticket estimates to the
   nearest 0.25h only after union and attribution.
7. Label confidence as `high` when foreground/Codex and git evidence agree,
   `medium` when a coherent task and dense repository evidence fill ActivityWatch
   gaps, and `low` when only sparse anchors exist. Use `~` for medium/low time and
   explain low-confidence entries after the table.
8. Determine contractual billability from Jira and MOCO rules only. There is no
   percentage-based billable-time heuristic.

Example:

```text
Raw output shows: ITEM-3049: 26m raw
App Times show: rider64.exe 1.3h, Cursor.exe 21m, Terminal 2.6h, Git 33m
Git Branches show: ITEM-3049 branch was active
Window Context shows: Translation Caching work

Estimate: ITEM-3049 = 4.5h
```

## GitHub and Local Git Audit

`github_audit.py` is the canonical discovery path. It:

- Resolves the authenticated login and exact `Europe/Zurich` day boundaries,
  including 23/25-hour DST transition days.
- Searches adjacent UTC dates by author and committer, then filters timestamps
  back to the requested local calendar day.
- Combines commit search, authenticated user events, PR involvement and commit
  lists, exact `PushEvent` comparisons, and every direct git repository under the
  configured local repository root.
- Inspects every discovered SHA through GitHub or local `git show`, retaining
  message/body, timestamps, parents, statistics, changed files, patches, PR
  associations, and discovery evidence.
- Deduplicates SHAs, records diverged push rewrites, marks superseded heads, and
  distinguishes GitHub-visible from local-only work.

Use the compact output for routine worklogs and save full JSON when the changes
need detailed semantic analysis:

```bash
python github_audit.py <date> --ai
python github_audit.py <date> --ai --output github-audit.json
```

Coverage warnings are actionable. Events have limited retention and APIs may be
inaccessible or truncated; investigate the reported repository/SHA only. Do not
fall back to GitHub's contribution calendar as proof of no work, and do not
repeat the entire audit manually when only one seam failed.

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
Observed 08:30 - 17:15 | ActivityWatch interaction: 2.7h | GitHub activity through 21:27
```

Without explicitly supplied calendar evidence, do not turn gaps between these
anchors into invented hours. State the observed interaction total and the wider
evidence window separately.

When explicitly supplied calendar evidence changes the total, identify that
explicitly:

```text
08:30 - 17:15 (7.5h work, calendar-adjusted) | Meetings: 10:00 - 12:00 (2h)
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
Include a compact GitHub audit note naming the repositories covered, the number
of distinct commits inspected, any rewritten/superseded commits, and material
PR/review-only activity. State confidence for inferred time; `high` can be
implicit, while `medium`/`low` must be named and briefly justified.

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

The `github` section controls login, timezone, local repository discovery, and
git author matching. The year-specific `moco.customer_projects` cache stores
confirmed customer project/task IDs and display names. Update it when a new year
starts or MOCO replaces a project/task; never infer customer or billability from
the cache alone.

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
- `ChatGPT.exe` / `Codex.exe` - Codex tasks; use local task history to identify their work context.
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
