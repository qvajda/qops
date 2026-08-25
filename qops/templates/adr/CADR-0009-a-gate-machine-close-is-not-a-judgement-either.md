---
status: accepted
revisit-after: 2026-11-01
amends: 0020
---

# A `gate:machine` close is not a judgement either

**Date:** 2026-08-20 · **Session:** owner-initiated · **Amends:** ADR-0020 and
`qops/reconcile.py`.

## Context

#12, #21 and #23 were found open, carrying `state:done`, for work already
merged (#32, #22+#31, #24). Investigating why turned up the actual defect:
ADR-0020 is explicit that `advance` and `reconcile` **label a merged row,
never close it** — "a merge means the code landed, not that the sortie is
judged" — and nothing else closes either. `reconcile` only ever sees a merge
it can attribute to an issue via the branch name (ADR-0019); a hand-merged PR
still trips it, but a merge older than its `--limit` window, or one it never
got the chance to see, does not. A correctly-working system therefore
accumulates open `state:done` issues indefinitely, and there is no mechanism
that closes them — only the owner, noticing.

That is not a defect at three rows. It is one once `pickup-loop`'s schedule is
on: an issue carrying `ready:auto` for work already merged is exactly the
shape that has fooled the picker before (qhoto_printshop #163, #169) — and the
open-`state:done` failure mode is adjacent enough to be worth closing off in
the same change, even though `advance`/`reconcile` already strip `ready:auto`
the moment they set `state:done`, so that specific combination cannot occur
via the mechanisms this ADR touches.

## Decision

**`reconcile` (and `advance`'s fast path) closes an issue it can prove is
`state:done`, when and only when that issue is `gate:machine`.**

The reasoning is ADR-0020's own, one step further. That ADR's case for
auto-merge is: on a `gate:machine` PR "there is nothing for a human to judge —
the gate has judged it — so the click is a mindless approval button, and a
mindless approval button is not a control." The same is true of the close.
Once the gate has judged the PR and the PR is merged, closing the issue asks
for a taste read that `gate:machine` has already declared does not exist. Not
closing it is the mindless click, worn as caution instead of laziness.

`gate:taste` is exactly the case this does not cover, on purpose: there the
owner's read is the only judgement there is, and ADR-0020 already reserves it
for them. `no-auto` vetoes the close the same way it vetoes the merge — it is
the standing per-issue override, and a close is not exempt from it.

**Mechanically:** `reconcile` closes in two situations — the row it is about
to advance to `state:done`, and a row it finds already at `state:done` but
still open (the backstop for #12/#21/#23's exact shape, so a row `advance`
already labelled but a stale reconcile window never got to close still self
heals on the next run). `advance`'s fast path gets the same check inline,
since it already knows the issue and the PR that merged it.

## What this is not

**Not** a claim that `state:done` + open is impossible from here forward for
every path — a `gate:taste` row that the owner never revisits stays open
forever, and that is intended: closing it is their decision, and CLAUDE.md's
new constraint (below) is that a *decision* is legitimate owner toil where
*deriving a fact from state already on the tracker* is not. It also does not
retroactively repair #12: that issue is `gate:taste` and its `state:done` was
wrong on the facts (only part of its scope shipped), which this ADR does not
touch — see #12's own correction, a label fix, not a close.

## Amended 2026-08-20, same day: `no-auto` also vetoes the relabel

Landing the above surfaced a second bug immediately: `qops reconcile`,
re-run after this PR merged, relabelled #12 back to `state:done` — a row
corrected by hand minutes earlier to `state:planned`, because PR #32 (which
still names #12 in its branch) closed only part of #12's scope. `reconcile`
and `advance` read "the branch's PR is merged" as "this issue is done," which
is only true for a correctly-sized sortie. `no-auto` already means "the owner
is handling this one"; it now vetoes the relabel-to-`state:done` too, checked
before either mechanism touches the row, not only before it would close one.

This does not fix the general case — a multi-part issue with no `no-auto` on
it will keep flipping to `state:done` on every merged PR that names it, for
as long as that PR stays inside `reconcile`'s merged-PR window. The general
fix (an issue should be sized as one sortie, or the branch should not be
allowed to imply full closure of a multi-part one) is not taken here.

## Consequences

- `qops/reconcile.py::reconcile()` gains a `closed` bucket alongside
  `advanced`/`skipped`/`failed`, and a `_closeable()` check
  (`gate:machine` and not `no-auto`).
- `qops/templates/automerge.yml.tmpl`'s `advance` job reads the issue's labels
  after the label edit and closes under the same condition.
- `tests/test_qops.py` — `test_reconcile_closes_a_gate_machine_row_the_gate_already_judged`,
  `test_reconcile_never_closes_a_gate_taste_row`,
  `test_reconcile_heals_a_row_advance_already_labelled_but_never_closed`,
  `test_reconcile_no_auto_vetoes_the_close_same_as_the_merge`,
  `test_advance_closes_only_behind_a_gate_machine_check`.
- **Failure mode this creates:** identical to ADR-0020's — a defect the
  machine gate cannot see now closes its own issue unread, same as it already
  merges unread. No new exposure; the fix is the gate's coverage, not the
  restoration of a click that judged nothing.
