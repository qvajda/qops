# The qops contract — config schema and CLI

**Frozen 2026-08-19 (Phase 8, P8.1).** This is what a consuming project may
rely on. `myThirdwheel` is consumer #2 and its first week is the design review:
no schema change inside that window — complaints get collected, not patched.

Two rules hold the whole thing up:

1. **`.qops/config.yml` is the only file that may name a project.** Everything
   else — the package, the templates, the agents, the skills, the tests — is
   substrate. `tests/test_qops.py::test_no_project_specific_string_outside_the_config`
   asserts it against the word list the config itself declares.
2. **The root is found, never derived from `__file__`.** `config.find_root()`
   walks up from cwd to the nearest `.qops/config.yml`. As a pinned dependency,
   `Path(__file__).parents[1]` is site-packages.

## Config schema

Required keys are marked ●. Everything else has a default and may be omitted.

### Identity

| Key | Type | Meaning |
|---|---|---|
| ● `project` | str | The project's name. Read by the templates and by the portability assertion. |
| `prd` | path | The project's own planning doc, if it has one. Informational. |
| ● `repo` | `owner/name` | The tracker. `qops brief` prints it, `doctor`, `metrics` and `reconcile` query it. |
| `default_branch` | str | Default `master`. |

### Interpreter

| Key | Type | Meaning |
|---|---|---|
| ● `python` | str | What the hooks and shims invoke. `py -3` on a Windows host. Nothing else in the package may guess at an interpreter — asserted by `test_no_substrate_module_assumes_posix`. |
| `python_posix` | str | The same thing for a POSIX runner (CI). |

### Guard

| Key | Type | Meaning |
|---|---|---|
| `protected_branches` | list[str] | A push to one of these is refused locally; branch protection is the half that cannot be routed around (ADR-0016, ADR-0021). |
| `max_worktrees` | int | `git worktree add` is refused at the cap. |
| `tripwires` | list | **May be empty, and a substrate repo's is.** Each entry: `name`, `pattern` (regex), optional `paths` (list of prefixes the tripwire applies to), `why` (ASCII — it is printed to a Windows console). |
| `scan_exclude` | list[path] | Prefixes `qops guard scan` skips: the files that name the tripwires on purpose. The PreToolUse hook honours it for writes and never for Bash. |

### Portability and schema

| Key | Type | Meaning |
|---|---|---|
| `portability_forbidden` | list[str] | Words no substrate file may contain. The project name and every tripwire name are added automatically. |
| `schema_check.sql` | path | A declared SQL schema to compare the live DB against. Omit the block and no check runs. |
| `schema_check.db` | path | The live DB. Absent on a fresh checkout, which is not drift. |

### Docs and hot path

| Key | Type | Meaning |
|---|---|---|
| ● `claude_md_max_lines` | int | The hot-path cap. Enforced by `groom.yml` **and** by the test suite. |
| `doc_link_roots` | list[dir] | Trees whose `.py` files are scanned for `docs/*.md` citations; every citation must resolve. Empty means no check. |

### Skills (ADR-0018)

| Key | Type | Meaning |
|---|---|---|
| `skills.native` | list[str] | Ours, tracked in git. Declared-and-absent is a `doctor` problem. |
| `skills.external` | list[str] | Reinstallable copies. Each needs a `ref` in `skills-lock.json`. May be `[]`. |

`skill_drift` fails on an installed-and-undeclared skill, a declared-and-missing
native, a lock entry outside the declared set, and a lock entry with no `ref`.

### CI

| Key | Default | Meaning |
|---|---|---|
| `ci.python_version` | `3.12` | |
| `ci.test_command` | `python -m pytest -q` | `test.yml`. |
| `ci.gate_command` | `python -m pytest -q` | `gate.yml` — the machine gate. |
| `ci.runs_on` | `ubuntu-latest` | |
| `ci.digest_cron` | `0 6 * * *` | Also when `reconcile` runs. |
| `ci.digest_posts_on_schedule` | `true` | `false` renders the digest **on demand only** (`gh workflow run digest.yml`) while `reconcile` keeps the cron. For a second repo on one owner's cadence, this is what stops the daily digest doubling; the reconciler is the half that must not be turned off, since `advance` cannot fire on a merge its own `GITHUB_TOKEN` caused. |
| `ci.groom_cron` | `0 5 * * 1` | |
| `ci.status_issue_label` | `qops:status` | The pinned digest issue's label. **Must appear in `labels.flags`**, and issues carrying it are exempt from the open-issue invariants — machine bookkeeping is not a sortie (#136, #167). |

### Agents

`agents.<role>` → `{model, effort, tools}` for each of the six roles
(`planner`, `coder`, `reviewer`, `scribe`, `triager`, `interactor`).
`delegation_cap` caps concurrent subagents.

### Taxonomy

`labels.type`, `labels.state`, `labels.mission`, `labels.gate`, `labels.origin`
are namespaces —
the rendered label is `<ns>:<value>`. `labels.flags` are verbatim strings.
`milestones` is a flat list. **Every label named anywhere else in this file must
appear here**, asserted by `test_every_label_the_config_names_is_in_its_own_taxonomy`.

`validate.require_on_open` — namespaces every open issue needs exactly one of.
`origin` is one of them (ADR-0023, amended by ADR-0029): it names whose licence
covers the row, not who typed it, and is enforced at filing by the local guard.
Three values: `owner`, `agent`, and `pending` — filed when a session cannot
honestly claim `owner` and a parent is intended. `pending` is derived to the
parent's `origin:` by `qops reconcile`, from a native sub-issue link to an
`origin:owner`/`origin:agent` parent — a tracker fact, never inferred from the
body, the author, or a claim in the filing.
`validate.forbid_at_import` — labels the importer refuses (`ready:auto`: auto
eligibility is a control, and granting it at import bypasses the control).

## CLI contract

`python -m qops <verb> [args]`. Every verb resolves the root itself. Exit 0
means clean, 1 means something is wrong and was reported, 2 means the call was
malformed or refused.

| Verb | Args | Does | Exit |
|---|---|---|---|
| `brief` | — | The SessionStart brief, ≤400 tokens. Names the tracker it read. | 0 |
| `ledger` | hook payload on stdin; no args prints the tail | Appends one JSON object per line. | 0 |
| `resume` | `--write`, `--quiet` | Prints `.qops/resume.md`; `--write` regenerates. | 0 |
| `guard` | none = PreToolUse hook (payload on stdin); `scan` = the CI half | Hook: **2 blocks the call**. Scan: greps the tracked tree. | hook 0/2; scan 0/1 |
| `close` | `<issue>…` `[--comment TEXT]` | Labels `state:done`, closes, writes the ledger. | 0/1; 2 if no issue given |
| `install` | — | Renders all six workflows from the templates + config. | 0 |
| `doctor` | — | Workflow drift, broken doc citations, skill drift, hook installation, the hot-path cap, schema drift, and the open-issue invariants. | 0/1 |
| `metrics` | `--state`, `--json`, `--since D`, `--until D` | S1–S13; `--state` writes the state-report table. | 0/1 |
| `reconcile` | `--limit N` | Advances the row of every merged sortie whose issue is not `state:done`. | 0/1; 2 if no `repo:` |

Two scripts sit outside the CLI, both rooted the same way:

- `scripts/qops_import.py --labels | --validate | --execute` — `--labels`
  creates every label the taxonomy declares and needs no issue corpus, so it is
  the first thing a fresh repo runs. It creates what is missing and never
  `--force`s over an existing label's colour or description. **A repo with no
  labels makes the picker's query return empty and exit 0**, which is
  indistinguishable from an idle queue. Milestones are not created: `gh` has no
  non-`api` verb for them and `gh api -X` is denied by a taken decision, so an
  import naming an absent milestone fails loudly instead.
- `scripts/qops_pickup.py [--root PATH] [--launch]` — the `pickup-loop` picker.
  Without `--launch` it prints what it would pick and starts nothing.

## What a fresh repo needs before `doctor` can be clean

`doctor` has unconditional preconditions, and a new repo satisfies none of them:

1. a `CLAUDE.md` (read at `install.py`, for the line cap);
2. a `.claude/settings.json` that invokes qops (otherwise the hooks are not
   installed);
3. a `skills:` block matching what is in `.claude/skills/`, with a
   `skills-lock.json` ref per external;
4. the six workflows rendered — `qops install`;
5. the label taxonomy created — `python scripts/qops_import.py --labels`.

And two settings that are the repo owner's, not the package's, without which
`automerge-loop` cannot do its job:

6. **branch protection on the default branch, with the gate as a required
   check** (ADR-0016). This is not hygiene. `automerge-loop` queues GitHub's
   native auto-merge; the *required checks* are what merge. With none
   configured, a PR is mergeable the instant it opens — the substrate's own
   second PR was merged ten seconds before its gate finished (qops#3). The job
   now refuses and goes red instead, but a red job is a message, not a gate.
7. **"Allow auto-merge" on**, and **"Automatically delete head branches" on** —
   the second replaced a `--delete-branch` flag that the same fix removed.

And one that is neither the package's nor the repo's, but the machine's:

8. **The workspace must be trusted once** (`hasTrustDialogAccepted` in
   `~/.claude.json`, set by running Claude Code interactively in the root a
   single time). Until it is, **every `permissions.allow` and `permissions.deny`
   entry in `.claude/settings.json` is ignored** — including the `gh api -X`
   denials that ADR-0016 and ADR-0020 rest on. It degrades quietly: a launched
   sortie still works under `--permission-mode acceptEdits` and merely prints a
   warning, so the controls are absent while the file that declares them, and
   the test that asserts it, both look correct (#19).
