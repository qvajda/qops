---
status: accepted
revisit-after: 2026-11-01
---

# When a human is required: interview at mission level, propose-and-pick at sortie level, nothing at `gate:machine`

**Date:** 2026-08-15 · **Session:** Phase 7 · **Approves:** proposal §2, owner
sign-off item 4 (named highest priority of the seven).

## Context

Every issue got identical treatment regardless of its gate. A lint fix and a
spec change both opened with the same interview, so a small mechanical item
consumed an owner session. The owner's time is the scarce resource and the
routing rule is the only thing that spends it deliberately.

The labels needed to decide this already exist in `.qops/config.yml` —
`gate: [machine, taste, none]`, `mission:`, `ready:auto` — and
`validate.require_on_open` already forces a `gate:` label onto every open issue.
Nothing read them for routing. The input was there; the rule was not.

## Decision

**Contact is a function of the gate, not of the agent's comfort.**

| Level | Trigger | Owner contact |
|---|---|---|
| Mission | `type:epic`, or any change to an ADR, a hard constraint, or the spec | **Interview.** Full grilling round before any issue is written |
| Sortie | one issue, one session | **Propose-and-pick.** One round, ≤4 options, each with a recommendation |
| `gate:machine` | mechanical, machine-checkable | **None before review.** Plan, build, PR, CI. The owner meets it green, once |
| `gate:taste` | judgement on an artefact | Owner sees the **artefact**, not the diff — a render, a digest entry, a draft |

Four rules bind the table:

1. **A mission mis-set costs every sortie under it.** That is why the expensive
   treatment sits there and only there.
2. **Two rounds means it was never a sortie.** If acceptance criteria cannot be
   written after one propose-and-pick round, escalate to a mission interview.
   Do not ask a second time at sortie level.
3. **Escalation is always allowed; demotion never is.** An agent may promote
   `gate:machine` to `gate:taste` when the work touches a constraint. It may not
   demote `gate:taste`.
4. **A taste review is legitimate only once the machine gate is green.** Already
   the stated design of `gate.yml`; this ADR makes it a routing rule too.

## Consequences

**`qops brief` prints the routing verdict for the active issue.** The rule is
useless in a skill body — that is a preference (CLAUDE.md). It goes where it is
read unasked, every session, for 83 tokens.

**`ready:auto` + `gate:machine` means proceed.** No message, no check-in, no
"shall I start". The combination is the owner's standing instruction, granted
once at label time rather than per session.

**`gate:none` blocks `ready:auto`.** Already true at import (review finding B7);
this ADR is the reason it matters — an unrouted issue must not be autonomously
picked up.

**This is the fix the babysitting regression needed.** Everything else in Phase 7
queues behind it, by owner instruction.
