---
status: accepted
revisit-after: 2026-11-01
---

# `ready:auto` still requires exactly one owner touch — which touch depends on who filed the issue

**Date:** 2026-08-19 · **Session:** interview on #25 · **Amends:** the
`ready:auto` bullet in `CLAUDE.md`, extends ADR-0017.

## Context

#25 named the actual bottleneck: steps 2–4 of the issue-to-merge chain
(label, plan, grant) are manual, and only step 5 (execute) is automated. The
owner's instinct on being asked to close that gap was to have an agent set
`ready:auto` directly. That collides with a standing hard constraint:

> `ready:auto` is the owner's to grant, and only the owner's. The triager is
> forbidden from applying it, and the importer refuses it at import.
> (CLAUDE.md)

and with ADR-0017, which treats the grant as the control boundary precisely
*because* it is a judgement call, not a mechanical one.

The resolution that survived interview is not "agent grants it" — it is that
the owner is still involved exactly once before an issue is picked up, but
*which* touch counts as that one contact depends on how the issue came to
exist:

- **Self-filed** (owner present, interactive session): the owner's one touch
  is the act of filing the issue itself. Once a deterministic, machine-checked
  proof of doneness exists (R8), nothing further should require the owner.
- **Second-hand** (an agent files it mid-sortie, owner never present at filing
  time): the owner has not touched it yet. A confidence signal is proposed,
  and the owner's one touch is a batch approval — reading reasoning across a
  set of proposals, not authoring a plan from scratch.

## Decision

**`ready:auto` eligibility is a function of `origin:` × the strength of R8,
not of an agent's judgement.**

| `origin:` | Path to `ready:auto` | What decides it |
|---|---|---|
| `origin:owner` | Granted automatically once R8 holds | A machine check: does a named test exist, currently fail without the fix, and pass with it. Deterministic — no agent opinion involved. |
| `origin:agent` | Proposed, never applied, by a confidence signal; owner batch-approves | Judgement — the same class of decision ADR-0017 already reserves for `gate:taste`. |

Every issue gets an `origin:` label at filing time, mechanically — set by
which path created it (interactive session vs. a running sortie), never
inferred after the fact. `qops doctor` asserts every open issue carries one,
the same way it already asserts every issue carries a `gate:`.

**R8 must be strengthened before the `origin:owner` path is safe.** Today
(`qops/install.py:_NAMES_A_TEST`) R8 only checks that the issue's plan *names*
a test file — a regex match, not a proof. That is gameable: a named test that
asserts nothing still satisfies today's check. The `origin:owner` auto-grant
must not go live against the current R8; it needs the stronger form (test
exists, fails red without the change, passes green with it) first.

**The `origin:agent` path gets a new agent role, not a new grant.** It scores
confidence and writes the proposal (label or comment); it never writes
`ready:auto` itself. The batch-approval view must show the stated reasoning
per issue, not just a checkbox — a checkbox list degrades into the same
rubber-stamp problem this ADR exists to avoid.

## Consequences

- `CLAUDE.md`'s `ready:auto` bullet is amended: still "the owner's to grant,
  and only the owner's" in effect, but the mechanism by which the owner grants
  it on `origin:owner` issues is the filing act itself, checked mechanically
  afterward — not a second manual label edit.
- Three follow-on issues, filed against `qvajda/qops`:
  1. `origin:` label + `qops doctor` presence check.
  2. Strengthen R8 from "names a test" to "test proves it" (red-before,
     green-after).
  3. Wire the `origin:owner` auto-grant once (1) and (2) exist; new
     confidence-proposer agent + batch-approval view for `origin:agent`.
- **Open question, not resolved here:** if batch-approval on the `origin:agent`
  path becomes a rubber stamp in practice, the owner's stated fallback is to
  measure approval rate against stated confidence and consider widening
  autonomy further — deferred, not designed, until there is data.
