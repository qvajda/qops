"""`qops install` renders .github/workflows from templates + .qops/config.yml;
`qops doctor` detects drift between what is on disk and what the config says.

A workflow nobody may hand-edit is the point: the CLAUDE.md line cap and the
tripwire list live in config, and the workflow is a rendering of them.
"""

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

from . import reconcile

TEMPLATES = Path(__file__).parent / "templates"
WORKFLOWS = ("test.yml", "gate.yml", "guard.yml", "digest.yml", "groom.yml",
             "automerge.yml", "reviewer.yml")

_DOC_LINK = re.compile(r"docs/[A-Za-z0-9_./-]+\.md")

# The one dependency-install block every rendered job that runs Python uses
# (ADR-0024). It is a single constant because the three copies that preceded it
# diverged into three different bugs, each surfacing only in a repo shaped
# unlike the one that rendered it: #1 (pyproject-only repo installed nothing and
# `test` could not start), #21 (`pip install pyyaml` only, and both guard jobs
# import qops), and the same hole still open in digest.yml's reconcile job.
#
# The three shapes it must cover, asserted by
# `tests/test_qops.py::test_the_install_block_installs_qops_in_every_repo_shape`:
#   requirements.txt  a consumer pins qops there, so that is where it comes from
#   pyproject.toml    the repo IS the package (this one) - install it editable
#   neither           qops is a subdirectory; only pyyaml is missing
INSTALL_DEPS = """python -m pip install --upgrade pip
if [ -f requirements.txt ]; then pip install -r requirements.txt
elif [ -f pyproject.toml ]; then pip install -e .
else pip install pyyaml; fi
if [ -f requirements-dev.txt ]; then pip install -r requirements-dev.txt; fi"""

# Every `run: |` that carries it sits at the same depth in a workflow step.
_RUN_INDENT = " " * 10


def context(cfg: dict) -> dict:
    ci = cfg.get("ci", {})
    return {
        "project": cfg["project"],
        "repo": cfg.get("repo", ""),
        "default_branch": cfg.get("default_branch", "master"),
        "python_version": ci.get("python_version", "3.12"),
        "test_command": ci.get("test_command", "python -m pytest -q"),
        "gate_command": ci.get("gate_command", "python -m pytest -q"),
        "runs_on": ci.get("runs_on", "ubuntu-latest"),
        "digest_cron": ci.get("digest_cron", "0 6 * * *"),
        "groom_cron": ci.get("groom_cron", "0 5 * * 1"),
        "status_issue_label": ci.get("status_issue_label", "qops:status"),
        # Default true: a project that says nothing wants its digest.
        "digest_posts_on_schedule":
            "true" if ci.get("digest_posts_on_schedule", True) else "false",
        "claude_md_max_lines": str(cfg["claude_md_max_lines"]),
        # Not from config, and deliberately: which files a repo declares its
        # dependencies in is the repo's shape, not the project's preference,
        # and the block has to handle every shape rather than be told one.
        "install_deps": INSTALL_DEPS.replace("\n", "\n" + _RUN_INDENT),
    }


def render_one(name: str, cfg: dict) -> str:
    text = (TEMPLATES / (name + ".tmpl")).read_text(encoding="utf-8")
    for key, value in context(cfg).items():
        text = text.replace("{{" + key + "}}", str(value))
    left = re.search(r"\{\{(\w+)\}\}", text)
    if left:
        raise KeyError(f"{name}: no config value for {{{{{left.group(1)}}}}}")
    return text


def render_all(root: Path, cfg: dict) -> list[str]:
    out = Path(root) / ".github" / "workflows"
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for name in WORKFLOWS:
        p = out / name
        p.write_text(render_one(name, cfg), encoding="utf-8", newline="\n")
        written.append(str(p))
    return written


def drift(root: Path, cfg: dict) -> list[str]:
    problems = []
    for name in WORKFLOWS:
        p = Path(root) / ".github" / "workflows" / name
        if not p.exists():
            problems.append(f"{name}: missing — run `qops install`")
            continue
        if p.read_text(encoding="utf-8").replace("\r\n", "\n") != render_one(name, cfg):
            problems.append(f"{name}: hand-edited — edit .qops/config.yml or the "
                            f"template, then `qops install`")
    return problems


def broken_doc_links(root: Path) -> list[str]:
    """Every docs/*.md path cited from code must resolve (PRD §7 Phase 4).

    Phase 3 broke 13 of 15 by archiving 89 docs. Only a check caught it.
    """
    root = Path(root)
    cfg_roots = []          # no config, no roots: the list is the project's
    try:
        from . import config as qconfig
        cfg_roots = qconfig.load(root).get("doc_link_roots", cfg_roots)
    except Exception:
        pass
    missing = []
    for tree in cfg_roots:
        for p in (root / tree).rglob("*.py"):
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for cited in set(_DOC_LINK.findall(text)):
                if not (root / cited).exists():
                    missing.append(f"{p.relative_to(root)} -> {cited}")
    return sorted(missing)


def skill_drift(root: Path, cfg: dict) -> list[str]:
    """The installed skill set equals the declared one (ADR-0018).

    ADR-0013 named the count as its mitigation and asked a human to re-read it.
    Nobody did, and 11 accepted skills became 19 installed. So it is a check.

    A MISSING external is not a problem: they are gitignored, reinstallable
    copies, so a fresh checkout (CI) legitimately has none. An EXTRA is - that
    is the drift that actually happened. The natives are tracked source and
    must be there.
    """
    root, problems = Path(root), []
    declared = cfg.get("skills") or {}
    native = set(declared.get("native", []))
    external = set(declared.get("external", []))
    if not native and not external:
        return ["`.qops/config.yml` declares no `skills:` set — ADR-0018"]

    skills_dir = root / ".claude" / "skills"
    installed = {p.name for p in skills_dir.iterdir() if p.is_dir()} \
        if skills_dir.is_dir() else set()
    for extra in sorted(installed - native - external):
        problems.append(f"skill `{extra}` is installed and not declared in "
                        f".qops/config.yml — uninstall it or declare it")
    for missing in sorted(native - installed):
        problems.append(f"native skill `{missing}` is declared and missing "
                        f"from .claude/skills/")

    lock_path = root / "skills-lock.json"
    if not lock_path.exists():
        return problems + ["skills-lock.json missing"]
    lock = json.loads(lock_path.read_text(encoding="utf-8")).get("skills", {})
    for name in sorted(external - set(lock)):
        problems.append(f"external skill `{name}` is declared and absent from "
                        f"skills-lock.json")
    for name in sorted(set(lock) - external):
        problems.append(f"skills-lock.json pins `{name}`, which is not in the "
                        f"declared external set")
    for name, entry in sorted(lock.items()):
        if not entry.get("ref"):
            problems.append(f"skills-lock.json: `{name}` has no upstream ref — "
                            f"drift against it cannot be detected (ADR-0018)")
    return problems


_CREATE_TABLE = re.compile(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);",
                            re.DOTALL)
_TABLE_LEVEL = ("UNIQUE(", "UNIQUE (", "FOREIGN KEY", "CHECK(", "CHECK (",
                "CONSTRAINT", "PRIMARY KEY(", "PRIMARY KEY (")


def _split_top_level(body: str) -> list[str]:
    """Comma-separated column/constraint clauses, ignoring commas nested
    inside a `CHECK(...)` or similar parenthesised clause."""
    clauses, depth, start = [], 0, 0
    for i, ch in enumerate(body):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            clauses.append(body[start:i])
            start = i + 1
    clauses.append(body[start:])
    return clauses


def _declared_schema(schema_sql: str) -> dict[str, set[str]]:
    """table -> declared column names, parsed from the declared schema file."""
    declared = {}
    for table, body in _CREATE_TABLE.findall(schema_sql):
        columns = set()
        for clause in _split_top_level(body):
            clause = clause.strip()
            if not clause or clause.startswith(_TABLE_LEVEL):
                continue
            columns.add(clause.split()[0])
        declared[table] = columns
    return declared


def schema_drift(root: Path, cfg: dict | None = None,
                 db_path: Path | None = None) -> list[str]:
    """Live DB has every table/column the declared schema declares (#160).

    The schema file is all `CREATE TABLE IF NOT EXISTS`, so a new column added
    there is a silent no-op against an already-created live table - GL-32
    shipped a standalone migration script that nothing runs and nothing
    checked for. A missing live DB (fresh checkout, CI) is not drift; there
    is nothing to compare against.

    Both paths are the *project's*, so they live in `.qops/config.yml` under
    `schema_check:` and a config that omits the block gets no check. A
    substrate repo has no database, and hardcoding one project's filenames
    into the substrate is the leak this whole phase exists to remove.
    """
    root = Path(root)
    spec = (cfg or {}).get("schema_check") or {}
    sql_rel, db_rel = spec.get("sql"), spec.get("db")
    if not sql_rel or not db_rel:
        return []
    db_path = Path(db_path) if db_path is not None else root / db_rel
    if not db_path.exists():
        return []
    declared = _declared_schema((root / sql_rel).read_text(encoding="utf-8"))
    problems = []
    conn = sqlite3.connect(str(db_path))
    try:
        for table, columns in declared.items():
            live = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if not live:
                problems.append(f"schema drift: table `{table}` is in {sql_rel} "
                                f"and missing from the live DB")
                continue
            for col in sorted(columns - live):
                problems.append(f"schema drift: `{table}.{col}` is in {sql_rel} "
                                f"and missing from the live DB - run its migration")
    finally:
        conn.close()
    return problems


# --- label invariants (#147) ----------------------------------------------
# Each of these is the machine version of something a human got wrong in the
# week of 2026-08-17: the sweep's hand-run step-4 pipeline, the acceptance
# run's finding 1, and the `qops:status` label that `ci:` named and the
# taxonomy never declared (#136 - the daily digest failed on it for weeks).
#
# `doctor` reports; the triager labels. Nothing here mutates an issue.

# A label, as written in config: `namespace:value`, no spaces. It matches
# `qops:status` and `ready:auto` and not a prose `why:` line.
_LABEL_LIKE = re.compile(r"^[a-z][a-z0-9_]*:[a-z][a-z0-9_.:-]*$")
_NAMESPACES = ("type", "state", "mission", "gate")


def taxonomy(cfg: dict) -> set[str]:
    labels = cfg.get("labels") or {}
    out = {f"{ns}:{v}" for ns in _NAMESPACES for v in labels.get(ns, [])}
    return out | set(labels.get("flags", []))


def _scalars(node, skip: str = "labels"):
    if isinstance(node, dict):
        for k, v in node.items():
            if k != skip:
                yield from _scalars(v, skip)
    elif isinstance(node, list):
        for v in node:
            yield from _scalars(v, skip)
    elif isinstance(node, str):
        yield node


def undeclared_labels(cfg: dict) -> list[str]:
    """Every label named anywhere in .qops/config.yml is in the taxonomy.

    `ci.status_issue_label: qops:status` was read by digest.yml and declared
    nowhere, so the importer never created it and the 06:00 UTC digest failed
    on a missing label every day. Cheap check, and the one that would have
    caught it.
    """
    declared = taxonomy(cfg)
    return [f"label {s!r} is named in .qops/config.yml and is not in the "
            f"`labels:` taxonomy" for s in sorted(set(_scalars(cfg)))
            if _LABEL_LIKE.match(s) and s not in declared]


# A test file named anywhere in the issue body: `tests/test_x.py`, or a bare
# `test_something`. Deliberately loose - the assertion is that a sortie names
# the thing that will judge it, not that the name resolves today.
_NAMES_A_TEST = re.compile(r"tests?[/\\][\w./\\-]*\.py|\btest_\w+")

# Any flag that vetoes pickup regardless of everything else (#48/#122).
BLOCKING_FLAGS = {"no-auto", "blocked"}


def eligible(issue: dict) -> bool:
    """`ready:auto` is one route in; `origin:owner` naming a test is the other
    (ADR-0023). The second route writes no label — the filing was the grant,
    so it stays a predicate, never an edit. `gate:taste` never qualifies by
    that route: judgement is exactly what a named test cannot substitute for.

    Lives here, not in `scripts/qops_pickup.py`, so `doctor` (#71) can call
    the same predicate `pickup-loop` uses without `qops/` importing from
    `scripts/` — the dependency has to run the other way.
    """
    labels = {l["name"] for l in issue.get("labels", [])}
    if "state:planned" not in labels:
        return False
    if labels & BLOCKING_FLAGS:
        return False
    if "gate:none" in labels or not any(l.startswith("gate:") for l in labels):
        return False
    if "ready:auto" in labels:
        return True
    if "gate:taste" in labels or "origin:owner" not in labels:
        return False
    return bool(_NAMES_A_TEST.search(issue.get("body") or ""))


# Paths the launch may not write, whatever the row says. Not a repo rule and
# not a config key: `--permission-mode acceptEdits` (#122) grants file edits
# and NOT edits to the files that configure Claude Code itself, which is the
# harness protecting an agent from rewriting its own role. That protection is
# right and is not what should widen - an unattended agent that may edit its
# own role, hooks or permission list has no controls left.
UNWRITABLE = (".claude/",)

# Only the `Expected to touch:` half of the Files section, and only the paths
# it backticks. Every specced row in this repo names `.claude/` under *Must not
# touch* - reading the wrong half would make the entire backlog unlaunchable,
# which is the one failure mode of this check that empties the queue silently.
_EXPECTED = re.compile(r"^[ \t]*Expected to touch:(?P<rest>.*)$", re.M | re.I)
_PATH = re.compile(r"`([^`]+)`")


def unwritable(body: str) -> list[str]:
    """The paths a row expects to touch that the launch may not write."""
    m = _EXPECTED.search(body or "")
    if not m:
        return []                      # no Files section is #42's question
    return [p for p in _PATH.findall(m.group("rest"))
            # removeprefix, not lstrip: lstrip strips *characters*, so
            # ".claude/x" lost its dot and matched nothing.
            if any(p.removeprefix("./").startswith(u) for u in UNWRITABLE)]

# The states no part of the pipeline advances on its own: `state:triage` waits
# on the planner, `state:blocked` on whatever blocks it. `ready:auto` sitting
# on either is a promise nothing can keep. Every other state is the row moving
# - pickup writes `state:building`, automerge writes `state:review`, reconcile
# closes `state:done` - and reporting those deadlocked the loop (#60).
STRANDED_STATES = {"state:triage", "state:blocked"}


# ADR-0028's filing bar. `ready:auto` is mechanical on `origin:owner`, so there
# is no grant-time check left and the body is the last thing between the owner's
# direction and an unattended commit to master. Rows write the section three
# ways (`## Acceptance`, `**Acceptance:**`, a bare `Acceptance:` line), so the
# marker is matched loosely and the *content* is what is actually asserted - a
# heading with nothing under it states no outcome.
_ACCEPTANCE = re.compile(r"^\s*(?:#{1,6}\s*|\*{1,2})?acceptance\b[:*\s]*", re.I)


# The states the filing bar does not judge. `state:triage` because a row the
# owner filed in one line must be allowed to sit there (#42). The terminal two
# because the bar exists so *downstream* can tell what done looks like, and
# nothing is downstream of done - a decision row goes `triage -> done` with no
# build in between, so it never meets a planner that would have written an
# acceptance section, and a `gate:taste` row cannot state a machine criterion
# by its own nature. #46 resolved that way and turned the gate red on the PR
# recording the decision (#89). `None` is a caller that cannot answer.
_BAR_EXEMPT = (None, "state:triage", "state:done", "state:cancelled")


# #92: the prose half of a blocker (`**Blocked by #80**`) and the label half
# (`state:planned`) can disagree once the blocker closes, and nothing ever
# makes the prose false again.
#
# **The claim form, not the words.** Anchored at the start of a line, through
# nothing but markdown emphasis. This row's own body was the first false
# positive: it *cites* #82's prose mid-sentence and *quotes* a run log saying
# `> #82 blocked by #80`, and neither is a row declaring a dependency. A body
# that discusses a blocker is not a body that has one.
_BLOCKED_BY = re.compile(r"^[ \t]*[*_]{0,2}blocked by\s+#(\d+)", re.I | re.M)

# The same carve-out #89 made for the filing bar: a finished row's history is
# allowed to describe what once blocked it, since nothing downstream reads it.
_BLOCKER_EXEMPT = ("state:done", "state:cancelled")


def states_an_outcome(body: str) -> bool:
    """An acceptance marker with something after it: on its own line, or on the
    next non-blank line that is not itself a heading.

    The judgement half - whether the stated outcome is a *good* one - is not
    here. That is the reviewer gate's, the same split R8 already makes between
    naming a test and the test proving anything.
    """
    lines = body.splitlines()
    for i, line in enumerate(lines):
        m = _ACCEPTANCE.match(line)
        if not m:
            continue
        if line[m.end():].strip():
            return True
        for nxt in lines[i + 1:]:
            if not nxt.strip():
                continue
            return not nxt.lstrip().startswith("#")
    return False


def issue_invariants(issues: list[dict], cfg: dict,
                     tracker_wide: bool = True) -> list[str]:
    """`validate.require_on_open` and finding 1, asserted against a list of
    issues. Pure, so it is driven off a fixture and never off the tracker.

    `tracker_wide` is False when the caller passed a *scoped* list — one PR's
    own row (`_rows_in_scope`). Every per-row invariant reads the same either
    way; the one that does not is #92's blocker check, which asks whether some
    *other* row is still open and can only answer that from a whole list."""
    problems = []
    # digest.yml opens the pinned status issue with exactly one label, and these
    # invariants rejected it — two halves of the substrate disagreeing about what
    # a valid issue is, for three permanent problems `doctor` could never clear
    # (#167). Machine-authored bookkeeping is not a sortie, and a gate that can
    # never be green stops being read.
    bookkeeping = cfg.get("ci", {}).get("status_issue_label")
    for issue in issues:
        num = issue.get("number")
        names = {l["name"] for l in issue.get("labels", [])}
        if bookkeeping and bookkeeping in names:
            continue
        for ns in cfg.get("validate", {}).get("require_on_open",
                                              ["type", "state", "gate"]):
            n = len([x for x in names if x.startswith(f"{ns}:")])
            if n != 1:
                problems.append(f"#{num}: carries {n} `{ns}:` labels, wants "
                                f"exactly one")
        if "gate:none" in names:
            problems.append(f"#{num}: `gate:none` — the gate was never decided, "
                            f"and it blocks ready:auto")
        # Finding 1: an inert flag reads as a filled queue. `pickup-loop`'s
        # eligible() requires state:planned, so ready:auto in a state nothing
        # advances is a promise nothing can keep, and it is invisible.
        #
        # It is only *those* states. Reading it as "anywhere but state:planned"
        # closed a deadlock on the loop (#60): pickup writes `state:building`
        # at launch, `automerge` clears it only after the merge, so a picked-up
        # row was a problem, `gate` went red, branch protection held the PR,
        # and the label was never cleared. Every row the loop picked bricked
        # itself - and since `gate` reads the whole tracker, it failed every
        # other open PR too.
        if "ready:auto" in names and names & STRANDED_STATES:
            stranded = ", ".join(sorted(names & STRANDED_STATES))
            problems.append(f"#{num}: `ready:auto` on {stranded} — nothing "
                            f"advances it from there, so pickup-loop can "
                            f"never pick it")
        # Triage R8, and the only half of it a machine can hold. The full suite
        # runs longer than one Bash call may, and a `claude -p` process exits
        # with its turn - so a sortie whose evidence of doneness IS the full
        # suite cannot finish, by construction (attempt 2, #57/#71). The rule
        # lived only in one repo's launch-prompt prose, which by GL-53 makes it
        # a preference. `body` absent means the caller passed a fixture that
        # cannot answer, not that the issue passes.
        if "ready:auto" in names and issue.get("body") is not None:
            if not _NAMES_A_TEST.search(issue["body"]):
                problems.append(f"#{num}: `ready:auto` and its plan names no "
                                f"test file — nothing can prove it done (R8)")
        # ADR-0028: the bar is on *leaving* triage, not on filing. A row the
        # owner filed in one line must be allowed to sit in triage - a control
        # that refuses the filing itself has moved the toil, not removed it.
        # `body` absent means the caller cannot answer, same as R8 above.
        state = next((n for n in names if n.startswith("state:")), None)
        if (state not in _BAR_EXEMPT
                and issue.get("body") is not None
                and not states_an_outcome(issue["body"])):
            problems.append(f"#{num}: {state} and the body states no outcome — "
                            f"nothing downstream can tell what done looks like "
                            f"(ADR-0028)")
        # #92: an open row whose body still says `Blocked by #n` for a blocker
        # that already closed. Labels are what pickup-loop reads; prose is
        # what an agent reads, and when they disagree the queue looks full
        # and moves nothing.
        #
        # **Only on the tracker-wide run**, because "not open" is derived from
        # the list passed in and `_rows_in_scope` hands a PR exactly one row —
        # on the merge path every blocker any row names would read as closed,
        # which is how this check first failed its own PR (#94). The daily
        # sweep (`digest.yml`) and a laptop `doctor` see the whole tracker,
        # and a stale blocker costs unattended sessions, not a merge.
        if (tracker_wide and state not in _BLOCKER_EXEMPT
                and issue.get("body") is not None):
            open_numbers = {str(i.get("number")) for i in issues}
            for blocker in sorted(set(_BLOCKED_BY.findall(issue["body"]))):
                if blocker not in open_numbers:
                    problems.append(f"#{num}: body says `Blocked by #{blocker}` "
                                    f"but #{blocker} is not open — the blocker "
                                    f"closed and the prose was never updated, "
                                    f"edit the body")
    return problems


def unlaunchable_and_auto_eligible(issues: list[dict]) -> list[str]:
    """A row `pickup-loop` would pick and can never launch (#71).

    `eligible()` and `unwritable()` are pickup-loop's own predicates: a row
    is only worth reporting when *both* hold — auto-eligible alone is normal,
    and unwritable alone is any row whose deliverable touches `.claude/` and
    is not `ready:auto`, which is most of this backlog. Reporting either half
    alone would either miss the contradiction (#57) or make the whole backlog
    look broken (#167's failure for the status issue).
    """
    return [f"#{i['number']}: auto-eligible and the launch cannot write "
            f"{', '.join(unwritable(i.get('body') or ''))} — pickup-loop "
            f"will skip it forever (#71)"
            for i in issues
            if eligible(i) and unwritable(i.get("body") or "")]


def redundant_no_auto(issues: list[dict]) -> list[str]:
    """A row where `no-auto` buys nothing `unwritable()` was not already
    catching for free (#127).

    `no-auto` answers authority, `gate:` answers judgement (ADR-0026) — but
    the only route in when a row names an `UNWRITABLE` path is already
    closed by `unlaunchable_and_auto_eligible()`'s reach check, whether or
    not `no-auto` is there. #83 carried the flag anyway and cost the owner
    a manual merge and a manual close for a reach the substrate already knew.

    Advisory only, deliberately: only the owner knows whether a given
    `no-auto` means "the launch cannot reach this" or "I am handling this
    one myself" — this reports the redundancy, it never removes the label.
    """
    return [f"#{i['number']}: `no-auto` on a `gate:machine` row that already "
            f"cannot write {', '.join(unwritable(i.get('body') or ''))} — "
            f"the reach check already withholds this, removing `no-auto` "
            f"hands back the merge and the close (#127)"
            for i in issues
            if "no-auto" in {l["name"] for l in i.get("labels", [])}
            and "gate:machine" in {l["name"] for l in i.get("labels", [])}
            and unwritable(i.get("body") or "")]


def _test_targets(body: str) -> list[str]:
    """The pytest node ids `_NAMES_A_TEST` finds in an issue body.

    The regex matches a file (`tests/test_x.py`) and a bare function
    (`test_x`) as two separate tokens even when the body writes them as one
    node id (`tests/test_x.py::test_x`) — reusing the regex rather than
    writing a second one that understands `::`. Stitch adjacent matches back
    together here instead.
    """
    targets = []
    for m in re.finditer(_NAMES_A_TEST, body):
        tok = m.group()
        if targets and body[targets[-1][1]:m.start()] == "::":
            targets[-1] = (targets[-1][0] + "::" + tok, m.end())
        else:
            targets.append((tok, m.end()))
    # Path-shaped only, deduplicated in first-seen order. `_NAMES_A_TEST` also
    # matches a bare `test_x` written in running prose, and pytest reads a bare
    # token as a path: it exits 4 with `file or directory not found` before a
    # single test runs, which R8 read as the change failing its own proof.
    # #27's PR failed on a sentence in its own plan. The loose half of the
    # regex stays - it is what keeps a barren body out of `ready:auto` - but
    # only a target pytest can resolve is worth spending a runner on.
    return list(dict.fromkeys(t for t, _ in targets
                              if "/" in t or "\\" in t))


def r8_proof(root: Path, issues: list[dict], base_ref: str | None = None,
             head_ref: str | None = None) -> list[str]:
    """ADR-0023's R8, made a proof rather than a filename regex.

    `_NAMES_A_TEST` (label time) only asks that the body names a test file —
    gameable by a named test with an empty body. This runs the named test(s)
    twice: once at HEAD, once at the PR's merge base with only the named test
    files carried forward. A test that passes at the merge base too proves
    nothing about the change, which is exactly the hollow case this exists
    to catch (finding, #27).

    Driven off `issues` and a git tree, never off the tracker directly, so a
    test can build both with a temporary repo. `base_ref`/`head_ref` default
    to the PR context GitHub sets (`GITHUB_BASE_REF`/`GITHUB_HEAD_REF`); no
    PR context means nothing to prove, and this is silent, even under
    `strict()` — R8 is a `ready:auto` rule, not a general invariant.
    """
    base_ref = base_ref if base_ref is not None else os.environ.get("GITHUB_BASE_REF")
    head_ref = head_ref if head_ref is not None else os.environ.get("GITHUB_HEAD_REF")
    if not base_ref or not head_ref:
        return []
    num = reconcile.issue_number(head_ref)
    if num is None:
        return []
    issue = next((i for i in issues if str(i.get("number")) == num), None)
    if issue is None:
        return []
    if "ready:auto" not in {l["name"] for l in issue.get("labels", [])}:
        return []
    targets = _test_targets(issue.get("body") or "")
    if not targets:
        return []

    root = Path(root)

    last: list[str] = []

    def run(cwd: Path) -> int:
        # The output is kept, not discarded. "the test it names fails at HEAD"
        # with nothing after it is unactionable from a CI log, and a run that
        # only reproduces on the runner is exactly when it is needed.
        out = subprocess.run([sys.executable, "-m", "pytest", "-q", *targets],
                             cwd=cwd, capture_output=True, text=True,
                             timeout=120)
        last[:] = (out.stdout + out.stderr).strip().splitlines()[-15:]
        return out.returncode

    try:
        merge_base = subprocess.run(
            ["git", "merge-base", f"origin/{base_ref}", "HEAD"], cwd=root,
            capture_output=True, text=True, timeout=30, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"doctor: skipping R8 proof for #{num} — could not find the "
              f"merge base ({exc})")
        return [f"#{num}: R8 proof did not run — {exc}"] if strict() else []

    head_rc = run(root)
    if head_rc == 5:
        return [f"#{num}: {targets} resolves to no test at HEAD — R8 cannot "
                f"prove it (ADR-0023)"]
    if head_rc != 0:
        print("\n".join(last))
        return [f"#{num}: the test it names fails at HEAD — its own change "
                f"does not pass R8's proof (ADR-0023)"]

    with tempfile.TemporaryDirectory() as base_dir:
        try:
            subprocess.run(["git", "worktree", "add", "--detach", base_dir,
                            merge_base], cwd=root, capture_output=True,
                           text=True, timeout=30, check=True)
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"doctor: skipping R8 proof for #{num} — could not check "
                  f"out the merge base ({exc})")
            return [f"#{num}: R8 proof did not run — {exc}"] if strict() else []
        try:
            test_dir = next((t.split("::")[0].replace("\\", "/").split("/")[0]
                             for t in targets if "/" in t or "\\" in t),
                            "tests")
            src, dst = root / test_dir, Path(base_dir) / test_dir
            if src.is_dir():
                shutil.rmtree(dst, ignore_errors=True)
                shutil.copytree(src, dst)
            base_rc = run(Path(base_dir))
        finally:
            subprocess.run(["git", "worktree", "remove", "--force", base_dir],
                           cwd=root, capture_output=True, text=True, timeout=30)

    if base_rc == 0:
        return [f"#{num}: the test it names passes without its change — R8 "
                f"proves nothing (ADR-0023)"]
    return []


# The three preconditions items 6-8 of docs/reference/qops-contract.md name as
# the owner's or the machine's, never the package's — `doctor` only ever reads
# them here, never writes them.
def owner_preconditions(root: Path, cfg: dict) -> list[str]:
    """Branch protection, auto-merge settings, and workspace trust.

    Each is checked best-effort and counted a problem on anything short of a
    confirmed yes — `gh` missing, unauthenticated, or the repo not existing
    yet are exactly the states a fresh repo is in, and they must read the same
    as a checked "no" rather than being silently skipped (contrast
    `open_issues`, which is right to skip: those invariants are read from a
    tracker this instrument already trusts to exist).
    """
    root = Path(root)
    problems = []
    repo = cfg.get("repo")
    branch = cfg.get("default_branch", "master")

    protected = False
    auto_merge_ready = False
    if repo:
        try:
            p = subprocess.run(
                ["gh", "api", f"repos/{repo}/branches/{branch}/protection"],
                capture_output=True, text=True, timeout=15)
            if p.returncode == 0:
                data = json.loads(p.stdout)
                checks = data.get("required_status_checks") or {}
                contexts = set(checks.get("contexts") or [])
                contexts |= {c.get("context") for c in checks.get("checks", [])}
                protected = any("gate" in (c or "") for c in contexts)
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
            pass
        try:
            p = subprocess.run(["gh", "api", f"repos/{repo}"],
                               capture_output=True, text=True, timeout=15)
            if p.returncode == 0:
                data = json.loads(p.stdout)
                auto_merge_ready = bool(data.get("allow_auto_merge")) and \
                    bool(data.get("delete_branch_on_merge"))
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
            pass

    if not protected:
        problems.append(
            f"branch protection on {branch} with the gate as a required "
            f"status check is not confirmed — the owner's to set (contract #6)")
    if not auto_merge_ready:
        problems.append(
            "\"Allow auto-merge\" and \"Automatically delete head branches\" "
            "are not both confirmed on — the owner's to set (contract #7)")

    if _trust_state(root) == "untrusted":
        problems.append(
            "the workspace has not been trusted here yet — run Claude Code "
            "interactively in this folder once (contract #8)")
    return problems


def _trust_state(root: Path) -> str:
    """"trusted", "untrusted", or "unknown" for `root` in `~/.claude.json`.

    A missing or unreadable file is "unknown", not "untrusted" (#19): a
    machine that has simply never opened Claude Code anywhere reads the same
    as one where this workspace's dialog was declined, and only the second is
    a problem `doctor` should report. `~/.claude.json` holds one root under
    either path-separator form on Windows, so the match is separator- and
    case-insensitive rather than an exact string compare.
    """
    claude_json = Path.home() / ".claude.json"
    if not claude_json.exists():
        return "unknown"
    try:
        data = json.loads(claude_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "unknown"
    target = str(root).replace("\\", "/").casefold()
    for key, entry in (data.get("projects") or {}).items():
        if str(key).replace("\\", "/").casefold() == target:
            if isinstance(entry, dict) and entry.get("hasTrustDialogAccepted"):
                return "trusted"
            return "untrusted"
    return "untrusted"


def open_issues(cfg: dict) -> list[dict] | None:
    """The tracker's open issues, or None with a printed reason.

    `doctor` is a local instrument. One that cannot run offline is worse than
    one that says why it is quiet (#147), so every failure here is a skip.
    """
    repo = cfg.get("repo")
    if not repo:
        print("doctor: skipping the label invariant — config names no `repo`")
        return None
    try:
        p = subprocess.run(["gh", "issue", "list", "--repo", repo, "--state",
                            "open", "--limit", "200", "--json",
                            "number,labels,body"], capture_output=True, text=True,
                           encoding="utf-8", timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"doctor: skipping the label invariant — gh unavailable ({exc})")
        return None
    if p.returncode != 0:
        print(f"doctor: skipping the label invariant — "
              f"gh exited {p.returncode}: {p.stderr.strip().splitlines()[:1]}")
        return None
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError as exc:
        print(f"doctor: skipping the label invariant — unreadable gh output ({exc})")
        return None


def strict() -> bool:
    """CI wants an unreadable backlog red; a laptop wants it quiet.

    `open_issues()` treats every failure as a skip, which is right for a local
    instrument (#147) and wrong for a required status check - a rate-limited
    `gh` would turn the gate green over a backlog nothing read. Explicit env
    rather than sniffing for `GITHUB_ACTIONS`, the same idiom `QOPS_UNATTENDED`
    already uses, so a consumer running `doctor` in some other CI gets it by
    setting the flag rather than by being recognised.
    """
    return os.environ.get("QOPS_STRICT") == "1"


def _on_a_pull_request() -> bool:
    """Whether this `doctor` run is the one `gate` makes on a PR.

    Both refs are set on every job of a `pull_request`-triggered workflow and
    on nothing else - a laptop run and the daily reconcile job see neither.
    """
    return bool(os.environ.get("GITHUB_BASE_REF")
                and os.environ.get("GITHUB_HEAD_REF"))


def _rows_in_scope(issues: list[dict]) -> tuple[list[dict], str]:
    """The rows this `doctor` run may report on, and a phrase naming which.

    On a PR that is the row the branch names, and nothing else. `gate` is a
    required status check, so a tracker-wide sweep there means one bad row
    anywhere fails every open PR at once - PR #58, a gitattributes chore, sat
    red on an unrelated row's labels, and an unattended sortie cannot make the
    tracker edit that would unblock it (#63). `r8_proof` already scopes this
    way; the label invariants were the half that never did.

    Off a PR - a laptop run, the daily reconcile job - the sweep is the whole
    tracker, unchanged. The sweep is moved off the merge path, not dropped.

    A branch naming no row (`no-issue/...`) judges no row: there is nothing on
    the tracker the PR is answerable to, and the daily job still sees every row.
    """
    head_ref = os.environ.get("GITHUB_HEAD_REF")
    if not _on_a_pull_request():
        return issues, f"{len(issues)} open rows"
    num = reconcile.issue_number(head_ref)
    if num is None:
        return [], f"no row — `{head_ref}` names none (0 of {len(issues)} open)"
    mine = [i for i in issues if str(i.get("number")) == num]
    return mine, (f"row #{num} alone, the row `{head_ref}` names "
                  f"({len(mine)} of {len(issues)} open)")


def doctor(root: Path, cfg: dict) -> list[str]:
    problems = drift(root, cfg)
    problems += skill_drift(root, cfg)
    problems += schema_drift(root, cfg)
    problems += undeclared_labels(cfg)
    issues = open_issues(cfg)
    if issues is not None:
        judged, scope = _rows_in_scope(issues)
        # Positive evidence, not the absence of the skip line. "verify by
        # measurement, not by status code" (CLAUDE.md): a gate log that only
        # says `doctor: clean` cannot distinguish an evaluated backlog from a
        # skipped one, which is exactly how #44 hid. It names the scope for the
        # same reason - the two runs are not the same claim (#63).
        print(f"doctor: invariants evaluated against {scope} "
              f"on {cfg.get('repo')}")
        # `_rows_in_scope` returns the list it was given, unchanged, when the
        # run is not on a PR — identity is the scope, exactly.
        problems += issue_invariants(judged, cfg, tracker_wide=judged is issues)
        problems += unlaunchable_and_auto_eligible(judged)
        problems += redundant_no_auto(judged)
        problems += r8_proof(root, issues)
    elif strict():
        # The reason is already printed by open_issues(), one line up. What was
        # missing is that the skip left no state behind: the step passed, and
        # "doctor: clean" sat directly under the skip line (#44).
        problems.append("the open-issue invariants were not evaluated - the "
                        "backlog was unreadable, see the skip above (QOPS_STRICT)")
    problems += [f"broken doc citation: {m}" for m in broken_doc_links(root)]
    settings = Path(root) / ".claude" / "settings.json"
    if not settings.exists():
        problems.append(".claude/settings.json missing — hooks are not installed")
    elif "qops" not in settings.read_text(encoding="utf-8"):
        problems.append(".claude/settings.json does not invoke qops")
    n = len((Path(root) / "CLAUDE.md").read_text(encoding="utf-8").splitlines())
    if n > cfg["claude_md_max_lines"]:
        problems.append(f"CLAUDE.md is {n} lines, cap is {cfg['claude_md_max_lines']}")
    # Contract items 6-8 are facts about the owner's account and host, not
    # about the diff, and `gate` is a required status check: a PR cannot grant
    # branch protection and a stateless runner has no `~/.claude.json` to have
    # accepted a trust dialog, so counting them here strands the branch on
    # something no sortie can ever fix. That is #63 exactly - the scoping
    # `_rows_in_scope` applies to the label sweep, applied to the half that
    # never had it. Off a PR the instrument still reads all three, which is
    # where they are answerable and where `init` prints them as next steps.
    if not _on_a_pull_request():
        problems += owner_preconditions(root, cfg)
    return problems


def main(argv: list[str], root: Path, cfg: dict) -> int:
    written = render_all(root, cfg)
    for p in written:
        print(f"rendered {Path(p).relative_to(Path(root))}")
    return 0


def doctor_main(argv: list[str], root: Path, cfg: dict) -> int:
    problems = doctor(root, cfg)
    for p in problems:
        print(p, file=sys.stderr)
    if problems:
        print(f"\n{len(problems)} problem(s).", file=sys.stderr)
        return 1
    print("doctor: clean.")
    return 0
