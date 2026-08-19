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
| `pickup-loop` | Windows scheduled task `qops-pickup-loop`, **disabled** | hourly when enabled | yes | branch + commit + PR; merges only via `automerge-loop` |
| `automerge-loop` | Actions — `automerge.yml`, plus the `reconcile` job in `digest.yml` | every PR event, plus daily | none | turns on native auto-merge for a `gate:machine` PR; may not merge a `gate:taste` one; labels a merged sortie `state:done`, may not close it |

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
**Deliberate limit:** it **warns and does not label.** A guessed label reads
exactly like a decided one. `ready:auto` is never applied by any loop.

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
**Does:** picks the least-recently-updated issue carrying `state:planned` **and**
`ready:auto`, with a real gate (`gate:none` is not one) and no `no-auto` /
`blocked` flag, then starts a session on it.
**Acceptance check:** it branches, commits, opens a PR and stops there. It
never merges by hand, never activates a listing, never pushes to `master`.
**Observed end to end, 2026-08-19 (attempt 3).** #160: claimed 08:28, branched
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
- **No sandbox escape unattended.** The denied session retried with
  `dangerouslyDisableSandbox`. The launch sets `QOPS_UNATTENDED=1` and `qops
  guard` refuses that flag when it is set. An owner at a keyboard may still
  make that call; a loop with nobody reading may not.

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

- **It labels; it never closes.** A merged PR means the code landed, not that
  the sortie is judged. On `gate:taste` work the owner's read is the only
  judgement there is, so closing stays the owner's.
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
idempotent, it labels and never closes, and a skip prints its reason. `advance`
stays: it is the fast path on a human-token merge, and deleting it to install
the slow one would trade latency for nothing.

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
