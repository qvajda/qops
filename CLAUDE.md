# qops — a ways-of-working substrate

One CLI, seven rendered workflows, six loops, a label taxonomy, six agent roles
and three native skills. It is the thing a project installs so that its sessions
start with the same brief, its constraints are enforced by a hook rather than by
memory, and its backlog can be worked unattended.

**This repo is the substrate itself.** The rule that shapes every change here:
nothing in `qops/`, `scripts/`, `tests/`, `.claude/` or `docs/` may name a
project. `.qops/config.yml` is the only file allowed to, and
`tests/test_qops.py::test_no_project_specific_string_outside_the_config`
enforces it. Read `docs/reference/qops-contract.md` before changing a config key
or a CLI verb — it is the frozen contract, and consumers depend on it.

This file is hot path: it enters every session unasked, and it is capped at
**150 lines** by `groom.yml` and by `tests/test_qops.py`.

## Provenance

Extracted from `qvajda/qhoto_printshop` on 2026-08-19 (Phase 8). The history was
**not** rewritten and no subtree surgery was done — the files were copied into a
fresh initial commit, and `README.md` names the source commits. Everything
before that commit lives in the source repo's history.

## Standing decisions — not planning variables

**This repo is public** (inherited from the source repo's ADR-0012, restated in
`docs/adr/0022-the-substrate-is-public.md` rather than left silent). A public
repo cannot be un-published for anyone who has cloned it. Nothing secret goes in
it, and a secret found in it is **rotated first** — rewriting history is a
separate decision and has never been the fix.

**Git history is not rewritten.** No `filter-repo`, no `filter-branch`, no BFG,
no force-push. Closed, not deferred.

**Branch protection is load-bearing, not hygiene.** `automerge-loop` only
switches on GitHub's *native* auto-merge; the **required status checks** are
what actually merge (ADR-0016, ADR-0020). Turning them off does not make merges
manual, it makes every `gate:machine` PR sit forever, which reads exactly like a
broken picker. `.claude/settings.json` denies `gh api -X` against those settings
on purpose: they are the owner's, and that denial is a taken decision, not an
obstacle to route around.

## Hard constraints — do not change without flagging first

- **`.qops/config.yml` is the only project-specific surface.** A new
  project-shaped value goes in config with a documented key, never in a module,
  a template or a test. → `docs/reference/qops-contract.md`
- **The root is found, never derived from `__file__`.** `config.find_root()`
  walks up from cwd. As a pinned dependency, `Path(__file__).parents[1]` is
  site-packages and not the repo being operated on. **An unattended run names
  its root** (`--root`) instead of walking: there are two roots on the cron
  host, and a picker that guesses reads the wrong backlog or none, exiting 0
  either way. → `docs/reference/loops.md`
- **Nothing assumes POSIX.** The cron host is a Windows desktop; `python:` is in
  config precisely so nothing else guesses at an interpreter. Asserted by
  `test_no_substrate_module_assumes_posix`. → `docs/adr/0009-...`
- **The guard reads argv, not the command string.** Parse the git subcommand
  once and decide from that parse; a new case is a row in the parametrize list,
  never a seventh regex. → `docs/adr/0021-the-guard-reads-argv.md`
- **`ready:auto` is the owner's to grant, and only the owner's — but which act
  counts as the grant depends on `origin:`.** On `origin:owner` issues the
  filing itself is the grant; it takes effect once R8 (a test proves it done,
  not just names one) holds, checked mechanically, no second label edit. On
  `origin:agent` issues nothing but the owner may ever write `ready:auto`; an
  agent may only propose it, and the owner grants by batch approval, never by
  a lone agent's confidence. → `docs/adr/0023-...`
- **`gate:` answers judgement, `no-auto` answers authority, `type:manual`
  answers reach.** A row is `gate:taste` only when the owner's preference is an
  *input* the work cannot proceed without — its deliverable *is* a choice only
  they can make. Everything else is `gate:machine`, **including when unsure**:
  an unsure row is underspecified, not tasteful. The act being the owner's to
  take (spend, publish, grant, activate) is `no-auto`, never the gate.
  → `docs/adr/0026-...`
- **An unattended sortie branches, commits, opens a PR, and stops.** It never
  merges by hand and never pushes to `master`. The merge is `automerge-loop`'s,
  on a `gate:machine` issue with every required check green.
  → `docs/reference/loops.md`, `docs/adr/0020-...`
- **A row is one sortie, and its filing is the licence.** One deliverable, one
  gate, one acceptance criterion; an oversized row is refused by the triager and
  split by the planner, never labelled (ADR-0027). And a row may not leave
  `state:triage` unless its body states an outcome a machine can turn into
  criteria — with the grant mechanical, the filing is the only owner act left in
  the chain, so it is where the check goes. A deliverable that cannot state a
  criterion is a taste row, caught by the same check. → `docs/adr/0028-...`
- **A workflow is a rendering, never a hand edit.** `.github/workflows/*` come
  from `qops/templates/*.tmpl` + config via `qops install`; `qops doctor`
  detects a hand edit. Fix the template or the config. **And it must run in a
  repo shaped unlike this one** — dependencies in `requirements*.txt` vs
  `pyproject.toml`, qops vendored vs pinned. One install block, three shapes,
  asserted by execution. → `docs/adr/0024-...`
- **The critic of a decision is a test.** An instruction in a prompt is a
  preference, not a control — if a decision says something must never happen, an
  assertion says so too, in code, next to the decision.
- **Recurring owner toil is not an implementation.** If closing a gap needs a
  human hand on a keyboard *every time* the gap recurs, the design is not
  finished — a defect this substrate can detect is a defect this substrate
  closes. An owner action is legitimate only where it is a *decision*: a taste
  judgement, a grant, an irreversible act — never a fact already derivable
  from state already on the tracker. → `docs/adr/0025-...`

## Conventions

- **Read the contract before changing a seam.** `docs/reference/qops-contract.md`
  is frozen: schema changes are collected during a consumer's first week and
  applied after it, not inside it.
- **A swallowed per-item exception must leave a state change behind.** A
  `try/except: continue` in a loop must write a status plus a reason, and still
  fail the run once, after the loop.
- **Verify by measurement, not by status code.** A green step that produced no
  observable change did not happen.
- **Commit type + issue number in the branch name**: `<type>/<issue#>-<slug>`,
  where type is `feat|fix|docs|chore|refactor|test`. `automerge-loop` reads the
  issue number off the branch, not off a `Closes #n` line — an instruction in a
  prompt is a preference.
- Reference code as `file:line`.

## Ways of working

Issues are the source of truth: `gh issue list` on **`qvajda/qops`** — `qops
brief` names the tracker it read, every time, because a consuming project has a
second one. The issue wins over any planning document.

Session state, the guard and the metrics are qops itself:
`python -m qops brief|ledger|resume|guard|close|install|doctor|metrics|reconcile`,
configured entirely by `.qops/config.yml`. Agent docs: `docs/agents/`.
Decisions: `docs/adr/`. The loops: `docs/reference/loops.md`.
