---
status: accepted
revisit-after: 2026-12-15
amends: 0026, 0028, 0031
depends-on: 0017, 0020, 0023
---

# `gate:taste` is a review at the end, not a decision at the start

**Date:** 2026-08-27 · **Session:** owner interview on #218, three rounds ·
**Amends:** ADR-0026 (its predicate, R3 and R4), ADR-0028 §2 (the filing bar's
taste-detector role), ADR-0031 §1 (the trigger set); restates ADR-0017's
routing rule.

## Context

Observed in a consuming project, repeatedly, over one day: a `gate:taste` row is
picked up as a remote-control session that asks the owner to decide **before any
work exists**. `alert_prompt()` (`scripts/qops_pickup.py:311`) asks for "exactly
one recommendation with at most four options"; with no artefact to judge, the
four options degenerate to *go ahead* / *park it* / *close it as stale*. The
owner spends an interactive session rubber-stamping a start he already licensed
by filing the row.

The consequence he names precisely: **`gate:taste` and `type:decision` behave
identically.** That is not drift. It is what two decisions say when composed.

### The two decisions, and the third surface that disagrees with both

- **ADR-0026** defines `gate:taste` as *the owner's preference is an **input**
  the work cannot proceed without — the row's deliverable **is** a choice only
  the owner can make*, and R4 states the identity outright: "`type:decision`
  stays `gate:taste` by construction".
- **ADR-0031 §1** accepts `gate:taste` as a clause of
  `pending.waiting_on_owner()` and fires on **entry** into the set. Filing the
  row is the edge, so the alert lands before the plan, let alone the artefact.
- **`qops/brief.py:98`** has shipped the *other* meaning throughout, never
  reconciled by ADR-0026: "`gate:taste` — the owner sees the artefact, not the
  diff; machine gate green first." That is ADR-0017's original routing rule,
  and it is the meaning the owner wants.

The substrate has been shipping two definitions of one label, and the one that
reaches the owner's attention hourly is the one he did not want.

### What is already built, and therefore not decided here

The end-of-sortie moment needs no new transport. `automerge.yml:93` is a
**refusal** branch: a non-`gate:machine` PR gets its issue labelled
`state:review` and the job stops — `stop "issue #$n is not gate:machine - the
owner merges this one"`. It never enables auto-merge. `state:review` is already
a `waiting_on_owner()` clause (`qops/pending.py:114`) and already an alert
trigger. So the path *file → picker → unattended build → PR → `state:review` →
remote-control session* exists end to end today; `gate:taste` is simply
short-circuited out of it at the first step.

The taxonomy needs nothing either: `type:decision` and `type:research` both
ship (`.qops/config.yml:138`). The contract stays frozen.

## Decision

**`gate:` answers *when* in the sortie the owner is needed, not *whether*.
`type:` answers what the row builds.**

- **`gate:taste`** — the owner's judgement lands on the **artefact, at the end**.
  Everything up to it runs unattended: planned, picked up, built, PR opened.
  The owner is called exactly once, to review, and the review is the gate.
- **`gate:machine`** — no owner contact before review at all (ADR-0020,
  unchanged).
- **`type:decision`** — the row builds a *set of proposals*; the owner's review
  picks one. It is not a special path: proposals are an artefact, they land in a
  PR (a draft ADR, a document under `docs/`), and the same review moment applies.
- **`type:research` is gate-agnostic.** `gate:machine` when the finding only
  feeds the automated path — the best way to reach a goal already set.
  `gate:taste` when the finding is meant for the owner's eyes and may lead him
  to set new goals. ADR-0026's R4 de-tasted `type:research` wholesale; that was
  right against the old predicate and is wrong against this one.

ADR-0026's *default* survives untouched: **when unsure, `gate:machine`**. An
unsure row is underspecified, not tasteful. What changes is only what the taste
label means once chosen — and its cost drops to near zero, because a
`gate:taste` row now moves through the queue by itself instead of parking.

### The owner moment, in full

1. The owner files the goal. On `origin:owner` the filing is the grant
   (ADR-0023) and, with a stated outcome (ADR-0028), the licence.
2. The picker takes the row and builds it unattended. **`gate:taste` no longer
   vetoes eligibility** (`qops/install.py:961`).
3. The PR opens. `automerge.yml` refuses the merge and writes `state:review`.
4. The alerter opens **one** remote-control session on the `state:review`
   clause, carrying the artefact and what the row asked for.
5. The owner reviews.
   - **Approved** — the session merges. The approval is the decision; the merge
     is its mechanical consequence, and asking the owner to click it afterwards
     is recurring toil, which CLAUDE.md forbids where the act is not itself a
     decision. It stays inside the session rather than moving into
     `automerge.yml` because the session already has the owner's word, in
     context, and no label round-trip carries it more cheaply.
   - **Rejected** — the session **asks what comes next** and does not choose:
     abandon, retry inside this session, edit the row to record the failure and
     the feedback, or something else the context suggests. A rejected artefact
     with no defined path back is how a parking lot forms, which is the exact
     failure ADR-0026 measured; here the interactive session that is already
     open is the path.

### The filing bar, re-founded

A `gate:taste` row **states what it builds** — a research document, a composed
image, a set of proposals — **and that it wants a positive review**. Its
acceptance criterion is about the artefact, not about the judgement.

This strikes **ADR-0028 §2**, which made the filing bar do double duty as a
taste detector on the premise that *a row whose deliverable is the owner's
preference cannot state a machine criterion*. Under this ADR that premise is
false: preparing the proposals **is** the work, and it is perfectly stateable.

Striking §2 removes a protection whose subject no longer exists — there is now
no class of row that must not be built. But §2 also carried the *argument* for
the bar's strictness ("the design rests entirely on the bar's strictness"), and
an argument removed is a strictness a future session loosens seeing no
consequence. **So the strictness is re-founded on a sharper leg:** a row with no
stated artefact is no longer merely unplannable — it is picked up and **built,
unattended, into nothing**. The bar protects more now than it did, not less.

`qops/install.py:1135` reasons `_BAR_EXEMPT` from the dead premise. The
exemption itself survives on its other leg — nothing is downstream of done — so
no behaviour moves, but the comment is corrected in the same change or it argues
from a falsehood.

## The critic

An instruction is a preference; a check is a control. The implementation rows
carry these, or this ADR is prose:

1. **`gate:taste` appears in no entry-time clause of `waiting_on_owner()`**,
   asserted over that function's own source — the shape of
   `test_the_alerter_holds_no_trigger_predicate`. A fixture-only test can pass
   by accident; a source assertion cannot.
2. A fresh `gate:taste` row is **absent** from `waiting_on_owner()`; the same
   row at `state:review` is **present**.
3. `install.eligible()` **accepts** a `gate:taste` + `origin:owner` row with a
   stated criterion, and `type:research` + `gate:taste` is a legal row.
4. The filing bar still **refuses** a `gate:taste` row that names no artefact.
5. `test_the_alerter_holds_no_trigger_predicate` and
   `test_gate_machine_alone_confers_no_autonomy` keep passing unchanged.
6. The review-clause alert prompt names both the merge step and the reject
   question. **Stated limit:** the session's conduct is a prompt, and a prompt is
   a preference — this assertion proves the text is there, not that the session
   obeys. It is the only control available, because the act is interactive by
   the owner's choice, and recording the limit is better than implying a control
   that does not exist.

## Consequences

- **Two alert prompts, not one.** The entry clauses (struck out, `no-auto`,
  `state:done` unclosed) keep today's shape. The `state:review` clause gets its
  own: the artefact, what the row asked for, then approve-and-merge or
  ask-what-next.
- **`automerge.yml.tmpl`, `reconcile.py` and `digest.yml.tmpl` do not move.**
  A `gate:taste` PR still never auto-merges and a `gate:taste` row still only
  reaches `state:done` (ADR-0025). The merge lands from the session, with the
  owner in it.
- **Taste rows join the picker queue.** They compete for the hourly slot with
  machine rows, least-recently-updated first. Not a defect; the backlog simply
  moves differently, and that is the point.
- **The existing backlog is wrong under the new meaning** and needs a re-label
  pass — `qops migrate` under ADR-0030: it proposes, the owner approves once.
  This is the third and last implementation row, and it may wait.
- **Failure mode this creates:** a taste row builds the wrong thing for hours
  and is rejected wholesale. The old up-front ask nominally prevented that; it
  did so by making the owner answer a question he had no information to answer,
  which is why it was rubber-stamped instead. The mitigations are the filing bar
  upstream and the reject path above, and the cost of a wasted unattended run is
  accepted against a rubber-stamp on every row.
- **Ships in three sorties, in order:** (i) the entry-clause removal, the
  eligibility unveto and the two prompts; (ii) the `type:decision` output
  landing in a PR; (iii) the migration.
