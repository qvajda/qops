---
status: accepted
revisit-after: 2026-11-15
depends-on: 0023, 0025, 0028
---

# A migration proposes, the owner approves once

**Date:** 2026-08-22 · **Session:** the five-goal interview, rounds one to three ·
**Depends on:** ADR-0028 (the filing is the licence), ADR-0023 (batch approval),
ADR-0025 (recurring owner toil is not an implementation).

## Context

A repo pinned to an old qops drifts in two halves. The **file** half is already
solved and needs no new mechanism: `qops install` re-renders the workflows from
the templates plus config, `scripts/qops_import.py --labels` creates whatever
labels the taxonomy has since gained, and `qops doctor` reports what is left.

The **issue** half has nothing. `qops/install.py` `issue_invariants` *reports*
that an open row is missing `origin:`, a `gate:`, or ADR-0028's machine-turnable
outcome statement. Nothing fixes it. `qhoto_printshop` is pinned roughly seventy
PRs back and carries thirty-five open rows filed before any of those rules
existed. Every future consumer, and every future taxonomy change, pays the same
bill.

That is the shape ADR-0025 already named: a defect this substrate can detect is
a defect this substrate closes, and a gap that needs a human hand on a keyboard
*every time it recurs* is an unfinished design.

### The collision

The obvious fix — have the migrator relabel the rows and rewrite the bodies —
runs straight into ADR-0028. **The body is the licence.** A row may not leave
`state:triage` unless its body states an outcome a machine can turn into
criteria, and that statement is the one owner act the whole grant chain still
rests on. An agent that rewrites bodies writes its own licence.

Refusing to touch bodies does not resolve it either. It just relocates the toil
back onto the owner, thirty-five rows at a time, which is the thing ADR-0025
forbids. Both horns are real.

## Decision

**A migration proposes everything and applies nothing until the owner accepts
one diff.**

Concretely:

1. **`--dry-run` writes nothing.** It emits a single diff over all open rows:
   label adds and removes, a proposed body rewrite where ADR-0028's outcome
   statement is absent, and a disposition per row — `keep`, `transfer <repo>`,
   or `close`. Not a per-row prompt. One artefact, reviewed in one sitting.
2. **`--execute` applies that diff whole or not at all**, and refuses if the
   corpus moved since the dry-run produced it.
3. **`--verify` re-reads the tracker** and asserts every row landed in its
   proposed disposition, with rows in equal to rows kept plus transferred plus
   closed. A half-migrated repo is the failure mode worth naming: it looks
   finished and is not.
4. **Closed rows are never touched.**

## Why this is not a hole in ADR-0028

Because the owner reads and accepts every body before it exists on the tracker,
the filing is still his. The licence is granted at acceptance instead of at
composition, which is precisely the shape ADR-0023 already uses for
`origin:agent` rows: an agent may propose `ready:auto`, and the owner grants it
**by batch approval, never by a lone agent's confidence.** This extends that
mechanism to bodies; it does not invent a second one.

It also matches how this owner actually works, stated in the #25 interview and
unchanged since: he sets direction and clears ambiguity, and does not write the
plans. On a corpus seventy PRs stale the provenance of a row is genuinely
unknown — which is the argument *for* the review, not against the rewrite.

## Consequences

- A migration is two sorties, not one: the verb (#103, `gate:machine`) and its
  run against a real corpus (#105, `gate:taste`, `no-auto`). Building the
  migrator against an imagined corpus is how it ships wrong.
- Cross-repo issue transfer is irreversible and stays `no-auto`.
- The upgrade path for a consuming repo is therefore already complete once this
  lands: bump the pin, `install`, `--labels`, `migrate`, `doctor`. No separate
  upgrade mechanism is needed, and none should be built.

## The critic

An instruction in a prompt is a preference. The assertions, in
`tests/test_qops.py` under #103:

- `--dry-run` against a fixture corpus leaves the tracker byte-identical — the
  test asserts the absence of the write, not the presence of the diff.
- `--execute` against a corpus that moved since the dry-run exits non-zero and
  applies nothing.
- `--verify` exits non-zero on a deliberately half-applied fixture.
