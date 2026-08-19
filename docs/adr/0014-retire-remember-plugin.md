---
status: accepted
revisit-after: 2026-11-01
---

# The Remember plugin is retired; qops carries session memory

The Remember plugin is disabled and `.remember/` is deleted from the working
tree. `qops ledger` / `qops resume` / `qops brief` (PRD v3 Phase 4) are the
replacement.

## Context

This was never a disk-space question — `.remember/` was 1.3 MB. It is an
**overlap** question. `.qops/ledger.jsonl` was specified from the start as the
session-continuity record, and Phase 4 builds `ledger`, `resume` and `brief`,
which is what Remember already did by another route: a rolling `now.md` buffer,
per-day narrative files, a 7-day `recent.md`, an `archive.md`.

Keeping both would stand up **two memory systems writing overlapping state about
the same sessions**. That is finding E5 — GSD state duplicating the issue body —
reproduced in a second location, and root cause 1 in two places. E5's lesson is
that the duplicate does not stay in sync, and that the reader then has to know
which copy is authoritative. One authority, or none.

Order was load-bearing and is recorded because it is easy to get wrong: **disable
the plugin → re-snapshot → verify the count both ways → amend the manifest →
delete.** Deleting first is a no-op, because a live plugin recreates the
directory on its next hook. E14 refused to delete for exactly this reason and was
right to.

## Decision

Retire it. The archive is the record, not the working tree.

- Plugin disabled by the owner (a UI action, 2026-08-13 ~23:47).
- `.remember/` re-snapshotted after quiescence — 62 files, `sha256`
  `d86d38f5…c71b521`, verified equal to
  `find .remember -type f ! -path '.remember/tmp/*' | wc -l`. Recorded in
  `docs/archive/2026-08-13-remember-sdd-snapshot-manifest.md` §2b, which
  supersedes §2 for `.remember/`; §2 remains valid for `.superpowers/sdd/`.
- Archive handed to the owner, **not** saved inside the working tree — the GL-51
  failure shape is a file with no second copy sitting in a directory tooling is
  entitled to delete.
- `.remember/` deleted. `.superpowers/` was already gone.

## Consequences

**Lost, and named rather than glossed:**

- `now.md` — the live in-session scratch buffer. qops has no equivalent and does
  not want one; the transcript is that buffer.
- The **narrative dailies** (`today-*.md`, `recent.md`, `archive.md`) — prose
  summaries of what a day did, auto-injected at `SessionStart`. `qops brief` is
  capped at 400 tokens and is state-shaped, not narrative. A session that wants
  the story of 2026-08-10 now reads git log and the issues, not a paragraph
  someone's hook wrote.
- Automatic capture. Remember wrote without being asked; `qops ledger` is
  written by hooks at defined points. Anything outside those points is not
  recorded at all. This is deliberate — unattended capture is what produced 87
  files nobody read — but it is a real loss and the next reviewer should check
  whether `brief` is actually enough at resume time.

**Gained:** one memory system. The `SessionStart` injection budget stops being
shared between two writers, and the ledger is queryable state rather than prose.

**Reversal cost:** low, deliberately. Re-enable the plugin and extract the
archive. Nothing in the pipeline or in qops reads `.remember/`.

**Revisit** if `qops resume` proves insufficient in practice — the failure
signal is a session re-deriving context that a daily would have carried. The fix
then is a narrative field in the ledger, not a second plugin.
