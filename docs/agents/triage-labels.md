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

R1–R7 were written for the 2026-08-17 sweep and lived in that sweep's plan
document, which is a session artefact. They are the substrate's rules, not that
session's, so they live here now and they travel with it.

| # | Rule |
|---|---|
| R1 | Close what is finished. A `state:done` issue that is still open is a lie in the queue. |
| R2 | Every open issue gets exactly one `type:`, one `state:`, one `gate:`. No exceptions, no `gate:none` survivors. |
| R3 | `gate:machine` = the finish line is checkable by tests or CI. `gate:taste` = the finish line is a judgement call (visual, commercial, brand, legal). **When unsure, `gate:taste`** — a wrong `machine` label produces an autonomous sortie that ships a taste decision. |
| R4 | `type:research` and `type:decision` are `gate:taste` by construction: their output is a finding for the owner, not a passing test. |
| R5 | `type:manual` never gets `ready:auto`, whatever its gate. If an issue is scriptable, retype it to `type:code` instead of relabelling around it. |
| R6 | No `ready:auto` on anything whose completion path calls an endpoint the project forbids without an explicit go-ahead — the sortie cannot finish unattended by definition. |
| R7 | `ready:auto` requires `state:planned`. Triage alone cannot fill the auto queue. |
| R8 | **`ready:auto` requires a named test.** An issue is auto-eligible only if a test file it touches proves it done, and the issue says which one. |

**R8 is the size rule, and it is about the runtime rather than about quality.**
The full suite runs longer than a single Bash call may, and a `claude -p`
process exits with its turn — so a sortie whose evidence of doneness *is* the
full suite cannot finish, by construction. On 2026-08-18 two unattended sorties
(#57, #71) wrote their entire change, backgrounded the suite, and ended their
turn waiting for a notification that run can never receive. `qops doctor` holds
the checkable half: a `ready:auto` issue whose body names no test file is a
problem. The judgement half — whether the named test actually proves the thing —
stays the owner's, like every other `ready:auto` grant.

**R3 is worth re-reading in a substrate repo.** Substrate work is unusually
machine-gateable — local code, a real test suite, no vendor endpoint — so R6
excludes almost nothing and `gate:machine` is cheap to apply. It is also
expensive to be wrong about: a bad autonomous change to the substrate governs
every project that consumes it.
