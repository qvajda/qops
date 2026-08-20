"""`qops install` renders .github/workflows from templates + .qops/config.yml;
`qops doctor` detects drift between what is on disk and what the config says.

A workflow nobody may hand-edit is the point: the CLAUDE.md line cap and the
tripwire list live in config, and the workflow is a rendering of them.
"""

import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

TEMPLATES = Path(__file__).parent / "templates"
WORKFLOWS = ("test.yml", "gate.yml", "guard.yml", "digest.yml", "groom.yml",
             "automerge.yml")

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


def issue_invariants(issues: list[dict], cfg: dict) -> list[str]:
    """`validate.require_on_open` and finding 1, asserted against a list of
    issues. Pure, so it is driven off a fixture and never off the tracker."""
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
        if (state not in (None, "state:triage")
                and issue.get("body") is not None
                and not states_an_outcome(issue["body"])):
            problems.append(f"#{num}: {state} and the body states no outcome — "
                            f"nothing downstream can tell what done looks like "
                            f"(ADR-0028)")
    return problems


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


def doctor(root: Path, cfg: dict) -> list[str]:
    problems = drift(root, cfg)
    problems += skill_drift(root, cfg)
    problems += schema_drift(root, cfg)
    problems += undeclared_labels(cfg)
    issues = open_issues(cfg)
    if issues is not None:
        # Positive evidence, not the absence of the skip line. "verify by
        # measurement, not by status code" (CLAUDE.md): a gate log that only
        # says `doctor: clean` cannot distinguish an evaluated backlog from a
        # skipped one, which is exactly how #44 hid.
        print(f"doctor: invariants evaluated against {len(issues)} open rows "
              f"on {cfg.get('repo')}")
        problems += issue_invariants(issues, cfg)
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
