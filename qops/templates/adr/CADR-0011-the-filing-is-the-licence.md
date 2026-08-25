---
status: accepted
revisit-after: 2026-10-15
amends: 0017, 0023, 0026
---

# The filing is the licence, so the filing is where the check goes

**Date:** 2026-08-20 · **Session:** the #25 interview, rounds one to three ·
**Amends:** ADR-0026's failure-mode arithmetic, ADR-0023's grant path, and
ADR-0017's routing rule · **Depends on:** ADR-0027.

## Context

The interview on #25 falsified its own premise. #25 argued that drafting plans
was the lever — *"reviewing a draft plan is a fifth of the work of writing
one"* — on the assumption that the owner writes plans and would otherwise read
them. **He does neither.** Owner, verbatim, round one:

> i don't write plans ever. None of the plans in qops or printshop were written
> by me. I seldomly review the plans, and only am there to clear ambiguity
> compared to the goal […] My role is to set the direction, not define how to
> get there.

So the pipeline #25 wanted to automate is not four manual steps with a human at
each. It is **one owner act — the filing — and three derivations**, of which
only the first is still manual today.

### The arithmetic ADR-0026 can no longer carry

ADR-0026 accepted the risk of a mislabelled `gate:machine` row on this basis:

> the three controls downstream (`state:planned`, `ready:auto`, R8's named test)
> each require the owner to look at the row before it can run, so this needs
> **three** misses and not one.

Every one of the three is removed by the design above. `state:planned` becomes
the planner's write. `ready:auto` becomes mechanical on `origin:owner`, which is
ADR-0023's own decision reaching its conclusion. R8's named test is written by
the same agent that chose the label, so it cannot be an independent check of
that label. **Three controls collapse into one, and the one is the filing.**

That is not an objection to the design. It is the design, stated honestly: an
issue the owner files becomes a licence for an unattended agent to commit to
`master` in his name. ADR-0020 already accepted unread *merges*; this accepts an
unread *chain*, and the difference is that nothing between the filing and the
merge was ever read by anybody.

## Decision

**The check moves to where the control now is: the filing.**

### 1. A filing bar, validated mechanically

A row may not advance past `state:triage` unless its body states an outcome a
machine can turn into acceptance criteria. `qops doctor` holds the checkable
half — a row with no stated outcome is a problem, reported, and the triager
refuses to label it (ADR-0027's refusal path, same mechanism, second reason).

This is R8 moved upstream. R8 checked a named test *at grant time*; with the
grant mechanical there is no grant-time left, so the check runs where the owner
still acts.

### 2. The filing bar is also the taste detector

This is the load-bearing consequence and it is why the bar is worth its cost.
**A row whose deliverable is the owner's preference cannot state a machine
criterion** — that is ADR-0026's predicate read from the other end. So a genuine
taste row fails the filing bar and stops, *by the same check*, without anyone
classifying it as taste first.

A mislabel therefore does not ship: the triager can only mislabel a row that
already passed the bar, and a row that passed the bar has a criterion, and a
criterion is what `gate:machine` claims to have. The single control does double
duty, and **the design rests entirely on the bar's strictness** — see the risk
below.

### 3. A plan is machine input, not an owner-facing ask

`planner.md` currently shapes every plan as an ask: one page, summary first, at
most four options, one recommendation. Nobody reads plans, so that shape is
wrong for them. **Plans become a spec a coder executes and a test checks.** The
one-page ask survives, unchanged, for `type:decision` rows only — the nine rows
still carrying `gate:taste` after the re-triage, and the two rows in the whole
corpus where the owner's read changed an outcome (`qhoto_printshop`#112, #151)
were both asks.

### 4. The reviewer's verdict blocks

The owner asked for a reviewer comparing the diff to the row's goal, *and* for
that verdict to block. A reviewer that only reports is a preference, not a
control (GL-53), so the two answers are the same answer: it becomes a **required
status check**.

Its behaviour is specified, because a non-deterministic gate under
`enforce_admins: true` (ADR-0016) can make `master` unmergeable for the owner
too:

| Outcome | Result |
|---|---|
| The reviewer ran and judged the diff to serve the row's goal | green |
| The reviewer ran and judged that it does not | **red — fail closed** |
| The reviewer could not run (auth, rate limit, timeout, model error) | **green — fail open, and it says so in the check's output** |

An LLM that could not run is not a rejection. Only a verdict is. The one case
this leaves open — a reviewer that runs and is wrong — is answered the way this
repo answers every wrong gate: it is a missing check, and the fix is the check.

### 5. Throughput is bounded by the schedule and nothing else

Considered and declined: a per-day sortie cap. `pickup-loop` fires hourly and
takes one row, so the worst case is 24 reversible PRs behind a green gate and a
blocking reviewer. A cap would also need a config key, and the contract is
frozen. Revisit if a day ever produces more than a handful.

## The risk this design carries, named because it is single-point

**Everything rests on the filing bar.** It is the entry check, the taste
detector and the replacement for three removed controls at once. If it is
lenient, a one-line filing licenses an unattended chain with nothing between it
and `master` but a reviewer agent's opinion. If it is strict, the owner pays at
filing time — the one place he said he wants to spend less.

Two things keep that honest rather than hopeful:

- The bar is **mechanical and testable**, not a judgement: does the body state
  an outcome that can be turned into a criterion. It is asserted, it is not a
  prompt.
- The `revisit-after` on this ADR is **short and deliberate** (2026-10-15),
  the same reasoning ADR-0009 used for the decision most likely to be wrong.
  This one's failure mode is not silent — it ships something — but it is
  discovered late, which is close enough.

## Consequences

- ADR-0026's failure-mode paragraph is superseded by this one. Its *predicate*
  is untouched, and the filing bar in §2 is that predicate enforced earlier.
- ADR-0023's `origin:owner` path reaches its conclusion: the filing is the
  grant, mechanically, and #26 (`origin:` label) and #27 (R8 proves rather than
  names) stop being follow-ups and become **prerequisites**.
- ADR-0017's routing rule gains a row: a `gate:machine` sortie now has *no*
  owner contact at any point, not merely none before review.
- Scope is `qvajda/qops` only. `qhoto_printshop` inherits by pinning a later
  version, once this has run on the substrate that can test it.
- **The critic:** the filing bar, the reviewer's fail-open/fail-closed split,
  and the two plan shapes each get an assertion. Named in the PRD
  (`docs/prd/2026-08-20-eligibility-pipeline.md`), which is the build.
