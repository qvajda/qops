---
status: superseded
superseded-by: 0018
revisit-after: 2026-09-01
---

# Eleven external skills are adopted as editable copies, over incumbent equivalents

> **Superseded 2026-08-15 by ADR-0018.** The mitigation this ADR named — the
> count, and a next reviewer who checks it — failed exactly as it was written to
> prevent: nineteen skills were installed against eleven accepted, and the
> displacement it called *owed, not done* was never paid. ADR-0018 pays it,
> replaces the count with a `qops doctor` check, and keeps three qops-native
> bodies in place of the set.
>
> **`revisit-after` is 2026-09-01 and it is not about skills.** It is the
> rollback re-decision: Phase 7 rejected reverting the overhaul on figures
> measured over `n=5` scored sessions, which is a signal and not a result.
> Re-decide at `n ≥ 20` (`docs/2026-08-15-qops-phase7-enforcement-proposal.md`
> §5). `qops metrics` cannot window from the CLI today — fix that first, or the
> re-decision is an impression again.

Eleven skills — ten from `mattpocock/skills` plus `loopy` — are installed as
**editable copies** (`npx skills add --copy`), not as the auto-updating
marketplace bundle and not all 35 the repo now ships. Copies are pinnable and
diffable; `skills-lock.json` is tracked at the repo root and records source plus
content hash per skill, so the pin survives even though the bodies live under a
gitignored `.claude/skills/`.

**The uncomfortable half, recorded because §3.3 of the PRD never checked it:**
this install already carries superpowers, GSD, and built-in equivalents of
`code-review`, `tdd` and bug diagnosis. Three or four implementations of each
role now coexist. That is a real maintenance surface and it is the exact shape of
the sprawl this overhaul exists to remove.

**Why it is accepted anyway:** the eleven are what the qops design *routes
through* — `/wayfinder` for missions, `/to-spec` → `/to-tickets` for sorties,
`/triage`'s state machine for labels. The incumbents were never wired into a
workflow; these are. **The mitigation is the count, and the next reviewer should
check it:** eleven is a set one person can re-read. If a twelfth arrives without
displacing something, this ADR is being ignored.

**Displacement is owed, not done.** Nothing was uninstalled this session —
nothing is deleted before PRD Phase 3.
