---
status: accepted
revisit-after: 2027-02-01
---

# The substrate repo is public, and the licence is MIT

**Date:** 2026-08-19 · **Session:** Phase 8, P8.2 · **Inherits:** the source
repo's ADR-0012.

## Context

`qops` was extracted from `qvajda/qhoto_printshop`, which is public by an
earlier decision (ADR-0012). The extraction could have inherited that silently.
It does not: an inherited decision nobody restated is a decision nobody can find
when it matters, and the two repos do not have the same exposure. The source
repo is one shop's pipeline; this one is a substrate that other projects install,
which makes it the more consequential of the two to get wrong.

Two properties of the choice were flagged as irreversible before it was made:

- **A public repo cannot be un-published** for anyone who has already cloned it.
  Making it private later removes convenient access and nothing else.
- **The licence is a one-way door for contributions.** Relicensing needs the
  agreement of everyone who has contributed under the old terms; a permissive
  licence chosen once stays chosen.

## Decision

**Public, MIT.**

Public because the substrate's value is that a second project can install it,
and because nothing in it is a secret: there are no credentials, no vendor
endpoints, and — by construction, asserted in `tests/test_qops.py` — no
project's vocabulary. Its own tripwire list is empty.

MIT because there is no patentable surface in a ways-of-working substrate, so
Apache-2.0's patent grant buys nothing, and the shortest licence is the one
nobody has to read before using it. Apache-2.0's §5 (inbound contributions
arrive under the same terms) was the one real argument for it and does not yet
have a contributor to serve.

## Consequences

**Secrets are rotated, never rewritten out.** If one is ever found here, the
first action is rotation. Rewriting history is a separate decision and has never
been the fix — a rewrite reduces convenient access to something already public,
it does not un-leak it. This repo's history is not rewritten (`CLAUDE.md`).

**No credential may enter this repo, including in a test fixture.** A fixture
that looks like a token teaches the next reader that tokens live here.

**If a contributor ever appears, the inbound terms are the licence and nothing
else.** No CLA, no separate contribution agreement — and if that stops being
adequate, that is a new ADR, not an edit to this one.

**Revisit** if the substrate ever needs to carry something that cannot be
public, at which point the honest move is a second private repo rather than
flipping this one.
