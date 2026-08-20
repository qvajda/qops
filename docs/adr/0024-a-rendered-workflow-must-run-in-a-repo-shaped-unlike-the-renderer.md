---
status: accepted
revisit-after: 2026-12-01
---

# A rendered workflow must run in a repo shaped unlike the one that rendered it

**Date:** 2026-08-20 · **Closes the class named in** #21 · **Extends** the
"a workflow is a rendering, never a hand edit" constraint in `CLAUDE.md`.

## Context

Three defects, one class, found in one week by one thing — a second consumer:

- **#1.** `test.yml` and `gate.yml` installed `requirements.txt` /
  `requirements-dev.txt` if present and nothing otherwise. This repo declares
  its dependencies in `pyproject.toml`, so the first push installed nothing and
  `test` went red with `No module named pytest`.
- **#21.** `guard.yml` installed `pip install pyyaml` and nothing else, while
  both its jobs `import qops`. That held for exactly as long as the only repo
  rendering it kept `qops/` as a subdirectory. The moment `qhoto_printshop`
  pinned the package instead, both jobs failed — and `tripwires` and
  `doc-links` are **required status checks** (ADR-0016), so it did not degrade,
  it blocked every PR in the consuming repo.
- **Still open at the time of writing, and found by taking this decision rather
  than by a fourth consumer:** `digest.yml`'s `reconcile` job installed
  `requirements.txt` if present and nothing otherwise, then ran
  `python -m qops reconcile`. Same shape as #21, one workflow over. The
  reconciler is the half that must not be turned off (`advance` cannot fire on
  a merge its own `GITHUB_TOKEN` caused), so this one fails the backstop.

#21 said the class wants a decision rather than a fourth point fix, and named
the missing property: **nothing asserts that a rendered workflow can actually
run in a repo shaped unlike the one that rendered it.**

Each fix was correct and none of them was the fix. The common cause is not any
one branch: it is that **four copies of the dependency-install step existed, in
three different versions, each calibrated against whichever consumer's shape
was in front of whoever last touched it.** A per-template shell block is
substrate that nothing renders, nothing shares and no test reads.

## Decision

**One install block, in the substrate, covering every declared repo shape, with
the shapes asserted by execution.**

1. `qops/install.py:INSTALL_DEPS` is the single dependency-install block. It is
   rendered into every job that runs Python via `{{install_deps}}`, the same
   way every other machine fact reaches a workflow. A template may not write
   its own.
2. The block covers three shapes, and the three are the decision:

   | Shape | What it means | What the block does |
   |---|---|---|
   | `requirements.txt` present | a consumer pins qops there | `pip install -r requirements.txt` |
   | else `pyproject.toml` present | the repo **is** the package | `pip install -e .` |
   | neither | `qops/` is a subdirectory | `pip install pyyaml` |

   `requirements-dev.txt` is installed on top when present, in every job.
3. **The shapes are executed, not pattern-matched.**
   `tests/test_qops.py::test_the_install_block_reaches_qops_in_every_repo_shape`
   builds a tree of each shape, runs the block with `python` and `pip` replaced
   by argv-logging shell functions, and asserts which branch fired. #1 was a
   branch that read correctly and never ran; a test that only greps for
   `pyproject.toml` would have passed against it.
4. `test_every_job_that_runs_python_uses_the_one_install_block` asserts there is
   no second way to install anything in a rendered workflow — a divergent copy
   is how all three defects arrived.

Point 2 also takes the decision #1 explicitly deferred: *"whether the template
should detect `pyproject.toml` and `pip install -e .` directly. That is a change
to rendered output in every consuming repo and needs its own decision."* This is
that decision. It is taken as an `elif`, so a repo that already has a
`requirements.txt` renders the same install it renders today and nothing changes
under it.

## What this does not cover

**It is not a claim that a rendered workflow runs.** It asserts one property of
one step — the step that broke three times. A rendered workflow can still fail
in a consuming repo for a reason this says nothing about, and the honest
statement of the guarantee is: *the dependency-install step reaches an install
that provides qops in each of three declared repo shapes.*

**It does not cover #19.** An untrusted workspace silently drops every
`permissions.allow` and `permissions.deny` entry from `.claude/settings.json`.
That is per-machine state in `~/.claude.json`, outside both repos and outside
every rendering — no template change reaches it, and it is `gate:taste` for
exactly that reason. **Corrected by ADR-0026:** that is *verification reach*,
not judgement, and the two had only one label between them when this was
written. #19 is `gate:machine` — its detectable half is a `doctor` warning —
with a `type:manual` remedy. Same *family* as #1 and #21 (an assumption calibrated
against one machine, degrading quietly), different *mechanism*, and it stays
open with #7 and #12.

**A fourth shape is a row, not a redesign.** `REPO_SHAPES` in the test and the
`elif` chain in `INSTALL_DEPS` are the two places, and a new shape that arrives
without a row is the thing the execution test is there to notice.

## Cost accepted

Every Python job now installs `requirements-dev.txt` when it exists, so
`guard` and `digest` pull a consumer's test dependencies they do not use.
`cache: pip` mitigates it. Splitting the block back into a runtime half and a
dev half would restore the divergence this ADR exists to remove, and a guard job
that cannot import the guard is worse than a slow one (#21).

## No config schema change

The contract in `docs/reference/qops-contract.md` is frozen during consumer #2's
first week. Nothing here needs a key: **which files a repo declares its
dependencies in is the repo's shape, not the project's preference.** A config
key would ask a project to declare something the block can simply detect, and
would have been wrong in all three defects — every one of them was a repo whose
shape the person writing the config had not thought about.
