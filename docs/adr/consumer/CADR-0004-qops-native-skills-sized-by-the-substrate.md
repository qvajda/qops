---
status: accepted
revisit-after: 2026-11-01
supersedes: 0013
---

# Three qops-native skill bodies; the substrate carries the rest; the external set is uninstalled

**Date:** 2026-08-15 · **Session:** Phase 7 · **Supersedes:** ADR-0013 ·
**Approves:** proposal §3 option B sized by C, owner sign-off item 5.

## Context

ADR-0013 adopted eleven external skills as editable copies and recorded its own
discomfort: three or four implementations of each role coexisting, *"the exact
shape of the sprawl this overhaul exists to remove"*, with **displacement owed,
not done**. It named the count as the mitigation and told the next reviewer to
check it.

Checked. **Nineteen skill directories are installed against eleven accepted**,
and nothing noticed. Displacement is still owed.

The Phase 6 acceptance sortie then failed three times in the skill layer:
`grill-me` pinned without `grilling`; `tdd` referencing `codebase-design` while
the local copy is still the pre-rename `domain-modeling`; a pinned set spanning
several upstream commits.

**All three have one root cause.** `skills-lock.json` records `source`,
`sourceType`, `skillPath` and `computedHash` — and **no upstream ref**. No
commit, no tag, no date. Each install took whatever `main` was at that minute
and nothing wrote down which minute. Drift is not the problem; **drift that
cannot be observed** is. A lock that cannot say what it pinned cannot detect
that upstream moved, so the rename tax is paid at random, mid-session, by the
owner.

One claim was checked and dismissed before it could carry weight: the report
that the external set's author now recommends against `grill-me` **does not
hold** — upstream's README lists it as active and `grilling` as the engine
behind it, `triage` and `wayfinder`. The real finding is smaller and worse:
`grill-me` is a six-line wrapper whose whole body is *"Run a `/grilling`
session."* We took a dependency on a shim and left the body uninstalled. That is
a packaging failure of ours, and it is the argument for owning the procedure.

## Decision

**Write three qops-native bodies. Let the substrate carry the rest. Uninstall
everything outside the declared set.**

The three that carry sequencing and cannot be a one-line hook message:

1. **interview** — the design-tree round mechanic (what `grilling` does).
2. **spec→issue** — synthesis that writes *this* label taxonomy, not a generic
   one.
3. **triage** — the state machine, reading `.qops/config.yml` rather than
   re-declaring its own vocabulary.

Everything else is substrate. A hook can inject the next step **at the moment it
matters**, which beats a library the model must first decide to consult. The
real axis was never skills-versus-no-skills; it is instruction-at-the-moment
versus instruction-in-a-library, and the substrate wins the first.

**Deletion was argued against, not merely listed.** A hook is a gate and a gate
can only say no; it cannot say what to do next. The one session where the flow
held was the session where the owner supplied the sequencing by hand. Deleting
the whole layer does not close that gap — it makes the gap silent, and the owner
goes on typing the sequence with nothing written down to type. Hence three
bodies, not zero.

**Survivors get a ref.** Any external skill that stays — the Replicate set is
genuine external domain knowledge — gains an upstream ref in
`skills-lock.json`, so drift becomes *detectable* instead of *discovered*.

**`qops doctor` asserts the installed set equals the declared set.** ADR-0013's
mitigation was a count a human was asked to re-read. That failed. The count
becomes a check.

## Consequences

**ADR-0013's outstanding displacement is paid in this pass**, not deferred
again. Nothing was uninstalled in Phase 3 and the debt compounded to nineteen.

**Seven `disable-model-invocation` frontmatter lines collapse into one rule.**
Deciding invocability per external skill, by hand, was itself the sprawl.
ADR-0019 states the rule for the two that matter.

**The interview procedure becomes ours to maintain** — a few dozen lines of
prose that nothing tests. That is the accepted cost, and it is smaller than an
unbounded rename tax landing on the scarce resource.
