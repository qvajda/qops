# The qops contract — config schema and CLI

**Frozen 2026-08-19 (Phase 8, P8.1).** This is what a consuming project may
rely on. `qhoto_printshop` is consumer #2 and its first week is the design
review: no schema change inside that window — complaints get collected, not
patched. The window opens when #105 lands, not when this line was written; the
name it replaced was a project that never onboarded, so nothing had started the
clock.

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
| `doctor_checks` | list[str] | `module:callable` entries, each imported from the consumer's root and called as `fn(root, cfg) -> list[str]`, merged into `doctor`'s problems. Omit the key and nothing is imported. An unimportable entry, or one returning a non-`list[str]`, is itself a `doctor` problem. |

### pickup-loop

| Key | Type | Meaning |
|---|---|---|
| `pickup_task` | bool | Default `true`. Whether `qops install` registers the scheduled task at all. False and no install touches the host's scheduler; `--unregister-task` still removes one, and `doctor` reports a task registered under this project's name while the config says there should be none. |
| `pickup_launch` | bool | Default `false`. Whether the registered scheduled task passes `--launch`. Off, the schedule fires, prints what it would have picked and spends nothing — the only way to prove the wiring without starting an agent (ADR-0032). |
| `pickup_max_silence_hours` | int | How long `qops brief` stays quiet before it reports that the loop has not completed a run. Read as state: the picker writes a heartbeat when a run finishes, and the absence of a recent one is the report. |

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
| `skills.native_skip` | list[str] | Opt-out: a name in `init.SKILLS` this consumer decided on purpose not to carry. May be `[]` or absent. |
| `skills.accept_drift` | list[str] | Opt-out for `skill_body_drift` (#200): names whose `SKILL.md` this consumer has deliberately customized. A list, not a map, because the declared set is one. May be `[]` or absent. |

`skill_drift` fails on an installed-and-undeclared skill, a declared-and-missing
native, a lock entry outside the declared set, a lock entry with no `ref`, and
(#179) a name present in qops's own `init.SKILLS` scaffold list that is
neither in `skills.native` nor in `skills.native_skip`.

`skill_body_drift` is the other half (#200): `skill_drift` compares the *set*
of names, this compares the **bodies** of the natives qops ships a template
for, against `qops/templates/skills/<name>/SKILL.md`. It reports; it never
overwrites — a skill body a consumer edited is theirs, and `skills.accept_drift`
is how they say so. An external skill and a consumer's own native skill are
never compared: neither has a template to drift from.

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
`agents.<role>.allow/deny` — optional per-role command classes (ADR-0033). A
role stating neither behaves exactly as it does today; there is no reader yet.
`agents.<role>.accept_drift` — `true` opts a role's `.claude/agents/<role>.md`
out of `agent_drift`'s check (#183): a project may have legitimately
customized a role, and this is how it says so instead of `doctor` failing on
it forever.

### Permissions

| Key | Type | Meaning |
|---|---|---|
| `permissions.extra.allow` | list[str] | Project-shaped rules merged onto the substrate standard allow set when `.claude/settings.json` is rendered (#158). |
| `permissions.extra.deny` | list[str] | Same, for the deny set. Additive in both halves, subtractive last (`qops/install.py:104-122`). |

### Taxonomy

`labels.type`, `labels.state`, `labels.mission`, `labels.gate`, `labels.origin`,
`labels.priority` are namespaces —
the rendered label is `<ns>:<value>`. `labels.flags` are verbatim strings.
`milestones` is a flat list. **Every label named anywhere else in this file must
appear here**, asserted by `test_every_label_the_config_names_is_in_its_own_taxonomy`.

`labels.priority` is optional and zero-or-one, deliberately **not** in
`validate.require_on_open`: an unlabelled row is normal priority, so no
existing row needs migrating. One value today, `parked` (ADR-0034) — the
owner's to grant, never the triager's. `parked` vetoes pickup, planning and
decomposition (`install.BLOCKING_FLAGS`) and is excluded from `qops pending`'s
"waiting on you" section: a parked row is not waiting on the owner, it is
parked by him.

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
| `init` | `--project`, `--repo`, `--python`, `--default-branch` (prompted if a flag is missing and stdin is a tty) | Scaffolds a blank repo: writes `.qops/config.yml`, `CLAUDE.md`, `.claude/settings.json`, the four native skills, `skills-lock.json`, then renders the workflows. Refuses if `.qops/config.yml` already exists. Prints the preconditions it cannot satisfy itself. | 0; 2 if `.qops/config.yml` exists or a required value is missing |
| `install` | `--unregister-task` | Renders all seven workflows from the templates + config, then registers `pickup-loop`'s scheduled task (unless `pickup_task: false`) — named `\qops\<project>\pickup-loop`, commanded from `python:` and the root, **disabled** (ADR-0032). Registering never enables, and never disables a task the owner enabled. `--unregister-task` removes it and renders nothing. A host with no scheduler renders the workflows and says so. | 0 |
| `doctor` | — | Workflow drift, broken doc citations, skill drift, hook installation, the hot-path cap, schema drift, consumer-declared `doctor_checks`, the pickup task's drift against what the config renders (its enabled/disabled state is *reported*, never a problem and never changed), the three owner/machine preconditions (branch protection, auto-merge settings, workspace trust — best-effort, and anything short of a confirmed yes counts as a problem, but read **only off a PR**: a runner cannot answer them and no PR can fix them), and the open-issue invariants. | 0/1 |
| `metrics` | `--state`, `--json`, `--since D`, `--until D` | S1–S13; `--state` writes the state-report table. | 0/1 |
| `reconcile` | `--limit N` | Advances the row of every merged sortie whose issue is not `state:done`. | 0/1; 2 if no `repo:` |
| `migrate` | `--dry-run` \| `--execute` \| `--verify` | `--dry-run` reads open rows and writes `.qops/migrate-plan.json`, touching no issue. `--execute` applies that plan whole or not at all, refusing if the corpus moved since. `--verify` re-reads the tracker and asserts every planned row landed (ADR-0030). | 0/1; 2 if no `repo:` or no flag given |
| `review` | —; reads `PR_NUMBER` + `PR_HEAD_SHA` | The CI half of the reviewer gate: reads the host's verdict comment for **this** head SHA. Calls no model, needs no secret. Every non-verdict outcome is green and says why. | 0; **1 only on `does-not-serve`** |
| `pending` | — | Read-only: what is waiting on the owner (`gate:taste`, `state:review`, `no-auto`, struck-out rows, a `gate:machine` row whose merged PR left it open, `doctor` problems) and what the loop takes next (the build/plan/decompose queues, in pickup order). Writes no label, no comment, no ledger row. One `gh issue list` call. | 0/1; 1 if the tracker could not be read or no `repo:` |

Two scripts sit outside the CLI, both rooted the same way:

- `scripts/qops_import.py --labels | --validate | --execute` — `--labels`
  creates every label the taxonomy declares and needs no issue corpus, so it is
  the first thing a fresh repo runs. It creates what is missing and never
  `--force`s over an existing label's colour or description. **A repo with no
  labels makes the picker's query return empty and exit 0**, which is
  indistinguishable from an idle queue. Milestones are not created: `gh` has no
  non-`api` verb for them and `gh api -X` is denied by a taken decision, so an
  import naming an absent milestone fails loudly instead.
- `scripts/qops_pickup.py [--root PATH] [--launch] [--review]` — the
  `pickup-loop` picker. Without `--launch` it prints what it would pick and
  starts nothing. A `--launch` run also produces the reviewer's verdict for
  every ready PR and posts it as a PR comment; `--review` runs that pass alone.
  It is here and not in a second scheduled task because one loop is one
  registration (#12), and here and not in CI because the model call needs this
  host's Claude subscription (#80). The registered task passes `--launch` only
  when `pickup_launch:` says so.

## What a fresh repo needs before `doctor` can be clean

`doctor` has unconditional preconditions, and a new repo satisfies none of them:

1. a `CLAUDE.md` (read at `install.py`, for the line cap);
2. a `.claude/settings.json` that invokes qops (otherwise the hooks are not
   installed);
3. a `skills:` block matching what is in `.claude/skills/`, with a
   `skills-lock.json` ref per external;
4. the seven workflows rendered — `qops install`;
5. the label taxonomy created — `python scripts/qops_import.py --labels`.

`qops init` writes 1-4 in an empty folder and renders the workflows; it cannot
do 5, because the labels live on a GitHub repo that has to exist first.

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
