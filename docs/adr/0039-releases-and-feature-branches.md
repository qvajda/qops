---
status: proposed
revisit-after: 2027-01-01
depends-on: 0016, 0020, 0021, 0027, 0029, 0038
---

# Feature branches and releases: the epic is the release unit, and the merge into `master` stays gated by a per-project owner act, not a rendering

**Date:** 2026-09-01 · **Session:** #274

## Context

Every sortie branches off `master`, opens a PR against `master`, and
`automerge-loop` merges it there on the required status checks (ADR-0020).
There is no seam between "a row is done" and "a set of rows is released": a
project whose `master` is in production gets each row landing in production
the moment its checks go green.

The version bump names the same gap directly: #258, #262 and #266 are three
hand-filed "bump the declared version" rows inside one week. CLAUDE.md's own
constraint — *recurring owner toil is not an implementation* — names that
exact pattern as a defect the substrate closes.

Three things must be settled before any of this is buildable, not designed
around:

1. **Branch protection is `master`-only, and it is what merges.** ADR-0016 and
   ADR-0020 make the required status checks the merge mechanism. A feature
   branch has none by default, so a `gate:machine` PR opened against one would
   merge on nothing.
2. **`protected_branches` is a flat, static list** (`qops/guard.py:426`,
   `.qops/config.yml:39`). A feature branch is created and destroyed per epic;
   a config list edited by hand cannot track that.
3. **`type:epic` is already the release-shaped noun.** `mission:` is a
   permanent lane; `type:epic` already carries an interview (ADR-0017) and
   automatic decomposition of its children (ADR-0029 §4). A fourth concept —
   milestone, feature, release — would split a taxonomy that already works.

## Options

**A. Do nothing; the version-bump row stays a recurring manual filing.**
Costs nothing to build and keeps every merge going straight to `master`.
Rejected on the CLAUDE.md line this row quotes: a defect the substrate can
detect and keeps not closing is not a neutral choice, it is toil left in
place with a document now naming it.

**B. A feature branch per epic, protected by a rendered GitHub branch-protection
ruleset the owner turns on once per epic; the epic's merge into `master` is the
release, carrying an automatic version bump derived from the epic's own scale
label.** This is the recommendation; detailed below.

**C. Skip branch protection on the feature branch entirely; gate the merge by
convention only (CI still runs, but nothing blocks a red merge).** Rejected:
this is exactly the hole the row exists to close — "a release mechanism that
merges into a feature branch without saying what gates that merge" is named
in the issue as the wrong answer by construction.

**D. Make the feature branch protection itself an unattended `gh api` write,
timed to epic creation.** Rejected outright: ADR-0038 draws the line at
*writes*, not at *reads* — `gh_api_refusal` refuses any `gh api` call carrying
a write flag when `ctx["unattended"]`, without exception for provenance or
timing. A `POST /repos/.../rulesets` at epic-creation time is a write the guard
refuses precisely because nobody is reading at that moment (ADR-0038's own
argument: the deny-vs-guard question was never about *which* write, only about
*whether anyone with standing to approve is present*). Protecting a branch
stays a `gh api` write and inherits ADR-0038 whole; it does not get a second
carve-out.

## Decision (Option B)

### 1. Feature branches and releases enter the substrate — scoped to the epic

Yes. The release unit is `type:epic` itself — no fourth noun. An epic already
means "direction the owner set, decomposed automatically below it"
(ADR-0029 §4); a release is that same set of rows, reaching `master` as one
unit instead of severally.

### 2. Binding: an epic *optionally* carries a feature branch, named from the issue

An epic issue gains a body field, `release-branch: <name>` — present only when
the epic is running in feature-branch mode. Its children's sorties branch from
and PR against `<name>` instead of `master` (the branch a sortie targets is
already read from state per ADR-0029's tracker-derived provenance, not
asserted; targeting `release-branch` instead of `default_branch` when present
is the same kind of derivation, not a new inference class). No `release-branch`
field, no change: an epic with none behaves exactly as every epic does today,
sorties straight against `master`. This is the toggle's finest grain — off by
default per epic, not merely per project.

### 3. What gates the merge that is not into `master`: the owner protects the
feature branch once per epic, at epic creation, the same session that ran the
interview

Branch protection is `master`-only *by default*, not by law: ADR-0016's table
(`required_status_checks`, `enforce_admins: true`, no force-push, no
deletion, zero required approvals) is a general recipe, reapplied by
`gh api` to `<release-branch>` the same way `qops init` already prints the
two ADR-0016 commands for the owner to run and approve at a prompt
(ADR-0038 §3). The epic interview session — already interactive, already the
owner at a keyboard, already the moment `type:epic` demands one (ADR-0017) —
prints a third command: protect `<release-branch>` with the same required
checks. The owner runs it there, once, or the epic runs in `master`-target
mode by default until they do. Nothing renders this automatically and nothing
in an unattended pass ever issues the write: this is squarely the case Option
D was rejected for, and ADR-0038 already drew that line — a `gh api` write
during an unattended pass is refused regardless of how confidently the epic's
own provenance would justify it.

Sorties under the epic build exactly as before — unattended, `gate:machine`
checks, `automerge-loop` merges each into `<release-branch>` the moment its
own checks are green, because those checks now run against the protected
feature branch, not `master`.

### 4. `protected_branches` becomes per-branch state read at guard time, not a
static list edited by hand

The flat list (`qops/guard.py:426`, `.qops/config.yml:39`) stays exactly as
it is for `master` and any project's other permanent branches — this is not
touched or widened in this decision. A feature branch's protection is instead
read live: `guard.py` already resolves another root's `protected` list
per-call (`other_roots.get(cpath)`, `guard.py:600`) rather than only from the
static config, which is the existing precedent for "protected-ness resolved
per call, not only from a fixed list." The implementing row extends the same
mechanism to ask "is `<branch>` currently protected on GitHub" for a branch
the epic names, instead of only consulting `cfg["protected_branches"]`. This
decision does not specify the exact call (a cached `gh api` read, a value
written into epic state at protection time) — that is an implementation
choice for the row that builds it, deferred here.

### 5. The version bump: chosen at epic-merge time, by the epic's own scale, no
new owner act

The bump is major/minor/patch. It is not typed by the owner as a new field:
it is derived the same way `origin:` is derived rather than claimed
(ADR-0029 §3) — from a fact already on the tracker. The candidate fact is the
epic's own children: a breaking-change label on any child sortie forces
major; otherwise any `feat`-typed child (commit-type prefix, already asserted
by the branch-naming convention in CLAUDE.md) forces minor; an epic whose
children are all `fix`/`chore`/`docs`/`refactor`/`test` bumps patch. This
mirrors Conventional Commits' own bump rule, applied at the epic's merge
instead of at every sortie's, which is exactly the toil #258/#262/#266 named:
the bump becomes one mechanical read of already-labelled state at one merge,
not a hand-filed row per bump. The exact label/prefix taxonomy this reads is
implementation detail for the row that builds it; what is decided here is
that it is derived, never asked, and derived once, at the release merge, not
per sortie.

### 6. The config key

`releases_by_epic: false` (bool, per-project, defaulting off) in
`.qops/config.yml`. When true, `qops brief` and the epic-interview flow offer
the `release-branch` field on `type:epic` issues; when false (today's
behaviour, every existing project), the field is never offered and every
sortie targets `master` exactly as it does now. The key name and default are
decided here; the contract entry in `docs/reference/qops-contract.md` is
written when the mode ships, per the frozen-contract rule — not in this ADR.

## What this closes and what it defers

**Closes:**
- Whether feature branches/releases enter the substrate: yes, scoped to
  `type:epic`, no fourth taxonomy noun.
- What gates a non-`master` merge: the owner's own protection write, at epic
  interview time, using ADR-0016's existing recipe — never an unattended
  write (ADR-0038 stands, no carve-out).
- How `protected_branches`' static shape meets a branch that does not exist
  yet: it doesn't change; feature-branch protection is read live, alongside
  it, by the same per-call resolution `guard.py:600` already has a precedent
  for.
- Where the version bump is decided: derived from already-labelled child-row
  state at the epic's merge, never a new owner field or a new hand-filed row.
- The config key: `releases_by_epic`, bool, default `false`.

**Defers, explicitly:**
- The concurrency question. `max_worktrees: 2` and one row per picker pass
  are unchanged by this decision. "Several epics in flight" is a tracker
  property — several epics may each carry a `release-branch` on record at
  once — not a claim that the picker builds more than one row at a time.
- The exact mechanism for reading a feature branch's live protection state
  (cached `gh api` read vs. state written at protection time) — an
  implementation choice for the row that builds §4.
- The exact label/prefix taxonomy the version-bump derivation in §5 reads —
  implementation detail for the row that builds it.
- Whether this mode gets a consumer-facing citation (`CADR-` under
  `docs/adr/consumer/`, ADR-0035) once it ships — decided when it ships, not
  here.
- Any change to `docs/reference/qops-contract.md` — frozen, batched, applied
  after this decision, never inside it.

## What would have made this wrong

A mechanism that lets an unattended pass write `master`'s or a feature
branch's protection settings, or that lets a feature-branch merge proceed
without required status checks resolved against *some* protected branch. This
decision proposes neither: every write stays the owner's, at a session they
are already in, using a recipe (ADR-0016) already proven safe for `master`.
