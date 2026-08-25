---
status: accepted
revisit-after: 2026-11-01
---

# The branch convention is enforced by a hook; unfinished work is recorded, never blocked

**Date:** 2026-08-15 · **Session:** Phase 7 · **Amends:** ADR-0001 ·
**Approves:** proposal §1, owner sign-off items 1–3.

## Context

Spec → branch → TDD → PR → CI → merge → delete branch is written in skill bodies
and in `docs/reference/qops-cheatsheet.md`, and nothing makes it happen. The
owner supplied the sequencing by hand for a full session. That is the cost the
overhaul exists to remove, and it is the repo's own lesson: **an instruction in
a prompt is a preference, not a control** (CLAUDE.md, GL-53).

## Decision

### 1. `to-spec` becomes model-invocable; `triage` stays owner-only

Not the same risk, so not the same answer.

`to-spec`'s `disable-model-invocation` blocked the exact reflex we want — the
agent noticing a spec is missing and writing one. But `to-spec` does not only
synthesise, it **publishes to the tracker**, so an unrestricted version can open
issues mid-conversation from a half-formed discussion. **The invocation is
allowed; the publish stays owner-confirmed.** Reflex kept, blast radius zero.

`triage` walks a state machine over many issues and relabels in a batch. `gh
issue list` is the source of truth, so a mis-read taxonomy corrupts the thing
every future session reads first. **Owner-only.** The reflex we want is not what
triage provides.

### 2. PreToolUse hook: Edit/Write require a `<type>/<issue#>-<slug>` branch

Blocks editing on `master`, editing on a branch with no issue behind it, and the
"I'll just fix this quickly" path.

**Scoped, because the false-positive list is the part that matters.** A guard
that blocks real work is how people learn to bypass guards.

- **Enforced on:** tracked files under `pipeline/`, `scripts/`, `qops/`,
  `tests/`.
- **Exempt:** `docs/`, `.qops/` (the hooks write there themselves), untracked
  files, the scratchpad.
- **Escape:** a `no-issue/<slug>` branch passes and **writes a ledger row.** An
  escape nobody can count is a bypass; one that surfaces in the brief is a
  nudge.

Known legitimate work this would otherwise have blocked, and why the scoping
exists: four of fifteen remote branches in this repo carry no issue number
(`docs/phase6-baseline`, `proto/mockup-scene-prototype`, `gl45-telegram-drops`,
`worktree-gl7-cron-orchestrator`) — the convention is aspirational, not current;
writing the spec that *creates* the issue cannot know the issue number; detached
HEAD during rebase, bisect or `gh pr checkout` has no branch name to match; a
sweep across several stage loops (the GL-54 shape) is one branch and several
issues.

### 3. The Stop hook records; it never refuses

**Rejected as originally proposed.** A Stop hook that refuses to end a session
on a dirty tree or an unmerged branch blocks work that is *supposed* to be
parked — this repo did exactly that twice (GL-6 attempt 1 was left uncommitted
on purpose, pending research) — and its only available move is refusing to end
the session, which is not a nudge. Stop hooks also re-enter themselves, and a
guard that must be defused with `stop_hook_active` to be usable is one the owner
disables inside a week.

**Instead:** on Stop, write the unfinished state to the ledger — branch,
ahead/behind, dirty paths, open PR — and the next `SessionStart` brief **leads**
with it.

## Consequences

**Enforcement moves to the place that is already always read.** The brief costs
83 tokens and enters every session unasked. Nagging there is free and cannot
strand anyone.

**The escape hatch is a metric, not a hole.** `no-issue/` usage is countable; if
it becomes the common path, the convention is wrong and this ADR is what gets
amended.

**Local hooks are a convenience an agent can disable.** The server-side half
(`guard.yml`, branch protection with `enforce_admins`) remains the half that
cannot be. This ADR adds sequencing, not security.
