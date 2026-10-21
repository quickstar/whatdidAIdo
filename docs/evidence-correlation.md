# Evidence correlation

Use this procedure for every initial worklog and re-evaluation, after reading
`config.json` and before estimating time. `AGENTS.md` owns session estimation,
billability and MOCO rules; the [T3 freshness gate](../README.md#freshness-gate-and-recovery)
owns acquisition checks. This procedure makes the interpretation exhaustive.

## 1. Establish the available evidence

Find the latest dated checkpoint, approved worklog and MOCO receipt in the
external review directory. Follow their output paths and archive manifests;
an older review can have been superseded by a nested re-evaluation. Keep the
approved total separate from a new estimate until the user approves a change.

Inspect the full saved collector JSON and GitHub audit, rather than relying
on `--ai` excerpts or the previous table. Extract messages and outcomes in
bounded batches while keeping the full exports on disk; exclude reasoning
and tool payloads from the human-work review. Resume valid completed
acquisition stages. Collect or recover only missing or invalid evidence under
the existing retry rules.

For each configured T3 environment, record the source identity, acquisition
time, requested-day messages, freshness result and unresolved gaps. Check all
date-relevant active and archived threads against the fetched full histories.
Inspect relevant inventory entries absent from those histories. A readable
local database covers its own environment; Windows pairing does not establish
that remote conversations are stored there.

**Done:** Every configured source is either verified under the freshness gate
or has a named gap, last known timestamp and concrete recovery prerequisite.

## 2. Account for human work across threads and hosts

Build one chronological list of requested-day human exchanges across the
selected chat source. Read each substantive prompt together with the relevant
assistant result and follow-up decision, including results reached through
another thread. Validate user-role origin and lineage: delegated prompts,
scheduled instructions, tool output and reasoning belong to agent execution.

Assign exchanges to a logical task using repository/workspace, ticket or PR,
explicit thread references, discussed artifact and actual message content.
Follow handovers after provider limits, separate review threads, copied review
results and implementation kickoffs. Current titles, branches and provider
metadata are hints; a misleading title must not override the conversation.

Keep a task together across hosts and devices. Split its customer, project,
billability or ticket attribution according to `AGENTS.md`, rather than making
one row per thread, provider or machine.

### Mobile participation and desktop gaps

T3 mobile uses the same server histories. Mobile prompts, technical decisions,
report feedback and explicit confirmations of a completed review are human
work evidence even while every desktop watcher reports AFK. Desktop interaction
is therefore neither a ceiling on attended work nor a prerequisite for using
a chat-supported session.

The user has confirmed using T3 mobile and threads on different hosts. Treat
mobile participation as a normal possibility on future dates. Preserve a
date-specific confirmation with its scope; it establishes neither attendance
through every silent gap nor the physical device behind each message. Keep
server provenance explicit, such as `T3 Orion server (client unknown)`.

For every task, inspect these exchanges as candidate work:

- Reading architecture, dependency, security or implementation reports and
  making technical decisions about them.
- Comparing screenshots, reference output, tests or a proposed migration plan.
- Reviewing a PR, correcting its scope, authorizing a fix, and coordinating a
  handover, merge or cleanup.
- Specifying or revising global skills, test infrastructure and working methods.

Count these as attended work only when supported; their existence alone does
not establish a duration. Establish their task attribution before considering
whether they justify extending desktop interaction.

**Done:** Every relevant human exchange is assigned to a task or explicitly
marked unresolved/excluded with a reason. Every task has a disposition in the
ledger below, including tasks with evidence but no supportable duration.

## 3. Reconcile GitHub and local git with those tasks

Use the canonical audit's complete repository, commit, push, PR and review
evidence. Correlate each distinct logical outcome with the conversations and
foreground evidence. Review-only work belongs in the same investigation as
authored code. Identify automated comments and merges from their content and
matching agent actions; an authenticated human login can also be used by an
agent.

Compare SHAs referenced in conversations, PR comments and push `before` values
with the hydrated audit. For a missing referenced SHA, inspect that repository
and object with a focused GitHub or local `git show` read. Preserve metadata,
body, files and patch beside the audit. Use known old heads and bases to bound
inspection of a rewritten branch's original commits. Report unavailable
objects as gaps rather than repeating successful repository discovery.

Group original, amended, rebased and squashed versions into the same logical
outcome. Where relevant, compare the old and replacement trees to distinguish
history cleanup from additional implementation. Keep distinct-SHA counts
separate from outcomes and attended duration.

An unmatched GitHub outcome remains visible as evidence-only or unresolved
until attributed. Commit density, merge timestamps and agent-authored reviews
can corroborate a human session; they do not independently establish its length.
Use a separate native Codex investigation only for an identified missing direct
task under the source-selection rules in `AGENTS.md`.

**Done:** Every discovered logical git outcome is attributed, evidence-only,
unresolved or excluded with a reason. Referenced missing commit objects and
automated activity are explicitly accounted for.

## 4. Validate candidate intervals globally

Consider reading, review, decision and handover phases as well as foreground
interaction. Correlate by task across the entire chronological list before
allocating time. For example, a plan completed in one thread at 19:57 and an
independent review requested at 20:24 can identify a previously missed plan
reading phase across threads.

Such timestamps bound a candidate, not a measured reading duration. Check the
completed response's `updated_at`, whether the later message responds to that
artifact, intervening unrelated work, evidence of absence and the unsupported
gap rule. Preserve the actual support and uncertainty for each accepted slice.
A result-completion-to-reply gap is not automatically continuous attendance.
An explicit "I reviewed it" establishes review work while leaving its start
and duration uncertain.

Exclude agent generation, CI/build waiting and autonomous execution. Mobile
use does not turn an overnight commit run or all time between prompts into
attended work. Resolve overlaps between different tasks before summing; use
the global union and apply the existing quarter-hour rounding rule afterward.

Keep short related phases when they can accurately join the same task,
customer/project and billability classification. Shared PR test-strategy or
handover review can be one non-ticket activity when the work itself is shared.
Ticket-specific implementation retains its own ticket attribution. Explain
omitted short tasks rather than silently combining unrelated work to reach
the MOCO minimum.

**Done:** Accepted intervals are nonoverlapping across tasks, every extension
has recorded evidence and confidence, and unrounded and rounded totals are
available. Explain material rounding sensitivity and unsupported durations.

## 5. Save the attribution ledger and complete the review

Save `attribution.json` beside the dated raw evidence. It is an interpretation
artifact created during review, not an output currently emitted by a collector.
Record:

- **Inputs:** canonical evidence paths, collection times and latest checkpoint;
  per-environment coverage and scoped user confirmations.
- **Tasks:** stable task identity, customer/project and ticket or PR context,
  sources, thread/message IDs, GitHub URLs and original/replacement SHAs.
- **Disposition:** `included`, `evidence-only`, `unresolved` or `excluded`, with
  reasons and references to the human exchanges and git outcomes covered.
- **Time:** accepted start/end slices, supporting evidence, confidence,
  unrounded union, rounded hours, and rejected or unresolved candidate phases.
- **Result:** proposed total, approved total if any, and separately the verified
  MOCO total and receipt path after authorized synchronization.

Before presenting the worklog, verify that the ledger covers the human
exchanges and git outcomes above, every row maps to one MOCO activity, and the
table matches its rounded total. Show useful evidence-only findings and source
gaps after the table, even when they cannot receive hours. Label a partial
estimate as partial; a complete evidence search can still leave attendance
duration uncertain.

When a user challenges a total, compare the ledgers and identify the missing
task, omitted phase, new evidence or corrected assumption. Preserve the prior
approval and explain the delta. A larger number is not itself a completion
criterion. After approval or MOCO changes, update the latest checkpoint's
approved-input and receipt paths so subsequent runs find the current result.
