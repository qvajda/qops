---
status: accepted
revisit-after: 2027-02-01
---

# What each role may run is one declaration, rendered into three enforcement points

The roster names six roles and grants each a tool list. `tools: [Bash]` on the
coder permits `git status` and `git push --force` identically, so the
distinction the roster exists to draw — a reviewer that never edits, a scribe
that never pushes, an interactor that carries no authority — is drawn nowhere
that runs. #13 asked what each role may actually run, and where that is written
down once.

## What was already true when this was decided

Two of #13's findings had closed themselves before it was worked, and are
recorded here so they are not re-derived:

- **`agents:` in `.qops/config.yml` is no longer dead.** `plan_argv` reads
  `agents.planner.tools` and `.model` (`scripts/qops_pickup.py:856`), the
  unattended launch borrows `agents.coder.tools`, and four tests bind the
  frontmatter to the config: the roster is the config's
  (`tests/test_qops.py:1051`), each role's `model`/`effort`/`tools` match
  (`:1058`), read-only roles hold no write tool (`:1078`), and the launch grant
  is the coder's toolset and no wider (`:1101`).
- **The narrow launch grant shipped.** `launch_argv` carries
  `--permission-mode acceptEdits` with a scoped `--allowedTools`, the blanket
  bypass flags are asserted absent, and `launch_env()` marks the run
  `QOPS_UNATTENDED=1`, which `guard.check` reads to refuse a sandbox escape an
  attended owner could still take (`qops/guard.py:362`).

Two findings survived, and this ADR answers them:

- **Tools are the wrong unit.** Nothing anywhere states a *command* rule per
  role.
- **`.claude/settings.json` is a scaffold, not a rendering.** `qops init`
  writes it once from `settings.json.tmpl` (`qops/init.py:100`) and nothing
  re-renders it: `install.render_all` and `install.drift` cover
  `.github/workflows/*` only, and `doctor` asserts merely that the file exists
  and contains the string `qops` (`qops/install.py:1269`). The two copies have
  already diverged — this repo's carries the `py -3` allows and the sign-off
  comment, the template carries neither. A consumer needing a project-shaped
  allow (`Bash(psql:*)`, `Bash(sqlite3:*)`) has no declared place to put it, so
  it hand-edits the scaffold and never receives a substrate update to the
  standard set again.

## The enforcement points

Every cell below names one. A cell with no enforcement point is a matrix
nothing loads, which is the defect #13 exists to name.

| | Point | Scope | Exists |
|---|---|---|---|
| **P1** | `.claude/settings.json` `permissions.allow/deny` | the whole session, every subagent in it | yes, hand-maintained |
| **P2** | `--allowedTools` / `--disallowedTools` on the launch | one role, only where the role is its own process | yes, for planner and the launch |
| **P3** | `qops guard` (PreToolUse, argv-parsed, exit 2) | every tool call, ctx-aware | yes; `unattended` today, `role` proposed |
| **P4** | an assertion in `tests/test_qops.py` | the declaration itself | yes, for the roster |

P1 is session-wide and therefore *not* role-scoped: an in-session subagent
inherits it. Only P2 is role-scoped, and only two roles are launched as their
own process today. Everything else rests on P3 and on the tool list. That is
the residual, stated again under Consequences.

## The table

Verdicts: **allow** — never prompts. **deny** — refused, and the refusal is
the point. **prompt** — the owner may take it at a keyboard; an unattended run
may not.

### 1. Read and inspect — `git status|diff|log|show|branch|rev-parse`, `gh * view|list`, file reads

| Role | Verdict | Reason | Point |
|---|---|---|---|
| planner | allow | sizing a row means reading the tree it will not edit | P1 |
| coder | allow | red-green starts by reading the failing thing | P1 |
| reviewer | allow | reading a diff is the whole of its job | P1 |
| scribe | allow | it records what happened and cannot record what it did not read | P1 |
| triager | allow | a label comes off the body and the state, both reads | P1 |
| interactor | allow | it renders state that already exists; reads are all it has | P1 |
| launch | allow | inherits the coder's answer, deliberately, until this row changed it | P2 |

### 2. Tests and qops read verbs — `pytest`, `qops brief|ledger|resume|doctor|metrics`

| Role | Verdict | Reason | Point |
|---|---|---|---|
| planner | allow | R8 asks whether a test proves the row done; it has to look | P1 |
| coder | allow | the red and the green are both this command | P1 |
| reviewer | allow | a verdict is by measurement, not by a green status code | P1 |
| scribe | qops verbs allow, `pytest` deny | the suite's result is a fact CI already publishes; re-running it spends a session on nothing new | P2 |
| triager | qops verbs allow, `pytest` deny | no label in the taxonomy is derived from a test result | P2 |
| interactor | qops verbs allow, `pytest` deny | it renders produced state; producing it is another role's act | P2 |
| launch | touched tests allow, full suite deny | the suite runs ~3.5 min, longer than one Bash call may run, and `test.yml` is the gate | P2 + the launch prompt |

### 3. Tree writes — `Write`, `Edit`, `MultiEdit`, shell redirection into the tree

| Role | Verdict | Reason | Point |
|---|---|---|---|
| planner | deny | a planner that edits the tree is a coder; the plan goes onto the row through `gh` | P2 + P4 (`:1078`) |
| coder | allow | the sortie's deliverable is exactly this | P2 |
| reviewer | deny | it reports findings; an editing reviewer reviews its own edit | P2 + P4 |
| scribe | allow under `docs/` and the ledger; deny elsewhere | prose and state, not code — an unscoped scribe is a second coder with no test discipline | P3 (path scope; **not built today**) |
| triager | deny | mechanical labelling touches no file | P2 + P4 |
| interactor | deny | it carries no authority of its own, and a write is authority | P2 + P4 |
| launch | allow | it is the coder, unattended | P2 |

### 4. Local git writes — `git add|commit`, `git checkout -b`, `git worktree add`

| Role | Verdict | Reason | Point |
|---|---|---|---|
| planner | deny | the row is the artefact; a branch from the planner is a sortie nobody licensed | P2 (no git write class granted) + P3 |
| coder | allow on a feature branch | the whole loop is branch, commit, PR, stop | P3 (ADR-0019 refuses an edit on the protected branch) |
| reviewer | deny | it never commits, by its own definition | P2 |
| scribe | `add`/`commit` allow on an existing feature branch; `checkout -b` deny | it records onto the sortie in flight; opening a second branch splits one row across two | P3 |
| triager | deny | a label edit is not a commit | P2 |
| interactor | deny | same as its tree writes: no authority | P2 |
| launch | allow | branch first as `<type>/<issue#>-<slug>`, then commit | P3 |

### 5. Remote git writes — `git push` to a feature branch, `gh pr create`

| Role | Verdict | Reason | Point |
|---|---|---|---|
| planner | deny | nothing it produces lives in a branch | P3 |
| coder | allow to a feature branch only | the PR is where the work becomes reviewable; the branch is never the protected one | P3 (`push_targets`) |
| reviewer | deny | it reviews the diff in front of it and publishes nothing | P2 |
| scribe | deny | #13's own example of the defect: a scribe that may push is a scribe that ships | P3 |
| triager | deny | labels reach the tracker through `gh`, never through a ref | P2 |
| interactor | deny | rendering outward is a comment, never a ref | P2 |
| launch | allow, then stop | it opens the PR and never merges it | P3 + the launch prompt |

### 6. Tracker writes — `gh issue edit|comment|create`, `gh label`

| Role | Verdict | Reason | Point |
|---|---|---|---|
| planner | allow, append-only, and never `ready:auto`, `no-auto`, `gate:` or `type:` | the plan appends under a marker; the grant, the gate and the type are the owner's and are already decided | P3 (`origin_refusal`) + the plan prompt |
| coder | comment allow, label edit deny | it reports what it built; advancing the row's state is the loop's act, off facts, not off the builder's opinion | P3 |
| reviewer | deny | its verdict goes to the session that asked; `reviewer.yml` is what posts to a PR, and that is a workflow, not this role | P2 |
| scribe | allow | issue updates and constraint records are precisely its job | P1 |
| triager | label edits allow; body edits deny; `ready:auto` never | mechanical, not editorial — only the owner grants (ADR-0023) | P3 + the role file |
| interactor | deny | a digest that edits a row has taken an act while claiming to report one | P2 |
| launch | comment and state label allow; `ready:auto`, `no-auto`, `gate:`, `type:` deny | it advances what it can prove and grants nothing | P3 |

### 7. Owner acts — `gh pr merge`, `gh issue close`, `gh api -X`, push to the protected branch, force-push, history rewrite, anything that spends

**deny for all seven roles**, and the reason is not the same reason:

| Role | Reason | Point |
|---|---|---|
| planner | it licenses nothing; the filing is the licence (ADR-0028) | P1 (`gh api` write flags denied) + P3 |
| coder | the merge is `automerge-loop`'s, on green required checks (ADR-0020) | P1 + P3 |
| reviewer | a review that can merge is not a review | P1 |
| scribe | recording a close is not taking one; a `gate:machine` close is not a judgement either, and is still the owner's act (ADR-0025) | P1 |
| triager | branch protection and the label set are repo settings, and those are the owner's last word (ADR-0016/0020) | P1 |
| interactor | it asks the owner's questions; it does not answer them | P1 |
| launch | nobody is reading, which is exactly when an irreversible act must not be taken; the sandbox escape is already refused here specifically | P3 (`guard.py:362`) |

## Decision

**One declaration in `.qops/config.yml`, rendered into P1 and P2, with P3 for
what a glob cannot express.**

1. **`.qops/config.yml` is canonical for the roster.** `.claude/agents/*.md`
   frontmatter is *rendered* from it by `qops install`, the way the workflows
   already are, and `doctor` reports a hand edit. The existing equality tests
   become drift checks.
2. **`.claude/settings.json` becomes a rendering, not a scaffold.** It joins
   `render_all` and `drift`. Its content is **the substrate standard set from
   the template, plus `permissions.extra.allow/deny` from the config** — the
   standard set stays owned by qops and updatable in place; the project-shaped
   allow (a local database, a project's own tooling) gets a declared home that
   survives an update. The `py -3` allows currently living only in this repo's
   copy move into the template.
3. **Per-role command rules live at `agents.<role>.allow/deny`**, rendered into
   `--allowedTools`/`--disallowedTools` at launch (P2), and read into
   `guard.check`'s ctx beside `unattended` — the launcher sets `QOPS_ROLE`, the
   same idiom `QOPS_UNATTENDED` already proves works (P3). No cell above needs
   a mechanism that does not exist, except the scribe's path scope, which is P3
   and is named as unbuilt.
4. **`delegation_cap` is deleted.** It has no reader, no test and no
   enforcement point — it appears in the config, the template and the contract
   doc and nowhere else. A decision whose critic is not a test is a preference
   (GL-53), and this one no longer has even prose stating it.

**The runner-up, rejected on the record: delete the config's `agents:` block
and make the frontmatter canonical.** It is the smaller diff and it removes the
duplication just as completely. Rejected because `qops_pickup` already reads
the config to build a launch, so the frontmatter would have to be parsed as
YAML-in-markdown by every consumer's loop; because the frontmatter is
Claude-Code-shaped and the declaration must outlive one harness; and because a
per-role command rule has nowhere to live in a file whose schema another
product owns.

## What makes this wrong

Writing the table above and nothing else. Every cell here names a point, and
the two cells that name a point which does not exist yet — the scribe's path
scope and `QOPS_ROLE` in the guard's ctx — say so in the cell rather than
implying coverage. A follow-up that renders the declaration but leaves the
cells unenforced reproduces #13's defect one level up.

## Consequences

- **P1 is session-wide and stays that way.** An in-session subagent inherits
  the session's permission set; only a role launched as its own process
  (`plan_argv`, `launch_argv`) can carry a narrower one. Four of the six roles
  have no launcher, so their cells rest on the tool list plus P3. This is the
  honest limit of the decision and is not closed by it.
- **`settings.json` becomes rendered, so a hand edit becomes a `doctor`
  problem.** Consumers that have already hand-edited theirs will see drift on
  the first `qops install` after this lands. That is the intended signal, and
  the migration is to move their edits into `permissions.extra`.
- **Two contract keys change**: `permissions.extra` is added, `delegation_cap`
  is removed. `docs/reference/qops-contract.md` is frozen, so both are
  collected and applied under the contract's own rule rather than inside a
  consumer's first week.
- **The `gh api` write denial is untouched.** It is the owner's taken decision,
  and this ADR only inherits it into every row of class 7.
