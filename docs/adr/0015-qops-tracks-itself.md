---
status: accepted
revisit-after: 2026-11-01
---

# qops's own work is tracked as issues, not as a document — and it leaves this repo when the plugin repo exists

PRD v3 Phase 5, which is one line of policy and is written here because a line
of policy in a phase list is a line nobody trips over.

## Context

Every previous ways-of-working push lived as a planning document: v1, v2, v3,
the kickoffs, the go-live plan. The go-live plan reached 401 KB and ~100,000
tokens — past the point where a single file read can return it, so consulting it
means scripted extraction. That is root cause 1 reaching its end state, and it
is the strongest argument in the PRD because the PRD did not have to make it.

qops cannot fix that for the pipeline and reproduce it for itself.

## Decision

**qops's own work is issues, not a document.** From Phase 5 onward:

1. **`qhoto_printshop` issues track pipeline work only.** GL-numbered sorties,
   defects, live-run findings — the shop.
2. **qops's work lives in the qops plugin repo.** Its build, its bugs, its
   portability work, its next phase.
3. **Neither is a planning doc.** A PRD may state a decision; it may not hold
   state. When the two disagree, the issue wins — that rule is already in
   `CLAUDE.md` and this extends it to qops itself.

**Interim, until the plugin repo exists (2026-08-14):** the repo has not been
created — that is a separate, outward-facing act and it was not authorised in
the same breath as the policy. Until it is, qops issues stay in
`qhoto_printshop` carrying **`mission:qops`**, which is already in the taxonomy
(`.qops/config.yml`). `mission:qops` is therefore the exact query that has to be
migrated: `gh issue list --label mission:qops --state all`.

**Today that query returns nothing.** No qops-related issue exists in this repo
to move, so Phase 5's "move any qops issue that is currently here" is satisfied
by measurement rather than by work. The next qops issue filed here is a
migration item, not a resident.

## Consequences

- Phase 7's portability proof gets easier: a plugin whose own backlog lives in
  the project it was extracted from is not portable, it is entangled.
- The digest and `qops metrics` read one repo. When the split happens, both need
  a second `--repo`, and `.qops/config.yml`'s `repo:` key is the single place
  that changes.
- **The failure mode to watch for is a qops issue quietly filed here without
  `mission:qops`.** Then the migration query misses it and it becomes pipeline
  backlog forever. `groom.yml`'s label-hygiene job lists issues missing labels,
  and the digest now renders that list, which is the closest thing to a check
  this has.

**Revisit** when the plugin repo is created — at which point the interim
paragraph is deleted rather than amended, and the migration is one `gh issue
transfer` per `mission:qops` row.
