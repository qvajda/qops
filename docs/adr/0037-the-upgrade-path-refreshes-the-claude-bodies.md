---
status: proposed
revisit-after: 2026-12-15
amends: 0030
depends-on: 0025, 0035
---

# The upgrade path has to refresh the `.claude/` bodies too

**Date:** 2026-08-28 · **Session:** #228, filed while assessing a from-scratch
onboarding · **Amends:** ADR-0030's last consequence line (the five-step
upgrade path); depends on ADR-0025 (recurring owner toil is not an
implementation).

**Status is `proposed` on purpose.** This ADR states three exits and one
recommendation; the owner picks one, and the pick is this row's gate
(ADR-0036). The implementation is a separate row, blocked on the pick.

## Context

ADR-0030 closes with:

> The upgrade path for a consuming repo is therefore already complete once this
> lands: bump the pin, `install`, `--labels`, `migrate`, `doctor`. No separate
> upgrade mechanism is needed, and none should be built.

That sequence does not reach a clean `doctor`. `install.main`
(`qops/install.py:1783`) renders the workflows and `.claude/settings.json`
(`render_all`), copies the consumer ADRs (`render_adr_consumer`), writes
`scripts/` and registers the pickup task. It never touches
`.claude/skills/<name>/SKILL.md` or `.claude/agents/<role>.md`. Only
`qops init` writes those (`qops/init.py:106-122`), and `init` refuses once
`.qops/config.yml` exists (`qops/init.py:80`).

So a consumer that bumps its pin to a tag whose skill bodies or role files
changed gets `skill_body_drift` (`qops/install.py:635`) and `agent_drift`
(`qops/install.py:677`) reporting stale copies, and no verb that fixes either.
The remaining exits are a hand copy, or declaring `skills.accept_drift` /
`agents.<role>.accept_drift` — a *standing* declaration that the stale copy is
fine, which permanently silences the check that exists to catch exactly this.

A stale role file is not cosmetic. `agent_drift`'s own docstring: a role file
IS the agent's instructions for that session, so a stale one does not miss a
feature, it makes the agent behave by rules the owner already replaced.

This is ADR-0025's shape. The gap recurs on **every** consumer's **every**
upgrade, and its only remedy is a human hand on a keyboard copying files, so
the design is unfinished. ADR-0030 is a taken decision and not a variable —
this is the amendment to its consequence line, not a route around it.

## Proposals

### A — `install` refreshes the `.claude/` bodies it already owns

`install` writes `.claude/skills/<name>/SKILL.md` for each name in
`skills.native` that qops ships a template for, and `.claude/agents/<role>.md`
for each of `AGENT_ROLES`, honouring `skills.accept_drift`,
`skills.native_skip` and `agents.<role>.accept_drift` — the same predicates
`skill_body_drift` and `agent_drift` already read, so what the check exempts
the writer skips, by construction.

**Reach:** closes the gap for every consumer with no new verb, no new config
key and no doc a consumer has to have read. It self-upgrades: `install` ships
in the pinned package, so bumping the pin gets the newer `install` before it
runs.

**Cost:** `install` starts overwriting files under `.claude/`, which is a
category it has half-touched (it renders `.claude/settings.json` already) and
a category `UNWRITABLE` (`qops/install.py:961`) protects. Those are different
things: `UNWRITABLE` bounds what a *launched agent* may write in an unattended
sortie; `install` is a verb the owner runs. This proposal does not widen
`UNWRITABLE` and does not let a sortie edit its own role.

The real cost is a hand edit nobody declared: an undeclared local change to a
skill body is silently replaced. Two precedents already in the tree disagree
about that — `render_all` overwrites unconditionally (a hand edit is a defect,
ADR-0024), `write_scripts` leaves a differing file untouched and warns
(`qops/install.py:110`). Under A, an undeclared difference *is* drift by
definition and `accept_drift` is the declaration; a consumer who wants the
edit kept says so once, in config, in the same key the check reads.

### B — a separate verb (`qops upgrade`, or `install --refresh-claude`)

Same writes as A, behind their own name, so `install` keeps its current
meaning ("render what config produces") and the overwrite is an act the owner
asks for by name.

**Reach:** identical to A once run, and strictly worse when not run — an
upgrade path with a sixth step is a step a consumer forgets, and forgetting it
is silent until `doctor` goes red with a message they have already learned to
answer with `accept_drift`.

**Cost:** ADR-0030's consequence line does not merely *not cover* this, it
forbids it — "No separate upgrade mechanism is needed, and none should be
built." Picking B means this ADR **strikes** that sentence rather than
amending it, and takes on the argument that made it: a second mechanism is a
second thing to keep in step with the first. A new verb also touches
`docs/reference/qops-contract.md`, which is frozen, so it lands on the
schema-change cadence rather than inside a consumer's first week.

### C — leave `install` alone, document the copy

Amend ADR-0030 to say the path is five steps plus a hand copy of the drifted
bodies, and make `skill_body_drift` / `agent_drift`'s existing messages the
documentation (they already say "merge the update by hand").

**Reach:** costs nothing to build and is honest about today's behaviour.

**Cost:** it is the option ADR-0025 names as unfinished — recurring owner toil,
once per consumer per upgrade, on a defect the substrate already detects. It
also leaves the wrong reading of the message ("declare `accept_drift`")
cheaper than the right one, which is how a check that exists to catch stale
agent instructions gets switched off by the person it protects.

## Recommendation

**A.** It reuses a mechanism already proven by two surfaces in the same verb,
adds no step to the upgrade path and no key to the contract, and its exemptions
are the checks' own predicates rather than a second list that can disagree with
them. B buys only the naming and pays ADR-0030's own prohibition for it; C is
the status quo with a paragraph.

If A's overwrite of an undeclared hand edit ever bites, the refinement is a
three-way check — refresh only a body byte-identical to the version the
consumer last installed, report the rest — and `skills-lock.json` is where
that version would be recorded. That is a later row, not a reason to prefer B
or C now: it is the same write with a narrower predicate.

## Consequences of the pick

Whichever is picked, the implementation row owes an assertion (the critic of a
decision is a test), and for A it is the one that would have caught this:
scaffold a consumer with `init`, stale a skill body and a role file, run
`install`, and assert `doctor` is clean — plus a second that a name in
`skills.accept_drift` and a role with `accept_drift: true` are left untouched
by that same run.

ADR-0035 applies once the behaviour lands, not now: this decision is about a
verb consumers run, so the implementation row carries the `CADR-` copy into
`qops/templates/adr/`. A proposal with no pick has nothing to copy.

## Explicitly not in scope

- What `skill_body_drift` or `agent_drift` **detect** — unchanged by all three.
- `UNWRITABLE`, and what a launched agent may write. Untouched, deliberately.
- The implementation, which is the row this one blocks.
