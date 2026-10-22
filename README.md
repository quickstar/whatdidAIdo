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
- **Cross-server chat context** — Reads local T3 databases and authenticated remote histories without SSH or opening threads, with cache fallback and coverage checks
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

### Correlate evidence before estimating

Follow [Evidence correlation](docs/evidence-correlation.md) for every initial
worklog and re-evaluation. It covers mobile T3 participation, task handovers
across threads and hosts, reading and review decisions, superseded commits,
and the attribution checks required before presenting a total. Save its
attribution ledger beside the dated collector outputs.

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
`userdata/statev2.sqlite` for T3 v0.1 and later (`userdata/state.sqlite` for
older installations), or a consistent SQLite backup exported from it. The
reader prefers populated v2 conversation projections over frozen legacy
projections and retains message origin and thread lineage for distinguishing
human exchanges from scheduled prompts and delegated work. After upgrading
the desktop, also verify the configured cache directory; v0.1 uses
`AppData/Roaming/t3code-v2/IndexedDB` on this Windows installation.
Never copy only a live database file while ignoring its WAL. Client pairing
does not mean that all server databases have been synchronized locally.

Declare every expected server, even before its history is accessible. The reader
supports local databases, authenticated remote reads and the desktop's
cross-server IndexedDB cache. An unavailable source produces a coverage warning,
rather than evidence of zero work. A readable database alone does not prove
that an exported backup is current.

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

### Setup and acquisition

For desktop cache and live API collection, install the optional dependencies with
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

The IndexedDB reader reads only the `shell` and `thread` object stores.
LevelDB sequence numbers reconcile obsolete values and tombstones.
Current raw records and blob references are selected before deserialization,
so removed blobs belonging to obsolete records do not break current history.
Both legacy thread snapshots and v2 projection/message snapshots are supported;
v2 history pagination and listed archived threads remain coverage checks.
The cache is always marked partial: it can omit unopened and archived threads,
retain stale state, or hold only a paginated part of a conversation. Output
reports missing potentially relevant bodies and pagination. A live client can
write while this read-only inspection runs, so it is not an atomic live snapshot.
For remote sources, enable `t3.live_http` to collect without opening threads or
using SSH. `t3_live.py` reuses the existing Windows desktop pairing from
`catalog` (the desktop `connection-catalog.json`) and `local_state` (the desktop
profile's `Local State`), with the same optional `reader_path` dependency folder.
Windows must permit the running user to unlock that pairing. Each source's
`name` must exactly match the paired server hostname, and `environment_id` must
match its saved environment. Credentials are decrypted only in memory and are
excluded from evidence and error messages; redirects are refused.

The adapter reads the active inventory and full thread snapshots over HTTP GET,
and archived inventory through the read-only
`orchestration.getArchivedShellSnapshot` WebSocket RPC. It fetches date-relevant
threads across providers and saves full projections in
`t3_history.live_evidence` in the main `--output` JSON
(`live_evidence` at the root of a `--t3-output` export).
The local server database remains authoritative. Remote coverage is
`live_readable` only when both inventories and all candidate thread reads
succeed; authentication, network, archive, or transcript failures remain explicit
coverage warnings. The server must be reachable. Collection observes live
snapshots, not an atomic transaction across servers.

### Freshness gate and recovery

Before interpreting every new or revised worklog, run the canonical
`python worklog.py <date> --ai --output <absolute-path>.json` to refresh locally
saved evidence across all configured T3 environments. Assume distributed work
on multiple computers and T3 mobile as required by `AGENTS.md`. An earlier
review's coverage assessment does not replace this refresh, especially after
source recovery.

The configured live adapter runs automatically: read the live local database,
fetch current active and archived remote inventories, and acquire all
date-relevant full histories before attribution. Verify every environment
below; local readability alone does not establish cross-server coverage. This
updates the collector's local evidence, not T3's private IndexedDB or server
database. There is no assumed central transcript replication or invented T3
sync command, and opening the desktop or selecting threads is unnecessary.
Resuming the same acquisition reuses valid completed stages. A MOCO-only write
uses the approved table without refreshing or re-estimating its activities.

Before using saved evidence, check its `date`, `timezone`, `collected_at`,
`t3_history.warnings`, and every `t3_history.sources` entry:

- Local `readable` means the configured database was read. Confirm whether it
  is the live server database or an exported backup.
- Remote complete acquisition requires `status == "live_readable"`,
  `live_http.archived_verified == true`, `live_http.complete == true`, and an
  empty `live_http.failed_thread_ids`. Inspect `requested_threads` against the
  candidate inventory and all remaining warnings.
- Check `requested_day_messages`, `latest_message_at`, `shell_updated_at`,
  `possibly_relevant_uncached_threads`, and `truncated_thread_ids`. A null shell
  timestamp is unknown, not a fabricated collection time. Remote latest-message
  time describes the histories fetched for this review, not necessarily every
  message on the server.
- Reconcile zero requested-day messages or old timestamps with human activity,
  relevant inventory, and scoped user confirmations. A current title alone does
  not prove that its transcript was collected. Cache-only, unresolved paginated,
  archive-unverified and failed sources remain partial.

Within the current acquisition, resume valid completed stages from saved JSON
and checkpoint paths. For a
reported gap, inspect only configuration, reader dependencies, Windows pairing
access and the affected server's reachability. Apply the scheduled workflow's
one transient retry limit, retaining stdout/stderr, exit status and retry count.
After source recovery or a reader fix, rerun the affected canonical helper and
reconcile the replacement evidence. Avoid duplicate running helpers and
regenerating delivered reviews merely to test collection.

If restoration fails, deliver the useful partial evidence with the affected
source, last timestamps and concrete restoration step. Request user action only
for an unmet prerequisite that cannot be repaired autonomously. The scheduler
host/server must run, remote servers must be reachable, and the scheduled
Windows account must be able to unlock the saved pairing. Keep tokens in memory;
never print or persist credentials in evidence or diagnostics.

`T3 live authentication unavailable (RuntimeError: Windows could not unlock the
existing T3 pairing ...)` concerns the collector's process context. It runs
inside the T3 server process, which the `T3 Code Server` Windows task starts.
With logon type S4U (Task Scheduler's "Do not store password"), that logon has
no user DPAPI credentials. Unlocking then depends on a cached key and is
intermittent: on 22 October 2026 it failed at 06:02 and 07:37 and succeeded at
07:52 in the same process. Since then the task stores the password (logon type
`Password`). If the warning returns, check that the task was not reset to S4U
and re-enter the password after a Windows password change; meanwhile treat it
as transient and rerun the helper once later. T3's app-owned thread tools reach
only the local server environment and do not replace the remote read.

A cached thread absent from a verified live active and archived inventory was
deleted on its server after caching. Keep its cached requested-day messages as
evidence with cache provenance.

### T3 tools and CLI

Use T3's app-owned thread and scheduler tools for conversation/schedule actions;
use `worklog.py` for evidence collection. The installed T3 v0.1.0 CLI's
`--help` lists server/service/project/auth commands, but no transcript-export or
cache-refresh command. Check the actual installed `--help` after upgrades
instead of inventing `t3 threads`, `t3 sync`, or equivalent commands.

In PowerShell, use `Get-Command t3` to locate an installed launcher, then
`t3 --help`; invoke an explicitly resolved executable path with the `&` call
operator if the launcher is absent. Use the execution tool's working-directory
option when necessary, never `cd` or `git -C`. Running bare `t3`, `t3 start`, or
`t3 serve` starts a server; it is not a history refresh. `t3 app` opens the UI
and does not establish transcript completeness. Pairing/auth commands can mint
or display credentials and are not routine read diagnostics.

If app-owned tools are missing in an ACP session and `T3_ACP_MCP_NODE` is set,
use the session's documented `acp-mcp-call` transport with its supplied runtime
and optional entrypoint. That is an orchestration fallback, not the transcript
collector. Follow the current injected tool schema rather than assuming CLI
subcommands or flags are stable.

## Generated files and local state

Save raw collector JSON, helper logs, attribution ledgers, approved worklogs and
temporary analysis scripts outside the checkout; the review text lives in its T3
thread. On this collector, use
`C:/Users/Lukas/AppData/Local/T3CodeServer/data/worklog-review/reviews/<date-or-range>/`
and pass absolute paths to `--output`. Keep historical evidence and MOCO
receipts so interrupted runs can resume and delivered dates are not duplicated.
Archive manifests in that state directory map older repository paths to their
new locations.

`config.json`, `.moco-sync-state.json` and `.t3-reader/` are ignored local runtime
files/dependencies and remain in place. In particular, preserve the MOCO ledger.
The ignore rules for generated reports are a backstop; new outputs belong in
the external state directory.

## License

MIT
