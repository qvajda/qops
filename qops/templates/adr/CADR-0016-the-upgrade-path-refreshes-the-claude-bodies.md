---
status: accepted
revisit-after: 2026-12-15
amends: 0030
depends-on: 0025, 0035
---

# The upgrade path has to refresh the `.claude/` bodies too

**Date:** 2026-08-28 · **Amends:** the upgrade path's last consequence line
(bump the pin, `install`, `--labels`, `migrate`, `doctor`); depends on
recurring owner toil not being an implementation.

**Picked: A.**

## Context

The upgrade path closes with: bump the pin, `install`, `--labels`, `migrate`,
`doctor` — no separate upgrade mechanism needed, none should be built.

That sequence did not reach a clean `doctor`. `install.main`
(`qops/install.py`) rendered the workflows and `.claude/settings.json`, copied
the consumer ADRs, wrote `scripts/` and registered the pickup task. It never
touched `.claude/skills/<name>/SKILL.md` or `.claude/agents/<role>.md`. Only
`qops init` wrote those, and `init` refuses once `.qops/config.yml` exists.

So a consumer that bumped its pin to a tag whose skill bodies or role files
changed got `skill_body_drift` and `agent_drift` reporting stale copies, and no
verb that fixed either. The remaining exits were a hand copy, or declaring
`skills.accept_drift` / `agents.<role>.accept_drift` — a *standing*
declaration that the stale copy is fine, which permanently silences the check
that exists to catch exactly this.

A stale role file is not cosmetic: a role file IS the agent's instructions for
that session, so a stale one does not miss a feature, it makes the agent
behave by rules the owner already replaced.

## Decision

**`install` refreshes the `.claude/` bodies it already owns.**

`install` writes `.claude/skills/<name>/SKILL.md` for each name in
`skills.native` that qops ships a template for, and `.claude/agents/<role>.md`
for each agent role, honouring `skills.accept_drift`, `skills.native_skip` and
`agents.<role>.accept_drift` — the same predicates `skill_body_drift` and
`agent_drift` already read, so what the check exempts the writer skips, by
construction.

This closes the gap for every consumer with no new verb, no new config key and
no doc a consumer has to have read. It self-upgrades: `install` ships in the
pinned package, so bumping the pin gets the newer `install` before it runs.

`install` starting to overwrite files under `.claude/` is a category it has
half-touched already (it renders `.claude/settings.json`). `UNWRITABLE` bounds
what a *launched agent* may write in an unattended sortie; `install` is a verb
the owner runs. This does not widen `UNWRITABLE` and does not let a sortie
edit its own role.

An undeclared local change to a skill body or role file is silently replaced —
that is drift by definition, and `accept_drift` is the declaration. A consumer
who wants the edit kept says so once, in config, in the same key the check
reads.

## Consequences

- Running `install` after bumping the pin now leaves `doctor` clean of
  `skill_body_drift` and `agent_drift`, without a hand copy and without
  declaring `accept_drift`.
- A name in `skills.accept_drift`, a name in `skills.native_skip`, or a role
  with `agents.<role>.accept_drift: true` is left untouched by `install`.
- `UNWRITABLE`, and what a launched agent may write, is unchanged.
