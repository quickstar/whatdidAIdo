# whatdidAIdo

> You know what you did. Your AI does too.

An AI-powered worklog generator built on [ActivityWatch](https://activitywatch.net/). Open an AI coding agent such as Codex or Claude Code in the repo, ask *"what did I do yesterday?"*, and get a clean worklog table — no manual time tracking needed.

## How it works

```
ActivityWatch ─┐
T3 history ────┼→ Evidence audit → AI Agent → Worklog → MOCO sync
GitHub + git ──┘     (complete)    (interprets)  (approved) (idempotent)
```

1. **ActivityWatch** silently tracks your window activity, browser tabs, and AFK status
2. **T3 history** contributes conversations across providers and servers; native Codex is an explicit alternative for direct/legacy Codex work
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
- **Cross-server chat context** — Reads T3 server databases and desktop caches without SSH, with coverage warnings and deduplication
- **Idempotent MOCO sync** — Preserves existing entries by default, keeps durable local identity for untagged work, verifies totals, and checks Jira links after writes
- **Break detection** — Identifies gaps in activity (lunch, coffee, etc.)
- **Smart context** — Distinguishes work YouTube (tutorials) from personal YouTube based on surrounding activity

## Quick Start

### Prerequisites

- An AI coding agent that can run shell commands and read repository files, such as Codex or [Claude Code](https://docs.anthropic.com/en/docs/claude-code)
- Python 3
- ActivityWatch v0.14.0b3 or newer with `aw-server-rust` running and collecting data
- [GitHub CLI](https://cli.github.com/) authenticated for the repositories to audit

### Setup

Clone this repository and open its directory in your terminal or AI agent, then create a local config:

```bash
cp config.example.json config.json
```

Edit `config.json` with your details:
- Configure the local ActivityWatch API host, port, and worklog timezone
- Configure `t3.sources` and optional desktop cache; `chat_history.source` selects one chat source
- For direct/legacy Codex investigations, optionally set `codex_home`
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
python worklog.py today --chat-source none # ActivityWatch only
python worklog.py 2026-10-05 --ai --output worklog-evidence-2026-10-05.json # Collect once; reuse saved evidence
python worklog.py 2026-09-01 --ai --chat-source codex # Explicit legacy/direct Codex investigation, no T3 scan
python worklog.py --activitywatch-health # Read-only source/bucket diagnostics
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
| `activitywatch.host` | Local `aw-server-rust` API host; defaults to `127.0.0.1` |
| `activitywatch.port` | Local API port; defaults to `5600` |
| `activitywatch.timezone` | IANA timezone used for worklog day boundaries |
| `activitywatch.timeout_seconds` | Read request timeout |
| `codex_home` | Optional Codex data directory; defaults to `CODEX_HOME` or `~/.codex` |
| `chat_history.source` | `t3`, `codex`, `none`, or `auto` (configured T3 first, legacy Codex otherwise) |
| `t3` | Named server databases, environment IDs, and optional desktop client cache |
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

**Observed 08:30 - 17:15 | ActivityWatch interaction: 2.7h (Andromeda 2.1h; MacBook Pro 0.9h; 0.3h overlap) | GitHub activity through 21:27**

| Cat | Client/Ticket | Source | Description | Time |
|-----|---------------|--------|-------------|------|
| Dev | PROJ-1234 | Andromeda + MacBook Pro | Implement user authentication flow | 4.5h |
| Bug | BUG-5678 | MacBook Pro | Fix session timeout on login page | 45m |
| Mtg | Acme (Jane Doe) | Andromeda | Sprint planning | 1h |
| Review | PR #42 | GitHub (device unknown) | Review payment integration | 30m |
| Admin | — | Andromeda | Email, ticket triage | 30m |

Every interpreted worklog includes the evidence source. Activity recorded for
the same task on multiple devices remains one task row with all contributing
sources; overlapping intervals are unioned rather than added. Evidence such as
a GitHub event that cannot prove the physical device is labeled as unknown
instead of being assigned to a machine by assumption.

## How AI time estimation works

Raw detection times (how long a browser tab or window was in focus) don't equal actual work time. The AI uses multiple signals:

1. **App times** — Foreground IDE, terminal, and git-tool intervals provide session evidence
2. **Git branches** — Which ticket branch was active = where dev time goes
3. **Window context** — File names and titles confirm what was being worked on
4. **Chat context** — T3 human exchanges, repositories, branches, tickets, and outcomes explain the work
5. **GitHub audit** — Commit diffs, push/rewrite events, PRs, and reviews reveal sessions ActivityWatch missed
6. **Meeting duration** — Explicitly supplied calendar evidence can establish attended meeting time

A ticket might show 20 minutes of raw browser time while a coherent foreground,
chat, and commit sequence supports a longer development session. An application
merely remaining open is never enough to count the intervening gap.

### One chat source per run

With T3 configured, the default collects T3 only: it does not open the Codex state database or scan rollout files. T3 already includes conversations using the Codex provider. A T3 read failure remains a coverage warning, rather than silently triggering another history scan. `--chat-source` overrides configuration; `auto` preserves native Codex behavior for installations without T3 configuration. The older `--no-codex` and `--no-t3` flags only disable the selected source; they do not switch to the other one.

Direct Codex app/CLI conversations and older dates may still require `--chat-source codex`. That explicit alternative reads local root tasks and excludes native subagents/automations, without collecting T3. If investigating missing direct work after a T3 review, reconcile the result against saved T3 evidence before attribution. Neither titles alone nor overlapping spans prove two records describe different work.

Use `--output` to persist the complete canonical analyzer evidence, including T3 transcripts, source coverage, and collection time. Reuse that file and the GitHub audit JSON during interpretation and interrupted-run recovery. Repeat collection only for an identified gap, source recovery, implementation change, or permitted transient retry. `--t3-output` remains available for a T3-only evidence export.

Chat spans and message counts are not billable durations. T3 mode leaves session estimates to evidence-based interpretation of human activity; native Codex mode also prints a candidate evidence union requiring validation. Union accepted intervals, exclude background execution, round after attribution, and label uncertainty. Contractual billability comes from Jira/MOCO rules.

## ActivityWatch API and multi-device sync

The analyzer never opens ActivityWatch's private SQLite files. It reads
`/api/0/info`, `/api/0/buckets/`, and bounded bucket-event endpoints from the
local Rust server. Connection settings resolve in this order:

1. `--aw-host` and `--aw-port`
2. `AW_HOST` and `AW_PORT`
3. `activitywatch` settings in `config.json`
4. `127.0.0.1:5600`

`aw-sync` does not make OneDrive files directly queryable. On the central
machine it must first pull each device's staging database into the local
server. The analyzer then discovers window, AFK, browser, and editor buckets by
API metadata, reports their source machines, removes exact imported replicas,
and unions `not-afk` intervals across machines.

Some watchers and browser extensions preserve an old hostname or use a device
GUID as `$aw.sync.origin`. Configure `activitywatch.source_aliases` to map every
known label for one physical device to a canonical source. Aliases are resolved
case-insensitively before bucket coverage, exact-event deduplication, AFK union,
and provenance reporting. Health output retains the raw-to-canonical mappings
for auditability. Add the canonical names to `activitywatch.expected_sources`
to warn when a participating machine is entirely absent. The health command
also reports per-source bucket coverage and the latest API-visible timestamp.

Pull-only `aw-sync` still initializes an empty staging database for the central
server's device ID. It remains at zero buckets/events because local data is not
pushed, but deleting it is ineffective: the next pull pass recreates it. Keep
the small empty database and judge freshness from satellite staging files and
the imported API buckets, not the central staging file timestamp.

For a central collector, run `aw-sync --sync-dir <path> daemon --mode pull` only
on the central machine and `aw-sync --sync-dir <path> daemon --mode push` on
satellites. (`--sync-dir` is a global option and must precede `daemon`.) Do not use the default
bidirectional daemon for this topology.

It resolves the Codex data directory separately in this order:

1. `--codex-home` CLI argument
2. `CODEX_HOME` environment variable
3. `codex_home` field in `config.json`
4. `~/.codex`

## T3 Code conversation evidence

`worklog.py <date> --ai` also reads each database in `t3.sources`. Set `name`
to a stable server identity and `database` to that server's local
`userdata/state.sqlite`, or a consistent SQLite backup exported from it.
Never copy only a live database file while ignoring its WAL. Client pairing
does not mean that all server databases have been synchronized locally.

Declare every expected server, even before its database is accessible. A source
without a database produces an explicit coverage warning, rather than zero work.
The reader supports local databases and the desktop's cross-server IndexedDB
cache, not yet authenticated remote T3 connections. Refresh remote exports separately and inspect the reported latest
message timestamps; a readable database alone does not prove export freshness.

The reader includes user/assistant messages across providers, including Claude
Agent and Codex, and preserves server provenance separately from the unknown
physical client device. Exact migrated message copies are deduplicated;
conflicting versions are retained with a warning. Current thread branch and
provider are context hints, not guaranteed historical metadata. Messages still
streaming from a previous day are marked accordingly.

Use `--output <path>.json` to save full analyzer evidence (or `--t3-output` for
only transcripts), and `--chat-source none` to disable chat collection. These files can
contain private chat content. The compact summary prints prompts and recent
assistant excerpts; inspect the full export when excerpts omit relevant context.
User-role messages can include delegated prompts. Agent runtime, message counts,
and T3 turn spans are **not** added to work duration. Reconcile this semantic
evidence with ActivityWatch and git before assigning time. Native Codex is an
alternate collection mode, not a second routine source.

For desktop cache collection without SSH, install the optional reader with
`python -m pip install --target .t3-reader -r requirements-t3-cache.txt`. Set
`t3.client_cache.indexeddb_directory` to the desktop profile's `IndexedDB`
directory and `reader_path` to the absolute `.t3-reader` path. Each named source
needs its `environment_id` from that server's `/.well-known/t3/environment`
descriptor. Additional cached environments are reported by ID. A configured
server SQLite database takes priority over its potentially older client cache.
Each unique database is queried once per invocation; aliases reuse the result.
The client cache is scanned once, and JSON bodies for environments already read
from a server database are skipped. The underlying IndexedDB reader still scans
shared storage records; this is not a claim that every disk byte is read once.

Only the `shell` and `thread` object stores are read; the credential catalog is
excluded. LevelDB sequence numbers reconcile obsolete values and tombstones.
The cache is always marked partial: it can omit unopened and archived threads,
retain stale state, or hold only a paginated part of a conversation. Output
reports missing potentially relevant bodies and pagination. A live client can
write while this read-only inspection runs, so it is not an atomic live snapshot.
For complete collection, an authenticated API adapter must reconcile active and
archived thread inventories and fetch missing history. No SSH is inherently
required: T3 exposes authenticated HTTP and WebSocket read operations.

## License

MIT
