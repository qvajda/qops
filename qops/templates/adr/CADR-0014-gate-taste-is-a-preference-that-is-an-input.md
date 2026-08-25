---
status: accepted
revisit-after: 2026-12-01
amends: 0017, 0020, 0024, 0025
---

# `gate:taste` is a preference the work cannot start without, and nothing else

**Date:** 2026-08-20 · **Session:** owner-initiated research-and-decide on #25 ·
**Amends:** ADR-0017's routing rule, ADR-0020 and ADR-0025 (which both borrowed
`gate:` for a job it does not do), ADR-0024's reasoning for #19's label, and
triage rules R3, R4 and R6 in `docs/agents/triage-labels.md`.

## Context

#25 says the substrate automates execution and not eligibility, and eligibility
is the bottleneck. ADR-0023 took the `ready:auto` half. This takes the half
upstream of it: **which rows are even candidates**, decided by R3 —

> R3 … **When unsure, `gate:taste`** — a wrong `machine` label produces an
> autonomous sortie that ships a taste decision.

That is a coin flip wearing caution's clothes, and it is the mechanism producing
the toil CLAUDE.md now forbids: a triager who cannot decide sends the row to the
owner, and the owner is the only reader of a `gate:taste` queue.

### The measurement

Every resolved `gate:taste` row across both trackers — `qvajda/qops` and
`qvajda/qhoto_printshop`, N = 14 — read for one question: **did an owner action
change the outcome, or did the owner only transcribe a conclusion something else
had already reached?**

| Row | What resolved it | Verdict |
|---|---|---|
| ps#49, #114, #123, #124, #126, #137, #152, #176, #177 | closed as **migration records** to qops#17/#14/#13/#12/#11/#10/#9/#7/#6; the work is still open, one tracker over | transcription (9) |
| ps#139 — Telegram ack listener | acceptance criterion stated **in the issue body before the work**; the owner tapped a real button and the log was read | transcription — the owner was the instrument, not the judge |
| ps#151 — the unsatisfiable "requests review" clause | three shapes offered; the owner picked a **fourth** ("shape 1-and-a-half") | **owner action changed the outcome** |
| ps#112 — Phase 7 sign-off | seven decisions requested; item 7 **DECLINED**, and items 8–10 were owner-initiated additions the proposal never asked for | **owner action changed the outcome** |
| qops#3 — automerge merges unread without required checks | mechanical fail-closed fix, merged as specified | transcription |
| qops#34 — ADR-0025 | its own closing comment: *"nothing left for a mechanism to derive here"* | transcription |

**2 of 14.** Not zero, so the exceptions define the predicate, as #25's brief
required — and they define it more sharply than any argument would have.

Three further figures, because they change what the label is *for*:

- **`gate:taste` is where rows stop.** 47 rows have carried it; 14 closed, and
  9 of those were repo migrations. **5 of 47 (11%) were ever actually
  resolved.** `gate:machine`: 24 of 43 (56%).
- **14 of the 22 open `gate:taste` rows in the printshop say, in their own
  body, `gate: none — defined when the sortie is planned`.** The import applied
  `gate:taste` *over the row's own statement that its gate was undefined*. R3's
  default is not a judgement anybody made; it is visible, in bulk, as the thing
  that happened when nobody judged.
- **Every one of the 9 migrated rows arrived in `qvajda/qops` still carrying
  `gate:taste`.** The label travelled with the row, unexamined, into a repo
  where the argument for it (vendor endpoints, commercial judgement, live
  storefronts) does not hold at all.

### What the two exceptions share, and what the owner's list does not

The owner's retrospective names seven moments where his taste was load-bearing,
and proposes that they share *the acceptance criterion could not be written down
before the work existed*. **That property does not survive the corpus.**

ps#139 is moment 6 (the Telegram listener), and its acceptance criterion is
written in its body, before the work, in full. What it lacked was not a stated
criterion but a **machine that could observe it** — the finish line was a
physical tap on a phone. That is a fact about verification reach, not about
judgement, and the taxonomy already has a word for it: `type:manual`.

What ps#112 and ps#151 share instead is exact: **the row's deliverable *was* the
owner's preference.** #112 asked "approve or decline these seven" — the criterion
("each item has an answer") was perfectly stateable; the *answer* was not
derivable from anything but the owner. #151 offered three shapes and got a
fourth. In both, the owner's preference is an **input** the work could not
proceed without.

Moments 2, 3 and 7 of the owner's list (art quality, copy quality, the Gelato and
Etsy dashboard reviews) fit the same shape and are, notably, **almost absent from
the resolved corpus** — they are the open rows (ps#132, ps#154, ps#155) that
never resolve. Moments 1 and 5 predate any tracker. Moment 4 is ps#112.

## The overload

`gate:taste` was carrying three unrelated jobs. They are separable, and the
taxonomy already has a carrier for each — **no new label, no schema change; the
contract stays frozen.**

| Concern | The question | Carrier | Decidable at triage without asking the owner? |
|---|---|---|---|
| **Judgement** | Is the deliverable *itself* the owner's preference? | `gate:taste` / `gate:machine` | yes — read the row's own statement of done |
| **Authority** | Is the *act* the owner's to take — spending, publishing, granting, activating, anything in his name? | **`no-auto`** | yes — the act's surface is named in the row |
| **Verification reach** | Can CI observe the finish line, or must a human hand? | **`type:manual`** vs `type:code` | yes |

Authority is the one that is not taste at all, and conflating it with judgement
is why the label felt arbitrary. `no-auto` already vetoes the merge (ADR-0020),
the close and the relabel (ADR-0025) and the pickup (`qops_pickup.eligible`) —
it is a real control with three mechanisms behind it, and it was sitting unused
while `gate:taste` did its job badly.

`type:manual` already carries R5 (*never gets `ready:auto`, whatever its gate*),
so **`gate:machine` + `type:manual` is a legal and useful row**: the finish line
is stated and checkable, just not by a runner. That combination does not occur
once in either tracker — all 19 open `gate:machine` rows are `type:code` —
because R3 swallowed it.

## Decision

**A row is `gate:taste` if and only if the owner's preference is an input the
work cannot proceed without: the row's deliverable *is* a choice only the owner
can make. Every other row is `gate:machine`.**

The triager's question, answerable from the row alone: *if the owner never
answers, can this row be finished at all?* Yes → `gate:machine`. If the whole
output of the row is the owner's answer → `gate:taste`.

**And the default inverts: when unsure, `gate:machine`.** An unsure row is not a
taste row; it is an underspecified one, and the answer to underspecification is
a stated criterion, not a label that parks it.

### Why inverting the default is safe, which is the load-bearing part

R3's caution — *"a wrong `machine` label produces an autonomous sortie that ships
a taste decision"* — is **false, and was false when it was written**.
`gate:machine` confers no autonomy by itself. `scripts/qops_pickup.py::eligible`
requires `state:planned` **and** `ready:auto` and no `no-auto`; `ready:auto` is
the owner's alone to grant (ADR-0023, CLAUDE.md) and needs a named test that
proves the work done (R8). A mislabelled `gate:machine` row with no plan and no
grant sits exactly where a `gate:taste` row sits: in the backlog.

So R3's default bought nothing and cost the eligibility pipeline. That claim is
the one thing here a future change could quietly invalidate, so it is asserted:
`test_gate_machine_alone_confers_no_autonomy`. If someone ever relaxes
`eligible()`, that test fails and this ADR's safety argument fails with it, in
the same commit.

### The rules this rewrites

- **R3** — restated as the predicate above, default inverted.
- **R4** — split. `type:decision` stays `gate:taste` by construction: its
  deliverable is a choice. **`type:research` no longer is.** A research row's
  deliverable is a *finding*, and a finding is not a preference — its finish
  line is "the finding is written where the row says." The owner reading it is a
  separate act, and where that reading is itself a choice, it is a separate
  `type:decision` row (the shape #25 → #26/#27/#28 already used). This is the
  single largest source of the parking lot: 15 of the 33 open `gate:taste` rows
  are `type:research`.
- **R6** — moves from the gate to the flag. "Its completion path calls an
  endpoint the project forbids" is **authority**, so it is a `no-auto` rule, not
  a `ready:auto` rule and not a gate rule.

## Reconciliation with ADR-0025

ADR-0025 asked "can completion be **derived**" and answered it with `gate:`,
which is the borrowing it already admitted was known-incomplete. Under this ADR
the two questions are distinct and both are answered:

- *May this row close without a human read?* — the **close** gate. Still
  `gate:machine` + not `no-auto`, exactly as ADR-0025 implemented it, and
  unchanged: on a row whose deliverable was never a preference, the close asks
  for a taste read that does not exist.
- *Is the act the owner's?* — **`no-auto`**, which ADR-0025's same-day amendment
  had already reached for and correctly made veto the relabel as well.

Nothing in `qops/reconcile.py` or `automerge.yml.tmpl` changes. What changes is
**which rows are `gate:machine` in the first place**, which is upstream of both.

## Consequences

- **24 of 33 open `gate:taste` rows become `gate:machine`** under the predicate
  (`docs/2026-08-20-gate-audit.md` has the row-by-row). Nothing moves the other
  way: all 19 open `gate:machine` rows stay.
- **Four of those 24 need `no-auto` added in the same edit** (ps#31, #55, #76,
  #93): their owner-need is authority — standing up a cloud project, activating
  programmatically, publishing a listing — and dropping the gate without adding
  the flag would lose a control rather than move it.
- **ADR-0024's reasoning for qops#19 is corrected.** It labelled #19
  `gate:taste` "for exactly that reason" — no template change reaches per-machine
  state. That is verification reach, not judgement. #19 becomes `gate:machine`;
  its detectable half (`doctor` warns when the workspace is untrusted) is a test,
  and its human half is `type:manual`.
- **Failure mode this creates:** a row whose deliverable *was* a preference gets
  planned, granted and shipped as machine work, and the owner reads a decision
  already taken. The three controls downstream (`state:planned`, `ready:auto`,
  R8's named test) each require the owner to look at the row before it can run,
  so this needs **three** misses and not one. Accepted on those odds, and the
  mitigation is the predicate's sharpness, not the restoration of the coin flip.
- **Not taken here:** the eligibility automation itself (#25 steps 2–4). This
  makes the predicate mechanically decidable by a triager; it does not build the
  triager that applies it. Scoped in the audit doc's last section.
