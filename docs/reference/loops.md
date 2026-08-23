# The six loops

Cold storage. One row of prose per loop was all v2 ever had; this file is the
definition the runtimes are built from, so an audit has something to read.

Five of the six are **LLM-free** and run in Actions. The one that costs money
is `pickup-loop`, and it is off.

| Loop | Runtime | Cadence | LLM | Authority |
|---|---|---|---|---|
| `gate-loop` | Actions — `gate.yml` + `test.yml` + `guard.yml` | every PR, every push | none | may fail a build; may not merge |
| `review-loop` | a session, on request — `/code-review` | per PR | yes, owner-initiated | reports; may not commit |
| `triage-loop` | Actions — `groom.yml` label-hygiene job | weekly + on demand | none | warns; may not label |
| `groom-loop` | Actions — `groom.yml` hot-path job | on any `CLAUDE.md` change, weekly | none | fails the build |
| `pickup-loop` | one Windows scheduled task per repo root, each **disabled** | hourly when enabled | yes | branch + commit + PR; merges only via `automerge-loop` |
| `automerge-loop` | Actions — `automerge.yml`, plus the `reconcile` job in `digest.yml` | every PR event, plus daily | none | turns on native auto-merge for a `gate:machine` PR; may not merge a `gate:taste` one; labels a merged sortie `state:done` and closes it if `gate:machine` (ADR-0025); a `gate:taste` row stays open for the owner |

## gate-loop

**Trigger:** a pull request, or a push to any branch.
**Does:** runs the test suite (`test.yml`), the substrate tests and `qops
doctor` (`gate.yml`), and the tripwire + doc-link scans (`guard.yml`).
**Acceptance check:** every applicable machine gate is green before a taste
review is requested. That is S4, and S4 counts review requests that arrive
without it.
**Failure mode it exists for:** a taste review spent on something a script
could have rejected.

## review-loop

**Trigger:** the owner, on a PR whose machine gates are green.
**Does:** `/code-review` — Standards and Spec as two parallel read-only
subagents, so neither pollutes the other's context.
**Acceptance check:** findings are reported as `path:line`, and the reviewer
never edits.
**Why it is not automated:** it is a taste gate. Automating the *request* is
S4's job; automating the *judgement* is not wanted.

## triage-loop

**Trigger:** weekly, and on demand.
**Does:** lists every open issue missing `type:` / `state:` / `gate:`.
**Acceptance check:** the list is empty.
**Deliberate limit:** the loop itself — the mechanical, LLM-free Actions job —
**warns and does not label.** It has no judgement to guess with. That limit
was, until #47, read as also fencing the `triager` role out of every label;
ADR-0026 made `gate:` decidable from the row alone, so a session-invoked
triager now writes `gate:` and `type:` and reports what it refused
(`.claude/agents/triager.md`). Nothing invokes the triager unattended — that
is `triage-loop`'s own limit, unchanged — so the two statements no longer
contradict: the loop still never labels, the role now may, when a session
calls it. `ready:auto` and `no-auto` are never applied by either.

## groom-loop

**Trigger:** any change to `CLAUDE.md`, plus weekly.
**Does:** fails the build if `CLAUDE.md` exceeds `claude_md_max_lines` (150).
**Acceptance check:** the cap holds.
**Why it is load-bearing rather than hygiene:** `CLAUDE.md` is the larger half
of the measured daily saving, and it grew ~10 lines/day for a month while the
cap was written down and unenforced. Unchecked, the cap is re-breached in about
three weeks. `tests/test_qops.py` asserts the same thing locally, so a breach
fails before CI sees it.

## pickup-loop

**Trigger:** hourly, **only when the task is enabled. It ships disabled.**
**Does:** picks the least-recently-updated issue carrying `state:planned`, a
real gate (`gate:none` is not one) and no `no-auto` / `blocked` flag, then
starts a session on it. Two routes make a row eligible (ADR-0023): it carries
`ready:auto` (the owner's explicit grant, the only route for an `origin:agent`
row), **or** it is `origin:owner` with a body naming a test and no `gate:taste`
— the filing itself is the grant there, so no label is written.
**Acceptance check:** it branches, commits, opens a PR and stops there. It
never merges by hand, never activates a listing, never pushes to `master`.
**And when there is nothing to build it plans one row instead** (#82, below),
which is what stops a full backlog reading as an idle queue.

### Registration — `qops install` renders it (ADR-0032)

One task per repo root, **registered disabled**, and it is rendered from
`.qops/config.yml` exactly like a workflow. It used to be the only part of qops
with no installer: the definition lived on the cron host, named that machine's
Python and that machine's checkout, and sat at the root of the task namespace
under `qops-pickup-loop`, one per machine — so a second project installing qops
replaced the first project's loop in silence (#12). A code change had already
invalidated the hand-made definition once (the source repo's #176) and nothing
noticed, because the task was off.

```
python -m qops install                    # renders the workflows AND the task
python -m qops install --unregister-task  # removes it, leaving no orphan
```

| | |
|---|---|
| **Name** | `\qops\<project>\pickup-loop`, `<project>` from the config. A folder, so `Get-ScheduledTask -TaskPath '\qops\*'` lists every project's loop at once |
| **Command** | `python:` from the config (`py -3`), the root's `scripts/qops_pickup.py`, `--root <root>`, and `--launch` only when `pickup_launch: true` |
| **WorkingDirectory** | the root |
| **Trigger** | once at 07:23, repeating hourly |
| **State** | disabled on a fresh registration; a re-install never enables, and never disables one the owner enabled |

- **`--root` is the point.** `find_root()` walks up from the working directory
  and returns that directory when it finds nothing, so a task with no root and
  no WorkingDirectory reads whatever tree the scheduler started it in. With two
  roots on one host that is the wrong backlog or no backlog, and the picker
  exits 0 either way. `repo_root()` refuses a root holding no
  `.qops/config.yml` and names where the root came from.
- **`Register-ScheduledTask`, not `schtasks /create`** — `schtasks` cannot set a
  WorkingDirectory, which is how the empty one got there.
- **Registering never enables.** ADR-0009's cost argument rests on the expensive
  loop being off unless the owner turned it on; an installer that helpfully
  starts it is a bigger defect than the one being fixed (#12). Turning it on
  stays one `Enable-ScheduledTask` and not a build.
- **`--launch` is off by default.** The flagless form prints what it would have
  picked and spends nothing, so the schedule can be proved to fire without
  starting an agent. It was baked into the hand-made task, where the dry run
  was unreachable from the schedule.
- **`qops doctor` checks it.** A registered command that no longer matches what
  the config renders is a problem; the enabled/disabled state is *reported* and
  is never a problem and never changed. On a host with no scheduler the query
  answers unknown rather than clean.
- **The old flat task is not migrated by code.** A machine that still holds
  `qops-pickup-loop` or `qops-pickup-loop-<project>` fires two pickers at one
  root; removing it is one `Unregister-ScheduledTask` by the owner.

### It answers a clarification when there is nothing to build (#85, ADR-0029 §5)

#83 makes a stuck planner file a `type:research` clarification and block the
parent — that terminates the planner's own idle, but nothing cleared the
clarification itself, so the backlog filled with questions nobody answered
and the loop idled behind them, one label further along. A `--launch` run
that finds nothing to build answers **one** clarification instead, ahead of
planning: the loop clears its own debt before it takes a new row.

- **A clarification is a fact on the row, not the label alone.** `type:research`,
  not `no-auto`/`blocked`, and its *native* parent (the same sub-issue edge
  `qops/reconcile.py:derive_origin` reads the other side of, #81) is
  `state:blocked` (`clarification()` in `scripts/qops_pickup.py`). An ordinary
  `type:research` sortie with no such parent stays on the plan/build path.
- **Runs ahead of the plan pass, behind the build pass.** Building still wins
  over everything (#82); a clarification and a plannable row never compete for
  the same run.
- **One clarification per pass**, same reasoning as the plan and decompose
  passes: the owner's review attention is not spent in a single burst.
- **Two outcomes, both correct.** Answered: the answer is appended to the
  parent's body, `state:blocked` comes off, `state:triage` goes back on, and
  the child closes — the parent returns to the planner on a later pass. Taste:
  the parent gets `gate:taste` and *stays* `state:blocked` — `plannable()`
  already keeps a `state:blocked` row out of the planner's reach, so no new
  label or predicate does that work.
- **What it never writes**: `ready:auto`, `no-auto`, on either issue. A session
  that cannot honestly answer and cannot honestly call it taste writes nothing
  and stops — the same refusal `plan_prompt()`'s unplannable clause already
  licenses, moved one row across.
- **Success is measured, not assumed**: the child is closed, the parent's body
  grew, and the parent carries exactly one of the two label shapes above
  (`produced_answer()`). Anything else releases and spends the row's #49
  strike budget, same account a failed plan spends.

### It plans when it has nothing to build (#82, ADR-0029 §1)

`state:triage -> state:planned` was the last act in the chain that only an
owner session performed, so the backlog could hold 18 rows and the loop still
report an idle queue — the rows were filed, and nothing turned them into work.

A `--launch` run that finds nothing to build, and nothing to answer either
(#85, above), plans **one** row instead: the least-recently-updated
`state:triage` row that passes the filing bar, through the `planner` role
(`.claude/agents/planner.md`), with the toolset and model `.qops/config.yml`
declares for it.

- **Building is never starved by planning, and answering runs ahead of it.**
  The plan pass runs only where the run would previously have stopped —
  nothing eligible to build, nothing to write eligibly, and nothing to answer.
- **One row per pass.** Planning the whole backlog in one burst spends the
  owner's review attention all at once, and a wrong planner would spend it
  before anyone read the first plan.
- **What it refuses**: a row stating no outcome (the filing bar — a plan cannot
  invent criteria the owner never licensed), a `type:epic` (an interview and
  #84's decomposition, never a plan instead of one), and anything carrying
  `no-auto` or `blocked`.
- **What it never writes**: `gate:`, `type:`, `ready:auto`, `origin:`. The
  planner appends a plan and sets `state:planned`, exactly as #55 left it.
- **Same budget, same account.** It writes the same `pickup` ledger event, uses
  the same run log, and a row it fails to plan three times strikes out under
  #49 like any other. A failed plan does **not** relabel the row: the build
  path's release writes `state:planned`, which here would assert the very thing
  the run failed to do.
- **Success is measured, not assumed**: `state:planned` *and* a body that grew.

### It decomposes an interviewed epic when there is nothing to plan (#84, ADR-0029 §4)

The interview stays the owner's — ADR-0017 still routes `type:epic` to *"Mission
- interview before any issue is written"*. What happens under it does not: once
an epic's interview has ended in an ADR, cutting its scope into sorties is
automatic.

The trigger is a fact on the row, never an assumption that a `type:epic` row
was interviewed just because it exists: the epic's body must name an ADR file
(`docs/adr/*.md`) that actually exists in the repo — the interview skill's own
rule that it "ends in something written down". A `type:epic` row with no such
reference is skipped and says why (`interviewed()`, `decomposable()` in
`scripts/qops_pickup.py`).

- **Runs one step further down the same fallback** the plan pass uses: only
  where a `--launch` run found nothing to build *and* nothing to plan.
- **One epic per pass**, same reasoning as the plan pass.
- **Through the `planner` role's toolset**, not a second one — filing a child
  is `gh issue create`, which the planner's `Bash` already reaches, and a new
  agent role is a `.claude/` write this loop is not licensed to make.
- **Children are filed `origin:pending`, never `origin:owner`.** The native
  sub-issue link to the epic is what a later `qops reconcile` pass
  (`qops/reconcile.py:derive_origin`, #81) turns into the epic's own `origin:`
  — inheritance is derived from a structural link, never claimed by the filer.
- **Dedup is the sub-issue link itself.** An epic that already has one is
  skipped: a second pass over the same epic files no duplicate children
  (`first_decomposable()`).
- **The epic itself is untouched apart from the links** — no label, no body
  edit. A failed decompose does not relabel it, same reasoning as a failed
  plan.
- **Success is measured, not assumed**: the epic's sub-issue count grew.

### The reviewer's verdict rides this run (#80)

A `--launch` run, after it has picked, judges every **ready** (non-draft) open
PR whose head SHA has no verdict comment yet: it reads the row the branch names
and `gh pr diff`, asks `claude -p` the one question ADR-0028 §4 states, and
posts the answer as a PR comment carrying `<!-- qops-reviewer:<sha> -->`. CI's
`reviewer.yml` then reads that comment and exits on it.

- **Why here.** CI cannot reach the Claude subscription — credential resolution
  ends at an interactive browser login or a short-lived token — so a reviewer in
  CI is a metered API key and a second cost line that grows exactly as this loop
  gets busier. The subscription is on this host.
- **Why not a second task.** A registration is a hand-made machine fact the repo
  cannot see, and #12 is the standing evidence of what that costs. The command
  line above is unchanged; the verdict pass rides the run it already starts.
- **A comment, never a commit status.** `gh api -X` against repo settings is
  denied by a taken decision (ADR-0016/0020). `gh pr comment` is a plain verb.
- **The SHA is load-bearing.** A verdict on an older commit would authorise
  whatever was pushed after it. A verdict for another SHA is no verdict.
- **Nothing here repeats.** One commit is judged at most once: the verdict
  comment is the record, and a PR waiting days on the owner is read as judged,
  not re-reviewed. A review that *fails* is retried at most `MAX_ATTEMPTS` (3)
  passes for that commit — counted in the ledger, the way `pickup-loop` counts
  strikes — then the host says so on the PR and goes quiet. A push is a new SHA
  and a fresh count, which is the only way to get a new verdict.
- **A sleeping host is a fail-open, not a hang.** No verdict for this SHA is
  green *and says so*, which is what makes it safe for this check to become
  required later. Turning it on is the owner's act, after a week of real
  verdicts — the row is `no-auto` for that reason.

**Reading the log: an idle queue and a broken picker both exit 0.** Every run
names the root and the tracker it read before it says anything else, so:

| Log | Meaning |
|---|---|
| `root <path>, tracker <owner/name>` then `nothing eligible` | healthy and idle — it read the right backlog and nothing qualified |
| `root <path>` naming the **wrong** repo, then `nothing eligible` | wrong task definition; the root came from the working directory, not `--root` |
| `<path> is not a qops root` | the root could not be resolved at all — exit 1, not a quiet 0 |
| `could not read the backlog … UNKNOWN` | `gh` failed (auth, network, rate limit) — exit 1. Never printed as `nothing eligible` |
| `nothing eligible` on a repo whose labels were never created | the query itself returns empty. `scripts/qops_import.py --labels` is what a fresh repo runs first |

**Observed end to end in THIS repo, 2026-08-19 — criterion 8.** #5: launched
11:47:57Z, claimed `state:planned` -> `state:building` 11:48:14Z, branched
`fix/5-state-review-label-swallowed` (ADR-0019's shape, a commit type and not a
label), one commit, PR #18 carrying `Refs #5`. `automerge-loop` **queued** native
auto-merge and the PR sat `BLOCKED`; the required checks went green at
11:49:48-11:49:52Z and GitHub merged it at 11:49:54Z. `qops reconcile` advanced
the row to `state:done` and dropped `ready:auto`. **Two minutes, no human
keystroke between the pick and the reconcile.**

Three things it does *not* prove, stated because a criterion that swallows its
own caveats is not a criterion:

- **The schedule is still unexercised.** The task is registered
  and **disabled**; this run was hand-launched and watched, because #9 is open.
- **The reconciler was dispatched, not scheduled.** `advance` did not fire, for
  the documented reason: GitHub raises no workflow run from an event its own
  `GITHUB_TOKEN` caused. The `reconcile` job in `digest.yml` is the backstop and
  has still never run on its own cron.
- **The subject satisfied the size rule.** R8 held, which is evidence the rule
  works rather than evidence the loop survives a subject that breaks it.

What it *does* prove, and this is the part the previous two attempts could not:
the merge waited for the gate. Every required check completed before the merge,
two seconds before it. On this repo's second-ever PR the same job merged ten
seconds *ahead* of its gate (#3) — so this run is the first observation of the
mechanism ADR-0020 always claimed.

**Observed end to end in qhoto_printshop, 2026-08-19 (attempt 3).** #160: claimed 08:28, branched
`fix/160-schema-drift-doctor-guard`, one commit, PR #166 carrying `Refs #160`
at 08:30:59Z — 2m36s launch to PR — auto-merged green at 08:40:15Z, and the
reconciler advanced the row to `state:done` within a minute of being dispatched.
No human touch between the launch and the reconcile. **Criterion 8 is met on
this repo**; `docs/2026-08-19-attempt-3-findings.md`. Two things it does *not*
prove: that a subject which breaks the size rule survives (the rule is what made
this one work), and that an *enabled* schedule is safe — the task stayed
disabled and the run was watched, because #152 is still open.
**Amended 2026-08-19:** the acceptance check reads "it branches, **commits**,
opens a PR" and the failure check now reads the same way. It used to accept a
bare branch as evidence of work, and on 2026-08-18 both unattended sorties
(#57, #71) wrote their entire change, backgrounded the full test suite (~3.5
minutes, longer than one Bash call may run) and ended the turn waiting for a
completion notification that a `claude -p` run can never receive — the process
exits with the turn. Each left a branch pointing at `master`'s tip, which
`produced_work()` scored as success, so neither claim was released and neither
issue said anything had gone wrong. `produced_work()` now counts commits ahead
of the default branch, and the launch prompt tells the sortie to run only the
tests it touched and never to wait on a backgrounded command — `test.yml` is
the full-suite gate, and it runs on the push.

**Amended 2026-08-18 (#151):** the criterion used to say "and requests review".
It was unsatisfiable — GitHub rejects a self-review request and this repo has
one collaborator — and it was already obsolete under ADR-0020, where the gate
*is* the review for `gate:machine` and auto-merge refuses a `gate:taste` PR
regardless. The waiting-on-you signal is now a label: `automerge-loop` puts
`state:review` on the issue of any PR it declines to auto-merge, and
`digest.yml` renders those as a *Waiting on you* section. A label something
writes and something else reads is a control; a clause nobody can satisfy is
not.
**Amended 2026-08-16 (ADR-0020):** its PR may still be merged, by
`automerge-loop`, if the issue is `gate:machine` and every required check is
green. `pickup-loop` itself gained no authority — it opens a PR and stops; the
merge is a separate loop with its own conditions, and neither can merge a
`gate:taste` PR.
**Every eligibility condition is the owner's to grant.** `ready:auto` is granted
by the owner alone; the triager is forbidden from applying it.
**Runtime note:** `scripts/qops_pickup.py` without `--launch` prints what it
would pick and starts nothing, which is how the wiring is proved without
spending anything.
**Amended 2026-08-16 (#122):** the first acceptance run read for 62 seconds and
wrote nothing — the launch carried no permission mode, so every branch and edit
waited on an approval nobody was there to give. Three repairs:

- **A scoped launch grant.** `--permission-mode acceptEdits` plus
  `--allowedTools` set to the *coder role's* toolset and no wider. It removes
  the interactive prompt; it widens nothing. The PreToolUse guard and branch
  protection remain the controls, and a blanket bypass
  (`--dangerously-skip-permissions`) is asserted absent, not merely omitted. If
  the grant later needs a per-role shape, that is #123 arriving.
- **The claim is released on failure.** A non-zero exit, *or* an exit with no
  branch and no PR, reverts `state:building` → `state:planned` and comments why.
  The 62-second run exited 0, so exit code alone would have kept the door shut.
  **Amended 2026-08-22 (#93):** the comment now carries the tail of that run's
  log (bounded by `RELEASE_TAIL_CHARS`), not just the reason and the log path —
  #82 burned all three strikes silently, and the one thing that explained the
  refusal never left the host until then. Deduped like `report_unlaunchable()`:
  a marker names the run's log, and a retry that fails the same way twice still
  gets two comments, because it is two runs.
- **The strike-out comment's own remedy now works (#99).** Three strikes apply
  `no-auto` and tell the owner "remove `no-auto` to hand it back" — but
  `struck_out()` only counted `pickup_release` events in the ledger, so
  clearing the flag changed nothing it read and the row stayed unreachable for
  the rest of `STRIKE_WINDOW_DAYS`. `strikes()` now starts counting after the
  row's last `pickup_struck_out` event whenever `no-auto` is absent from it: no
  new label, the flag stays the one signal, and a row nothing can build still
  cannot be re-picked without an owner act. The same fix corrected the final
  line a pass prints when every eligible row was struck out — it used to fall
  through to the `unwritable`-path message (#48), naming the wrong cause.
- **No sandbox escape unattended.** The denied session retried with
  `dangerouslyDisableSandbox`. The launch sets `QOPS_UNATTENDED=1` and `qops
  guard` refuses that flag when it is set. An owner at a keyboard may still
  make that call; a loop with nobody reading may not.

**Amended 2026-08-23 (#9):** the launch no longer runs with `cwd=ROOT`. A
sortie branches, so a launch sharing the owner's checkout switched the branch
underneath a live owner session — observed once, harmless only because the
tree happened to be clean. `loop_worktree()` gives the launch its own
persistent worktree at `.qops/wt/loop` instead: created once, detached at
`default_branch`, and reused by every later sortie rather than one per run.
Reuse rather than per-run creation was the deciding property — nothing is ever
abandoned, so there is no prune path to get wrong, and `max_worktrees: 2`
(`qops/guard.py:263`) was already sized for owner tree plus loop tree. On
reuse the worktree is reset to a clean detached `default_branch` first, so a
prior sortie's branch or a killed run's leftovers cannot leak into the next
launch. `git branch`/`git rev-list` in `launch_evidence()` still run with
`cwd=ROOT`: refs are shared across a repo's worktrees, so ROOT sees a branch
created in the loop worktree without ROOT's own checkout ever moving.

## automerge-loop

**Trigger:** any pull-request event — opened, reopened, synchronised, labelled,
unlabelled, ready-for-review, closed.
**Does:** queues GitHub's **native** auto-merge for a qualifying PR. It does
not merge; branch protection's required checks do, when they go green — **and
if the branch has no required checks it refuses to queue anything and fails
loudly**, because there the same call would merge on the spot (qops#3). On
`closed` + merged it advances the linked issue instead (below), and on a
PR it declines to auto-merge it labels that issue `state:review` — the
waiting-on-you signal the digest renders (#151).
**Qualifies:** not a draft, not from a fork, a branch matching
`<type>/<issue#>-<slug>` (ADR-0019), and the **linked issue** carrying
`gate:machine` and no `no-auto`. The gate is read from the issue, not the PR —
nothing labels a PR. `no-issue/` has no issue, so it never auto-merges.
**Acceptance check:** a `gate:taste` PR is never merged by it, and a red gate
never merges anything.
**Why it exists:** on a `gate:machine` PR the owner's click had nothing left to
judge — the gate judged it. A mindless approval button is not a control
(ADR-0020).
**Failure mode it accepts:** a defect the machine gate cannot see reaches
`master` unread. That is the same exposure every unread manual merge already
carried, made honest — so a defect that lands this way is a **missing check**,
and the fix is the check, not the restoration of the click.
**Amended 2026-08-16 (#128): the merge releases the claim.** #122 released it on
failure and on nothing else, so the first successful unattended run shipped and
left its issue OPEN at `state:building` with `ready:auto` still on it —
`metrics.S9` then reported a finished sortie as in-flight, and the instrument
ADR-0013's re-decision depends on was counting wrong. The `advance` job now
fires on a merged PR whose branch names an issue, sets `state:done` and drops
`ready:auto` and every other `state:`.

- **It labels, and closes a `gate:machine` row (ADR-0025).** A merged PR means
  the code landed, not that the sortie is judged — but on `gate:machine` there
  is no taste read left to give, the same reasoning ADR-0020 already uses for
  the merge itself. `gate:taste` still only reaches `state:done`; closing that
  one is a judgement, and stays the owner's. `no-auto` vetoes the close.
- **It does not rest on the agent writing `Closes #<n>`.** The branch already
  carries the issue number (ADR-0019) and the workflow already parses it to
  read the gate. #116's PR carried no such line and shipped anyway — an
  instruction in a prompt is a preference, not a control (GL-53). The launch
  prompt gained the instruction half too, as `Refs #<n>`: a link, not a close.
- **It is not conditional on the gate.** However the PR was merged, by the job
  above or by the owner, the row is no longer in flight.
- **The launch prompt names the branch prefixes.** #116 branched
  `code/116-...`: it read `type:code` off the issue and used a label where
  ADR-0019 wants a commit type (`feat|fix|docs|chore|refactor|test`). This half
  is prompt-only on purpose — a merge rejected over a prefix nit would be worse
  than the drift.

**Amended 2026-08-18 (#150): `advance` alone was never enough.** It triggers on
`pull_request` + `closed` + `merged`, and **GitHub starts no workflow run from
an event its own `GITHUB_TOKEN` raised** — so on the one path that matters, a
PR the `enable` job auto-merged, no `closed` event exists and `advance` never
runs. #59 shipped and sat at `state:building` + `ready:auto`; #115 was the same
failure written off as a stale row. The backstop is `qops reconcile`, a job in
`digest.yml` on `digest_cron`: it lists merged PRs, reads the issue each branch
names (ADR-0019, never `Closes #n`), and advances any row that is not
`state:done`. It reads state rather than reacting to an event, which is why it
repairs the row however the PR merged — bot, human or hand-merge. It is
idempotent, it labels and — `gate:machine` only, ADR-0025 — closes, and a skip
prints its reason. `advance` stays: it is the fast path on a human-token
merge, and deleting it to install the slow one would trade latency for
nothing.

**Amended 2026-08-20 (ADR-0025, #12/#21/#23):** `reconcile` also heals a row
`advance` already labelled `state:done` but never got to close — its own
`--limit` window, or a hand-merge older than either mechanism ever saw, is
otherwise indefinite, and an owner noticing is not a mechanism.

## Audit

Loop Doctor, 2026-08-14, once (PRD v3 Phase 4 item 10). A **design** audit —
none of the five then defined had fired yet, so no finding is connected to an
observed failure. Verdict: repair needed. `groom-loop` and `review-loop` were sound and
were left alone. Three material findings, all fixed in the same commit:

1. **`pickup-loop` re-picked the same sortie forever.** It chose the
   least-recently-updated eligible issue and never changed that issue's state,
   so an hourly fire on a sortie that failed or stalled picked the same issue
   again next hour — one session per hour, indefinitely. **Fix:** claim the
   issue (`state:planned` → `state:building`) *before* launching, and abort the
   launch if the claim fails. The claim is the no-progress stop.
2. **`gate-loop`'s acceptance check and its instrument disagreed.** The
   definition says "every applicable machine gate green"; `metrics.s4` looked
   only for a check named `gate` or `test`, so a red `guard.yml` — the tripwire
   and doc-link scan — scored as clean. **Fix:** S4 now reads every check's
   conclusion rather than two names.
3. **`triage-loop` warned into a place nobody reads.** Weekly `::warning::`
   lines in an Actions log, consumed by nothing, so the loop had no terminal
   state and could warn identically forever. **Fix:** the untriaged list is
   rendered into the digest, which reaches the owner and can reach zero.
