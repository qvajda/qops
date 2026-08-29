# qops

A per-project ways-of-working substrate. One CLI, seven rendered workflows, six
loops, a label taxonomy, six agent roles and four skills.

A project installs qops so that its sessions start with the same brief, its
constraints are enforced by a hook rather than by memory, its metrics are
measured rather than felt, and its backlog can be worked unattended by an agent
that branches, commits, opens a PR — and stops.

```
python -m qops brief      what a session gets at SessionStart, <=400 tokens
python -m qops ledger     append a session event (hook payload on stdin)
python -m qops resume     print or regenerate .qops/resume.md
python -m qops guard      PreToolUse hook; `guard scan` is the CI half
python -m qops close      close a sortie: label state:done and close the issue
python -m qops init       scaffold a blank repo to a clean doctor
python -m qops install    render .github/workflows from templates + config
python -m qops doctor     drift, broken doc links, an uninstalled hook
python -m qops review     does a PR's diff serve its row's stated outcome
python -m qops metrics    S1-S13; --state regenerates the state report
python -m qops reconcile  advance the row of every merged sortie
python -m qops migrate    propose a taxonomy migration, apply nothing unasked
python -m qops pending    what waits on the owner, and what the loop takes next
python -m qops version    print the installed qops version
```

## The one property that matters

**`.qops/config.yml` is the only file that may name a project.** The package,
the templates, the agent definitions, the skills and the tests are substrate:
they carry no project name, no path, no interpreter and no threshold of their
own. That is not a style preference — it is asserted, in
`tests/test_qops.py::test_no_project_specific_string_outside_the_config`,
against a word list the consuming project's own config declares.

The second: **the repo root is found, never derived.** `config.find_root()`
walks up from the working directory to the nearest `.qops/config.yml`. Installed
as a pinned dependency, `Path(__file__).parents[1]` is site-packages, not the
repo being operated on.

## Install

```bash
pip install "qops @ git+https://github.com/qvajda/qops@v0.4.0"
```

Pin a tag, not a branch. A substrate that mutates under a live project is a
failure mode with a name in the source repo's history.

Then, in the consuming repo:

```bash
python -m qops install          # renders .github/workflows/
python -m qops doctor           # tells you what is still missing
```

`doctor` has preconditions a fresh repo does not meet — a `CLAUDE.md`, a
`.claude/settings.json` that invokes qops, a declared skill set, the label
taxonomy. `docs/reference/qops-contract.md` lists them, along with every config
key and the exit code of every verb. **Read the contract before depending on a
seam**: it is frozen, and schema changes are collected during a new consumer's
first week rather than applied inside it.

## Documentation

| | |
|---|---|
| `docs/reference/qops-contract.md` | The config schema and the CLI contract. Frozen. |
| `docs/reference/loops.md` | The six loops: what each may do, and what it may not. |
| `docs/adr/` | Why each thing is the way it is. Numbering is inherited from the source repo, so the gaps are that repo's pipeline decisions. |
| `docs/agents/` | The six agent roles and the triage rules R1–R8. |
| `CLAUDE.md` | The hot path — what a session working on qops itself needs. |

## Provenance

Extracted from **`qvajda/qhoto_printshop`** on 2026-08-19, in Phase 8 of that
repo's ways-of-working work. qops was built there, for one project, over six
phases; this repo is the seventh, which is the one where it stopped being a
subdirectory of the project it happened to serve.

**The history was not rewritten and no subtree surgery was done.** The files
were copied into a fresh initial commit. Everything before that commit — every
decision, every failure that produced a rule, the three unattended-sortie
attempts that produced `docs/reference/loops.md` — lives in the source repo's
history and is unaffected by anything here.

Source commits, at the point of extraction:

`qvajda/qhoto_printshop` at **`91a344908`** — the tip of `master` on
2026-08-19, immediately after Phase 8's P8.1 landed. The three commits that made
the substrate portable, and which are worth reading before changing a seam here:

| commit | what it settled |
|---|---|
| [`70826ef`](https://github.com/qvajda/qhoto_printshop/commit/70826ef) | P8.1 — froze the config schema and the CLI contract, closed five project-specific leaks, and replaced four one-day measurements with four assertions |
| [`62fc53f`](https://github.com/qvajda/qhoto_printshop/commit/62fc53f) | the guard stopped reading the command string and started parsing the git subcommand (ADR-0021) |
| [`91a3449`](https://github.com/qvajda/qhoto_printshop/commit/91a3449) | `qops_import.py --labels` — nothing had ever created the label taxonomy, which is the one prerequisite whose absence is silent |

The ADRs numbered below 0013 that appear in `docs/adr/` are the source repo's,
kept at their original numbers so that citations in the code keep resolving. The
gaps are that repo's pipeline decisions, and they did not travel.

## Versioning

`pyproject.toml`'s `version` is what a consumer pins against, via a tag. `v0.1.1`
was tagged against a tree that still declared `version = "0.1.0"` — a silent
mislabel, caught only when a consumer's `pip show qops` reported a version the
tag didn't match. `tests/test_qops.py` now asserts the declared version was
bumped before a tag can be cut against it.

## Licence

MIT. See `LICENSE`, and `docs/adr/0022-the-substrate-is-public.md` for why this
repo is public and why the licence is that one.
