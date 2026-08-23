---
status: accepted
revisit-after: 2026-11-23
depends-on: 0017, 0025, 0029
---

# An alert claims the row and opens a session, and the set it fires on is `pending`'s

**Date:** 2026-08-23 · **Session:** the #119 interview, rounds one to three ·
**Depends on:** ADR-0029 §6 (the floor this replaces), ADR-0017 (contact is a
function of the gate), ADR-0025 (an owner action is legitimate only where it is
a decision).

## Context

ADR-0029 §6 pinned the floor at a label and a digest line and named Remote
Control as the upgrade, citing **ADR-0002, which does not exist in this repo**.
The Telegram path was never the answer: `digest.yml.tmpl:123` posts under
`if: env.TELEGRAM_TOKEN != ''` and `qvajda/qops` has no such secret, so that
block has been a silent no-op since extraction. Owner decision, 2026-08-22: it
is replaced, not repaired.

**The predicate was not a blank page.** `pending.waiting_on_owner()`
(`qops/pending.py:75`, shipped 0c24b38) already enumerates what waits on the
owner, in five clauses. An ADR that invents a sixth definition gives the
substrate two answers to one question.

### What the ledger measured

Two days, 584 events, two owner interventions — both observed, neither reasoned:

**#82 struck out** at `2026-08-22T01:00:31` after three consecutive
`pickup_release "no commit and no PR"` runs. The loop then returned `rc:0` at
02:00, 03:00, 04:00, 05:00 and 06:00 — five clean hours while the top row was
dead. First owner session: 06:44:44. **≈5h45m, ended by the owner sitting
down.** Nothing told him.

**PR #109 went unjudged twice**, `[WinError 206] The filename or extension is
too long`, at 17:09 and 18:00. Both logged `"n": 1`: `review.attempts()` keys on
`(pr, sha)` (`qops/review.py:157`), so the re-push refilled the budget. The
cause is fixed (9dd5625, prompt to stdin); the counter keying is not. That is a
defect row, not an alerting decision.

One miss is strike-shaped. One is a strike counter that cannot reach three.

## Decision

### 1. The trigger set is `pending.waiting_on_owner()` — the function, not a copy

The alerter calls it. It defines no predicate of its own and holds no `state:`,
`gate:` or `no-auto` literal. Two lists that agree today diverge on the first
edit and neither looks wrong afterwards.

Each candidate trigger, accepted or rejected:

| Candidate | Verdict |
|---|---|
| **struck out** under #49 | **Accepted.** The one class with a measured miss (#82, 5h45m). |
| **`state:review`** | **Accepted.** The loop asked for eyes; nothing else supplies them. |
| **`gate:taste`** | **Accepted.** ADR-0017: the deliverable *is* the owner's judgement. |
| **`no-auto`** | **Accepted.** It withholds an act only the owner may take. |
| **`state:done` + `gate:machine`, still open** | **Accepted.** Its PR merged and the row cannot close itself. |
| **`state:blocked`** (#83's clarification) | **Rejected as its own trigger.** The clarification sub-issue carries the gate; when it needs the owner it is a `gate:taste` row and alerts in its own right. Alerting on the parent too pages twice for one decision. |
| **a red required check** | **Rejected, subsumed by strike-out.** ADR-0025: a defect the substrate can detect is one the substrate closes. It reaches the owner when it has burned three sessions, not before. The class that can never strike out — `attempts()` reset by a moving sha — is a defect against that counter, not a sixth clause here. |

### 2. Edge, not level — and a claimed row is not waiting

`gate:taste` and `no-auto` are conditions that persist for days. Alerting on
membership pages hourly forever; the alert gets muted, and a muted alert is
worse than the digest line it replaced. The alert fires on **entry** into the
set, including an entry the owner authored: filing a `gate:taste` row is a
request for the owner's judgement whoever typed it.

The claim is the record of having fired. Nothing else stores it — no ledger
flag, no comment marker, no local state. The tracker is the store, so a
reinstalled host and a second root read the same answer.

**The collision this closes.** `no-auto` and `state:review` are themselves
clauses of the set. Claiming a row with them creates a fresh edge and re-alerts
it, hourly, forever. So `waiting_on_owner()` learns the claim: **a row with a
live claim is *with* the owner, not *waiting on* him.** One change in the shared
function, both consumers correct — `qops pending` gains the same distinction.

### 3. The claim is `state:building` + `no-auto`, or `state:review`

Which one depends on what the session is for: building or fixing takes
`state:building` + `no-auto`; reviewing takes `state:review`. The row reads as
worked-on because it *is* — in an interactive session rather than an unattended
one. No new `state:` value: the pair already says exactly this, and the taxonomy
is a frozen surface (`docs/reference/qops-contract.md`).

### 4. The session opens with the row and a drafted proposal

Not the full `pending` render — two concurrent alerts would each recite the
other. Not the bare row either: that yields one reply, "advise what to do",
which is this shape with the proposal blocked behind an uninteresting turn.

**The session name is the triage surface.** Several sessions may wait at once —
that is delegation, not contention, and the owner picks where his attention
goes. The name must let him pick: a struck-out or blocked row has to stand out
from a `gate:taste` row idling until he has time. The name template encodes the
clause, not just the issue number.

### 5. Liveness: the hourly loop reaps, and re-alert is unbounded

Only the hourly loop emits. An alert therefore waits up to sixty minutes; that
is the price of one serialised emitter, and it is cheaper than two.

A session can die without releasing its claim — reboot, expired auth, a manual
close, an update. The row then looks worked-on and is not, which is the stall
this ADR exists to remove wearing the costume of progress. So:

- **The ledger records the mapping**, at launch, as `pickup` already does for a
  build: `{"event": "alert_launched", "issue": "…", "session": "…"}`.
  `session_start` alone cannot serve — it carries `session_id`, `cwd` and
  `branch`, and no issue number.
- **Liveness is proven by an observable that dies with the session** — the host
  reporting that session alive, checked each hourly pass. *Unverified
  assumption:* that RC sessions are enumerable by their templated name on this
  host. The implementation row proves it or falls back to a heartbeat the
  session writes itself; it does not proceed on the assumption unproven.
- **A dead claim is released**, and the next pass alerts again. No strike cap on
  the alert. A host-wide failure that kills every session kills the loop that
  launches them, so the runaway needs the loop healthy and the sessions dying —
  contingency for a fraction of the host failing while the rest survives is
  overkill for an unobserved case. If it is ever observed, strikes on the alert
  are the fix.

That is the re-alert policy in full: **while the claim is live, silence; when it
dies, the row returns to the set and alerts on the next pass.**

## The critic

An instruction is a preference; a check is a control (CLAUDE.md). Three
assertions, or this ADR is prose:

1. The alert module holds no `state:`, `gate:` or `no-auto` literal and reaches
   the set only through `pending.waiting_on_owner()` — the shape of
   `test_no_project_specific_string_outside_the_config`.
2. One alerts / does-not-alert pair per accepted clause, and a
   does-not-alert case for `state:blocked` and for a red check that has not
   struck out.
3. A row carrying a live claim is absent from the set; the same row with its
   claim reaped is present.

## Consequences

**The Telegram block goes with the implementation, not here.** It is dead but
inert, and removing the only alerting path before its replacement exists widens
the gap.

**`qops pending` changes too.** The claim distinction is in the shared function,
so the render gains "with you in session X" against "waiting on you". That is a
better render, and it is not optional — it is where the collision in §2 is
actually fixed.

**The implementation is a separate row and stays blocked on this one.** It
carries the RC transport, the name template, the reaper and the Telegram
removal.

**What would make this wrong:** an ADR that settles the transport and leaves the
predicate to the implementation. The transport is the easy half. Settling the
predicate in code means it was never decided — so the predicate is decided here,
by reference to a function that already exists, and the transport is not
mentioned beyond the fact that it is a session.
