---
status: accepted
revisit-after: 2027-02-01
---

# Priority is a namespace of its own, and parked is its floor

ADR-0026 split `gate:` into judgement, authority and reach, and its audit
(`docs/2026-08-20-gate-audit.md:95`) named a fourth concern the gate had been
carrying and left unassigned: *"parking is priority, and priority has never
been a gate. The label was being used as a snooze button."* #28 is a live
instance — `gate:taste` on an idea the owner does not want worked now — and
`qops pending` reports it under "waiting on you" on every run, forever, with
no action behind the nag. #106 asked for a per-issue critical override from
the other end and found the same gap: *"a new label namespace and therefore a
schema change."*

## Decision

`labels.priority` is a new, optional, zero-or-one namespace. One value today:
`parked`. Not added to `validate.require_on_open` — an unlabelled row is
normal priority, so the ten open rows at the time of this decision need no
migration.

`priority:parked` is owner-only to grant (same authority as `ready:auto`;
`.claude/skills/triage/SKILL.md` already forbids the triager from deciding
priority) and vetoes pickup, planning and decomposition through the one set
those three predicates already share (`install.BLOCKING_FLAGS`), rather than a
fourth `if` written into each. It is excluded from `qops pending`'s "waiting
on you" section on the same guard slot as a live claim: a parked row is not
waiting on the owner, it is parked by him.

Unparking a row must resume it at whatever `state:` it already carried —
parking never touches `state:`. It is a floor, not a queue position: `#106`'s
`priority:critical` and any ladder between the two is explicitly out of scope
here, its own sortie.

## What makes this wrong

Writing `priority:parked` into a third place `BLOCKING_FLAGS`, `waiting_on_owner`
and a hand-rolled predicate would disagree with the other two.
Adding `priority:` to `require_on_open` would invalidate every existing open
row for a namespace optional by design.
