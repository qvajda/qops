# Triage Labels

The skills speak in terms of five canonical triage roles. This file maps those
roles to the actual label strings used in this repo's issue tracker.

**The defaults were not kept.** This repo already has a taxonomy — PRD v3 §9,
held in `.qops/config.yml` — and it was amended and signed off before any issue
existed. Taking the skills' default five would have created a **second**
vocabulary describing the same states, which is the duplication `/triage` says to
avoid. Every role below maps onto a label the taxonomy already defines; **no new
label is created by this mapping.**

| Label in mattpocock/skills | Label in our tracker | Meaning                                  |
| -------------------------- | -------------------- | ---------------------------------------- |
| `needs-triage`             | `state:triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`               | `state:blocked`      | Open, waiting on a named external party  |
| `ready-for-agent`          | `ready:auto`         | Fully specified, ready for an AFK agent  |
| `ready-for-human`          | `no-auto`            | Requires human implementation            |
| `wontfix`                  | `state:cancelled`    | Will not be actioned                     |

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the
corresponding label string from this table.

## Two riders the mapping carries, both load-bearing

1. **`ready:auto` is a control, not a description.** No row was given it at
   import (review finding D1), and a row cannot hold it while its `gate:` is
   `none`. `/triage` may propose it; the validator refuses it at import.
2. **`state:cancelled` is not `done`.** It exists because GL-29 was struck rather
   than completed, and closing it as `done` would be a lie the tracker then
   repeats. Map `wontfix` here and nowhere else.

Edit the right-hand column to match whatever vocabulary you actually use — but if
you do, change `.qops/config.yml` in the same commit. The validator reads that
file, not this one.

## The triage rules

R1–R8 were written for the 2026-08-17 sweep and lived in that sweep's plan
document, which is a session artefact. They are the substrate's rules, not that
session's, so they live here now and they travel with it.

| # | Rule |
|---|---|
| R1 | Close what is finished. A `state:done` issue that is still open is a lie in the queue. |
| R2 | Every open issue gets exactly one `type:`, one `state:`, one `gate:`. No exceptions, no `gate:none` survivors. |
| R3 | **`gate:taste` if and only if the owner's preference is an *input* the work cannot proceed without — the row's deliverable *is* a choice only they can make. Everything else is `gate:machine`.** The triager's question, answerable from the row alone: *if the owner never answers, can this row be finished at all?* **When unsure, `gate:machine`** — an unsure row is not a taste row, it is an underspecified one, and the answer to that is a stated criterion, not a label that parks it (ADR-0026). |
| R4 | `type:decision` is `gate:taste` by construction: its deliverable is a choice. **`type:research` is not.** A research row's deliverable is a *finding*, and a finding is not a preference — its finish line is "the finding is written where the row says". Where reading it is itself a choice, that is a separate `type:decision` row (ADR-0026). |
| R5 | `type:manual` never gets `ready:auto`, whatever its gate. If an issue is scriptable, retype it to `type:code` instead of relabelling around it. |
| R6 | **`no-auto` on anything whose completion path calls an endpoint the project forbids, spends, publishes, grants or acts in the owner's name.** That is *authority*, not judgement, so the flag carries it and the gate says nothing about it — a row may be `gate:machine` + `no-auto` (ADR-0026). |
| R7 | `ready:auto` requires `state:planned`. Triage alone cannot fill the auto queue. |
| R8 | **`ready:auto` requires a named test.** An issue is auto-eligible only if a test file it touches proves it done, and the issue says which one. |

**R8 is the size rule, and it is about the runtime rather than about quality.**
The full suite runs longer than a single Bash call may, and a `claude -p`
process exits with its turn — so a sortie whose evidence of doneness *is* the
full suite cannot finish, by construction. On 2026-08-18 two unattended sorties
(#57, #71) wrote their entire change, backgrounded the suite, and ended their
turn waiting for a notification that run can never receive. `qops doctor` holds
the checkable half at label time: a `ready:auto` issue whose body names no test
file is a problem. **At PR time it goes further (#27, ADR-0023):** the named
test is run red-before, green-after — once against the merge base with only
the named test files carried forward, once at HEAD — so a named-but-hollow
test (passes with or without the change) is caught mechanically instead of
resting on the owner's judgement.

## The three concerns the gate used to carry (ADR-0026)

`gate:taste` was doing three unrelated jobs at once, which is why it read as
arbitrary. They are separate, each has its own carrier already in the taxonomy,
and **each is decidable by the triager from the row alone**:

| Concern | The question | Carrier |
|---|---|---|
| **Judgement** | Is the deliverable *itself* the owner's preference? | `gate:taste` / `gate:machine` (R3, R4) |
| **Authority** | Is the *act* the owner's to take — spending, publishing, granting, activating, anything in his name? | `no-auto` (R6) |
| **Verification reach** | Can CI observe the finish line, or must a human hand? | `type:manual` vs `type:code` (R5) |

Authority is not taste. A row may be `gate:machine` + `no-auto`: the finish line
is checkable and the act is still the owner's. A row may be `gate:machine` +
`type:manual`: the criterion is stated, and a human is the only instrument that
can read it — `qhoto_printshop`#139, where the owner's tap was a measurement and
not a judgement.

**Inverting R3's default is safe because `gate:machine` confers no autonomy.**
`scripts/qops_pickup.py::eligible` requires `state:planned` **and** `ready:auto`
and no `no-auto`; `ready:auto` is the owner's alone (ADR-0023) and needs a test
that proves the work done (R8). A mislabelled `gate:machine` row with no plan
and no grant sits exactly where a `gate:taste` row sits — in the backlog. That
claim is the one a future change could quietly invalidate, so
`test_gate_machine_alone_confers_no_autonomy` asserts it: relax `eligible()` and
R3's default fails in the same commit.

**Still worth re-reading in a substrate repo.** Substrate work is unusually
machine-gateable — local code, a real test suite, no vendor endpoint — so R6
excludes almost nothing. It is also expensive to be wrong about: a bad
autonomous change to the substrate governs every project that consumes it. That
cost is held by `ready:auto` and R8, which is where it belongs.
