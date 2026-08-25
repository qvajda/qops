---
status: accepted
revisit-after: 2026-12-01
amends: 0025
---

# One row is one sortie

**Date:** 2026-08-20 · **Session:** the #25 interview, round two ·
**Amends:** ADR-0025's deferred general case · **Precedes:** the eligibility
PRD, which cannot be written without it.

## Context

ADR-0025's same-day amendment named a hole and left it open, verbatim:

> This does not fix the general case — a multi-part issue with no `no-auto` on
> it will keep flipping to `state:done` on every merged PR that names it […]
> The general fix (an issue should be sized as one sortie, or the branch should
> not be allowed to imply full closure of a multi-part one) is not taken here.

Two independent findings say the deferral has run out.

**One: it already cost a correction by hand.** `qops reconcile` relabelled #12
back to `state:done` minutes after the owner had corrected it to
`state:planned`, because PR #32 named #12 in its branch and closed only part of
its scope. The repair was `no-auto` — a standing veto used as a bandage on a
sizing defect.

**Two: the re-triage found four more of them, and the predicate cannot label
them.** Applying ADR-0026 across both trackers, four rows are *half machine and
half taste*: `qhoto_printshop`#51 (named technical defects, plus open-ended
"compositor refinement"), #154 (bring one candidate to standard — mechanical;
choose three art pieces for the banner — the owner's eye), #92, #156. ADR-0026
asks "is the deliverable itself the owner's preference?" and a two-part row
answers *both*. There is no label for that and there should not be one; the row
is wrong, not the taxonomy.

This blocks the eligibility pipeline outright. A triager that applies `gate:`
cannot label what is not one sortie, and a mechanism that reads a branch as
"this row is finished" is only correct when the row was one sortie to begin
with.

## Decision

**A row on the tracker is exactly one sortie: one deliverable, one gate, one
acceptance criterion, finishable in one session.** A row that is not is a
defect in the row.

Two mechanisms, split by role, and the split is deliberate:

1. **The triager detects and refuses.** An oversized row is left unlabelled and
   reported, exactly as an ambiguous one already is (`.claude/agents/triager.md`
   — *"a guessed label reads exactly like a decided one, which is worse than a
   gap"*). The triager does not split: splitting writes issue bodies, and the
   triager is fenced out of issue bodies for good reason.
2. **The planner splits.** A reported row goes to the planner, which already
   carries the rule — *"if the work is larger than one session, say so and
   propose the split"* — and already may not silently widen a plan. Splitting a
   row into children is that rule applied one level up.

**What "oversized" means, decidably, at triage time and without asking the
owner:** the row states more than one outcome that could ship independently, or
its outcomes do not share a gate under ADR-0026. Both are readable from the row
alone. A row naming several *files* is not oversized; a row naming several
*deliverables* is.

## What this does not take

**The branch still implies the row.** ADR-0025 offered a second shape — stop
`reconcile` and `advance` inferring closure from the branch name — and it is
**not taken**. Under this ADR the inference becomes sound: a branch names a row,
a row is one sortie, so a merged PR does finish it. Removing the inference would
cost the mechanism ADR-0019 and ADR-0020 both rest on, to defend against a state
this ADR makes illegal. `no-auto` stays as the escape for the row that is
exceptional anyway, which is what it was before it was used as a bandage.

## Consequences

- `.claude/agents/triager.md` gains the refusal case and its report line.
- `.claude/agents/planner.md` gains splitting a reported row as an explicit
  output, not merely a thing it may say.
- **The four rows found by the re-triage are split by hand before the pipeline
  runs**, not by the mechanism this ADR describes — the mechanism refuses them,
  it does not repair them. `qops#12` is already carrying `no-auto` for this
  reason and is the fifth.
- **Failure mode this creates:** a row correctly sized at filing time grows.
  Nothing re-checks a row once it is labelled, so a row that acquires a second
  deliverable in a comment is invisible to the triager that already passed it.
  Accepted; the mitigation is that the *plan* is written from the row and the
  planner sees the whole row, comments included.
- **The critic:** `tests/test_qops.py::test_the_triager_refuses_rather_than_guesses`
  asserts the refusal is in the role and that the triager still may not write
  issue bodies, so the two mechanisms cannot merge into one by drift.
