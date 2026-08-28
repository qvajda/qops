"""`qops install` renders .github/workflows from templates + .qops/config.yml
and registers `pickup-loop`'s scheduled task from the same config;
`qops doctor` detects drift between what is on disk and what the config says.

A workflow nobody may hand-edit is the point: the CLAUDE.md line cap and the
tripwire list live in config, and the workflow is a rendering of them.
"""

import importlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from . import ledger, reconcile

TEMPLATES = Path(__file__).parent / "templates"
WORKFLOWS = ("test.yml", "gate.yml", "guard.yml", "digest.yml", "groom.yml",
             "automerge.yml", "reviewer.yml")

_DOC_LINK = re.compile(r"docs/[A-Za-z0-9_./-]+\.md")

# Consumer-facing ADRs (#181, ADR-0035): decisions a rendered workflow or
# native skill may cite by number. They ship as package data under
# `templates/adr/`, copied verbatim into every consumer's `docs/adr/consumer/`
# so a citation resolves in *that* repo's tree, not just this one's — and
# numbered `CADR-NNNN`, a namespace of its own, so they can never collide with
# a consumer's own project-specific `docs/adr/000N-*.md`.
ADR_CONSUMER_DIR = TEMPLATES / "adr"
_CADR_CITE = re.compile(r"CADR-\d{4}")

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
        # `settings.json` is the only template that uses it, and it must:
        # ADR-0009 put `python:` in config precisely so nothing guesses an
        # interpreter, and a hardcoded interpreter in a template is a guess.
        "python": cfg.get("python", "python"),
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


# `scripts/qops_import.py` and `scripts/qops_pickup.py` are documented as
# required in a fresh repo (`init.NEXT_STEPS`, `docs/reference/loops.md`) but
# lived only in this repo's own `scripts/` — a `requirements.txt` consumer's
# `pip install` never pulls a sibling directory in, only what the package
# declares (ADR-0024's shapes). Packaged as data under `templates/scripts/`
# (#177) so they reach a consumer the same way a workflow template does,
# while the two files a sortie may not touch stay exactly where they are.
CONSUMER_SCRIPTS = ("qops_import.py", "qops_pickup.py")
SCRIPTS_SRC = TEMPLATES / "scripts"


def write_scripts(root: Path) -> list[str]:
    """Copy the packaged consumer scripts into `root/scripts/`.

    Not a rendering: these carry no `{{placeholder}}`, so a byte match against
    the packaged copy is enough to tell "untouched" from "the consumer edited
    this" — and only the second one is left alone, warned about instead of
    silently overwritten.
    """
    out = Path(root) / "scripts"
    out.mkdir(parents=True, exist_ok=True)
    messages = []
    for name in CONSUMER_SCRIPTS:
        text = (SCRIPTS_SRC / name).read_text(encoding="utf-8")
        dest = out / name
        if dest.exists():
            if dest.read_text(encoding="utf-8") == text:
                continue
            messages.append(
                f"scripts/{name}: exists and differs from the packaged copy "
                f"— left untouched, remove it to re-pull the packaged version")
            continue
        dest.write_text(text, encoding="utf-8", newline="\n")
        messages.append(f"wrote scripts/{name}")
    return messages


def script_drift(root: Path) -> list[str]:
    """Its own check, called from `doctor` beside `skill_drift` — not folded
    into `drift()`. `drift()` answers "does what `render_all` rendered still
    match the templates"; these are copied, not rendered, and `render_all`
    does not write them. Folding it in made every `render_all` then
    `drift() == []` assertion fail on a file `render_all` never claimed.
    """
    return [f"scripts/{name}: missing — run `qops install`"
            for name in CONSUMER_SCRIPTS
            if not (Path(root) / "scripts" / name).exists()]


SETTINGS = Path(".claude") / "settings.json"


def _merge(base: list, extra: list) -> list:
    """Order-preserving concatenation, first occurrence wins.

    `dict.fromkeys` rather than a `set` on purpose: a permission list is
    read by humans and a reordered one reads as a rewrite. It is also what
    makes the interpreter allows safe - where `python:` is `python` the
    template's literal set and its rendered set are the same strings, and
    this collapses them instead of shipping every rule twice.
    """
    return list(dict.fromkeys(list(base) + list(extra)))


def render_settings(cfg: dict) -> str:
    """`.claude/settings.json` - the substrate standard set, merged with
    the project's own `permissions.extra` (#158).

    The merge is additive in both halves and then **subtractive last**:
    every entry in the merged `deny` is removed from the merged `allow`,
    whichever half it came from. A project may widen the standard set and
    may narrow it; it may not hand itself back something the substrate
    denied. Append `extra.allow` after the subtraction and ADR-0016/0020
    stop resting on anything, because `Bash(gh api -X:*)` becomes one
    config edit away.
    """
    data = json.loads(render_one("settings.json", cfg))
    extra = (cfg.get("permissions") or {}).get("extra") or {}
    perms = data.setdefault("permissions", {})
    deny = _merge(perms.get("deny", []), extra.get("deny", []))
    allow = _merge(perms.get("allow", []), extra.get("allow", []))
    perms["allow"] = [a for a in allow if a not in set(deny)]
    perms["deny"] = deny
    return json.dumps(data, indent=2) + "\n"


def render_adr_consumer(root: Path) -> list[str]:
    """Copy every consumer-facing ADR into `docs/adr/consumer/` (#181).

    A citation with nothing copied is a dead link the moment it leaves this
    repo — that was the defect. The copy is verbatim: renumbering already
    happened once, when the file was named into `templates/adr/`.
    """
    out = Path(root) / "docs" / "adr" / "consumer"
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for src in sorted(ADR_CONSUMER_DIR.glob("CADR-*.md")):
        dest = out / src.name
        dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8",
                        newline="\n")
        written.append(str(dest))
    return written


def render_all(root: Path, cfg: dict) -> list[str]:
    out = Path(root) / ".github" / "workflows"
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for name in WORKFLOWS:
        p = out / name
        p.write_text(render_one(name, cfg), encoding="utf-8", newline="\n")
        written.append(str(p))
    settings = Path(root) / SETTINGS
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(render_settings(cfg), encoding="utf-8",
                        newline="\n")
    written.append(str(settings))
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
    # Its own message, deliberately. Every consumer scaffolded before #158
    # was invited to hand-edit this file - it was a scaffold, written once
    # and never re-read - so the first thing this check ever does on their
    # repo is report a hand edit they were right to make. Naming where the
    # additions now live is the whole point of the line.
    settings = Path(root) / SETTINGS
    name = SETTINGS.as_posix()
    if not settings.exists():
        problems.append(f"{name}: missing — run `qops install`")
    elif (settings.read_text(encoding="utf-8").replace("\r\n", "\n")
            != render_settings(cfg)):
        problems.append(f"{name}: hand-edited — move your additions to "
                        f"`permissions.extra.allow/deny` in .qops/config.yml, "
                        f"then `qops install`")
    return problems


# --- the pickup task (#12, ADR-0032) --------------------------------------
#
# The one loop that costs money was the one part of qops with no installer: its
# definition lived on the cron host, named that machine's interpreter and that
# machine's checkout, and sat at the root of the task namespace under a name
# with no project in it — so a second project installing qops replaced the
# first project's loop in silence. It is rendered from the config now, exactly
# like a workflow, and named per project so a second checkout gets a second
# task. Registering never enables: ADR-0009's cost argument rests on the
# expensive loop being off unless the owner turned it on.

TASK_FOLDER = "qops"
TASK_LEAF = "pickup-loop"
TASK_START = "07:23"
TASK_SCRIPT = ("scripts", "qops_pickup.py")


def _resolve_interpreter(token: str) -> str:
    """The config's interpreter, as the scheduler will have to find it.

    A registered task does NOT resolve its executable the way a shell does:
    no PATHEXT, and not the user's PATH either. `py` and even `py.exe` register
    fine here and then fail every fire with 0x80070002, "cannot find the file
    specified" — because the launcher is a per-user install and lives outside
    the machine PATH. Hourly, silently, on a host whose whole failure mode is
    silence (ADR-0009); found only by firing the task and reading its result.

    So the path is resolved **on the host, at install time**, and that is not
    the defect #12 named. What was wrong before was an absolute path *nothing
    generated*: written by hand, unable to follow `python:`, invalidated by a
    config change with nothing to notice. This one is derived from `python:`
    every install, re-derived on every host, and checked by `doctor`. The repo
    still names no interpreter but the one in the config.
    """
    found = shutil.which(token)
    if found:
        return found
    return token if Path(token).suffix else token + ".exe"


def _quote(arg: str) -> str:
    return f'"{arg}"' if " " in arg else arg


def task_spec(root: Path, cfg: dict) -> dict:
    """What the config says the scheduled task is. Pure — reads no host.

    A folder rather than a flat prefix: one query over the qops folder lists
    every project's loop on the machine at once, which a suffixed flat name
    cannot do.
    """
    root = Path(root).resolve()
    interpreter = str(cfg["python"]).split()
    interpreter[0] = _resolve_interpreter(interpreter[0])
    args = interpreter[1:] + [str(root.joinpath(*TASK_SCRIPT)), "--root", str(root)]
    # Default off, and that is the safety valve: the flagless form prints what
    # it would have picked and spends nothing, so the wiring can be proved
    # without starting an agent. It was baked into the hand-made task, where it
    # was unreachable.
    if cfg.get("pickup_launch", False):
        args.append("--launch")
    return {
        "path": f"\\{TASK_FOLDER}\\{cfg['project']}\\",
        "name": TASK_LEAF,
        "execute": interpreter[0],
        "arguments": " ".join(_quote(a) for a in args),
        "workdir": str(root),
    }


def task_id(spec: dict) -> str:
    return spec["path"] + spec["name"]


def a_linked_worktree(root: Path) -> bool:
    """The task belongs to the main checkout, and only to it.

    `pickup-loop` runs every unattended sortie in a worktree under
    `.qops/wt/loop`, which carries a tracked `.qops/config.yml` — so
    `find_root()` inside a sortie resolves to the worktree, and an `install`
    run there would render the same task name with the worktree as its root
    and `-Force` it over the real one. The picker would then be pointed at a
    tree that is `clean -fdx`ed at the start of every run, with a valid config
    and no error: the silent replacement this whole change exists to close,
    reached from inside the loop.

    A linked worktree has `.git` as a file holding a `gitdir:` pointer; a main
    checkout has it as a directory.
    """
    return (Path(root) / ".git").is_file()


def _ps_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _powershell(script: str):
    """The host's answer, or None when there is no host to ask.

    None is not "clean" — it is "unknown", and every caller reports it as
    nothing rather than as agreement. A POSIX runner has no scheduled tasks and
    no project's loop lives on it; `qops install` in CI renders workflows and
    stops there.
    """
    if os.name != "nt":
        return None
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        return None
    return subprocess.run([exe, "-NoProfile", "-NonInteractive", "-Command", script],
                          capture_output=True, text=True)


_QUERY_TASK = """
$ErrorActionPreference = 'Stop'
$t = Get-ScheduledTask -TaskPath @PATH@ -TaskName @NAME@ -ErrorAction SilentlyContinue
if (-not $t) { Write-Output 'null'; exit 0 }
$a = $t.Actions[0]
[pscustomobject]@{
  execute   = [string]$a.Execute
  arguments = [string]$a.Arguments
  workdir   = [string]$a.WorkingDirectory
  state     = [string]$t.State
} | ConvertTo-Json -Compress
"""

# Register, then disable ONLY a task that was not already enabled. Force keeps
# the definition current across a re-install; the conditional keeps an
# enable the owner performed from being quietly undone by one.
_REGISTER_TASK = """
$ErrorActionPreference = 'Stop'
$was = Get-ScheduledTask -TaskPath @PATH@ -TaskName @NAME@ -ErrorAction SilentlyContinue
$action = New-ScheduledTaskAction -Execute @EXECUTE@ -Argument @ARGUMENTS@ -WorkingDirectory @WORKDIR@
$trigger = New-ScheduledTaskTrigger -Once -At @START@ -RepetitionInterval (New-TimeSpan -Hours 1)
Register-ScheduledTask -TaskPath @PATH@ -TaskName @NAME@ -Action $action -Trigger $trigger -Force | Out-Null
if (-not $was -or $was.State -eq 'Disabled') {
  Disable-ScheduledTask -TaskPath @PATH@ -TaskName @NAME@ | Out-Null
}
(Get-ScheduledTask -TaskPath @PATH@ -TaskName @NAME@).State
"""

_UNREGISTER_TASK = """
$ErrorActionPreference = 'Stop'
$t = Get-ScheduledTask -TaskPath @PATH@ -TaskName @NAME@ -ErrorAction SilentlyContinue
if (-not $t) { Write-Output 'absent'; exit 0 }
Unregister-ScheduledTask -TaskPath @PATH@ -TaskName @NAME@ -Confirm:$false
Write-Output 'removed'
"""


def _script(template: str, spec: dict) -> str:
    out = template
    for key, value in (("@PATH@", spec["path"]), ("@NAME@", spec["name"]),
                       ("@EXECUTE@", spec["execute"]),
                       ("@ARGUMENTS@", spec["arguments"]),
                       ("@WORKDIR@", spec["workdir"]), ("@START@", TASK_START)):
        out = out.replace(key, _ps_literal(value))
    return out


def registered_task(spec: dict) -> dict | None:
    """The task as the host holds it: {} when absent, None when unknowable."""
    done = _powershell(_script(_QUERY_TASK, spec))
    if done is None or done.returncode != 0:
        return None
    try:
        found = json.loads(done.stdout.strip() or "null")
    except json.JSONDecodeError:
        return None
    return {} if found is None else found


def task_problems(spec: dict, found: dict | None) -> list[str]:
    """Drift between what the config renders and what the host has. Pure."""
    if found is None:
        return []
    name = task_id(spec)
    if not found:
        return [f"pickup task {name}: not registered — run `qops install`"]
    problems = []
    for field in ("execute", "arguments", "workdir"):
        want, got = spec[field], str(found.get(field, ""))
        if want.casefold() != got.casefold():
            problems.append(f"pickup task {name}: {field} is {got!r}, the config "
                            f"renders {want!r} — run `qops install`")
    return problems


def task_drift(root: Path, cfg: dict) -> list[str]:
    spec = task_spec(root, cfg)
    return task_problems(spec, registered_task(spec))


def task_state_of(found: dict | None) -> str:
    """Reported, never changed — enabling stays a deliberate owner action.

    Takes the query's answer rather than making it: `doctor` asks the host once
    and both the drift check and this line read that one answer.
    """
    if found is None:
        return "unknown (no scheduled-task host)"
    if not found:
        return "not registered"
    return str(found.get("state", "")).casefold() or "unknown"


def wants_the_task(cfg: dict) -> bool:
    """Whether this project keeps a picker on the host at all.

    A standing project decision, so it is a config key and not a flag: `qops
    install` is run by sorties and by CI as well as by hand, and a flag that
    has to be remembered every time is remembered none of them. A project that
    never runs the loop — every consumer that only wants the workflows, the
    guard and the brief — says so once and no install anywhere touches its
    machine's scheduler.
    """
    return bool(cfg.get("pickup_task", True))


def register_task(root: Path, cfg: dict) -> str:
    spec = task_spec(root, cfg)
    if not wants_the_task(cfg):
        return "pickup task: not registered — `pickup_task: false` in the config"
    if a_linked_worktree(root):
        return (f"pickup task {task_id(spec)}: not registered — this root is a "
                f"linked worktree and the task belongs to the main checkout")
    done = _powershell(_script(_REGISTER_TASK, spec))
    if done is None:
        return f"pickup task {task_id(spec)}: not registered, this host has no scheduler"
    if done.returncode != 0:
        return (f"pickup task {task_id(spec)}: registration failed — "
                f"{done.stderr.strip().splitlines()[-1] if done.stderr.strip() else 'no output'}")
    return f"registered pickup task {task_id(spec)}, state {done.stdout.strip().casefold()}"


def unregister_task(root: Path, cfg: dict) -> str:
    """So uninstalling a project leaves no orphan firing at a deleted tree."""
    spec = task_spec(root, cfg)
    if a_linked_worktree(root):
        return (f"pickup task {task_id(spec)}: not removed — this root is a "
                f"linked worktree and the task belongs to the main checkout")
    done = _powershell(_script(_UNREGISTER_TASK, spec))
    if done is None:
        return f"pickup task {task_id(spec)}: nothing to remove, this host has no scheduler"
    if done.returncode != 0:
        return f"pickup task {task_id(spec)}: removal failed — {done.stderr.strip()}"
    return f"pickup task {task_id(spec)}: {done.stdout.strip()}"


def broken_adr_citations(root: Path) -> list[str]:
    """Every `CADR-NNNN` cite in a *rendered* workflow, native skill body or
    agent role file resolves to a real file under this tree's
    `docs/adr/consumer/` (#181).

    Scans rendered output, not `qops/templates/` — the source always
    resolves against `templates/adr/`, that is not the citation that can go
    stale. What goes stale is a consumer's copy: never installed, or
    installed once and never refreshed after a qops upgrade added or
    renumbered a consumer-facing ADR.

    `.claude/agents/*.md` was outside this scan until #198, which is the
    surface where a wrong citation costs most: a role file IS the agent's
    instructions, so a bare number resolving to the consumer's own unrelated
    ADR is not a dead link, it is the planner reading someone else's rule.
    """
    root = Path(root)
    present = {"-".join(p.name.split("-", 2)[:2])
               for p in (root / "docs" / "adr" / "consumer").glob("CADR-*.md")}
    missing = []
    targets = list((root / ".github" / "workflows").glob("*.yml")) \
        + list((root / ".claude" / "skills").glob("*/SKILL.md")) \
        + list((root / ".claude" / "agents").glob("*.md"))
    for p in targets:
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for cited in sorted(set(_CADR_CITE.findall(text))):
            if cited not in present:
                missing.append(f"{p.relative_to(root)} -> {cited}")
    return sorted(missing)


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

    problems += upstream_skill_drift(declared)
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


def upstream_skill_drift(declared: dict) -> list[str]:
    """The consumer's declared native set covers every skill `qops init`
    itself scaffolds a fresh repo with (#179).

    A repo that ran `qops init` before a name was added to `init.SKILLS`
    never gets it back — neither `install` nor `doctor` wrote a newly-added
    native skill into an existing consumer's declared set or tree, same shape
    of gap as the taxonomy (#178) and the scripts (#177). This only reports;
    adding the skill file and the config entry stays the owner's edit.

    `skills.native_skip` is the opt-out: a name a consumer decided on purpose
    it does not want, so this stops flagging it instead of failing forever.

    Imports `init` lazily — `init.py` imports this module, so importing it
    back at module load time would be circular.
    """
    from . import init as initmod
    native = set(declared.get("native", []))
    skip = set(declared.get("native_skip", []))
    missing = set(initmod.SKILLS) - native - skip
    return [f"native skill `{name}` is in qops's own scaffold list "
            f"(init.SKILLS) and missing from `.qops/config.yml`'s "
            f"`skills.native` — add it, or add it to `skills.native_skip` "
            f"to opt out (#179)" for name in sorted(missing)]
SKILL_TEMPLATES = TEMPLATES / "skills"


def _differs(ref: Path, theirs: Path) -> bool:
    """One installed file against the copy qops ships, newline-insensitively —
    a consumer on Windows has CRLF where the package has LF, and that is not
    drift."""
    return (theirs.read_text(encoding="utf-8").replace("\r\n", "\n")
            != ref.read_text(encoding="utf-8").replace("\r\n", "\n"))


def skill_body_drift(root: Path, cfg: dict) -> list[str]:
    """A consumer's native `.claude/skills/<name>/SKILL.md` matches qops's own
    current copy (#200).

    `skill_drift` compares the *set* of installed names against the declared
    one; the bodies were never read, and neither `install` nor `doctor` ever
    rewrote one. So a consumer pinned two releases back files and triages rows
    under the rules its skills were written against - `qhoto_printshop` at
    `v0.2.0` ran `interview`, `spec-to-issue` and `triage` from before
    ADR-0026's `gate:` split and ADR-0028's outcome bar - while every skill
    check `doctor` had was green. `agent_drift`'s argument (#183), one
    surface over.

    Only names qops actually ships a template for are compared: an external
    skill is someone else's, and a consumer's own native skill has no
    template to drift from. `skills.accept_drift` is the opt-out, a list of
    names for the same reason `skills.native_skip` is one - the declared set
    is a list, not a map.
    """
    declared = cfg.get("skills") or {}
    accepted = set(declared.get("accept_drift") or [])
    problems = []
    for name in sorted(set(declared.get("native", [])) - accepted):
        ref = SKILL_TEMPLATES / name / "SKILL.md"
        theirs = Path(root) / ".claude" / "skills" / name / "SKILL.md"
        # A missing one is `skill_drift`'s report, not this one's: two lines
        # for one fact is how a green run stops being read.
        if not ref.exists() or not theirs.exists():
            continue
        if _differs(ref, theirs):
            problems.append(
                f".claude/skills/{name}/SKILL.md: drifted from qops's current "
                f"copy — merge the update by hand, or add `{name}` to "
                f"`skills.accept_drift` in .qops/config.yml if this is "
                f"deliberate")
    return problems


AGENT_ROLES = ("coder", "interactor", "planner", "reviewer", "scribe", "triager")
AGENT_TEMPLATES = TEMPLATES / "agents"


def agent_drift(root: Path, cfg: dict) -> list[str]:
    """A consumer's `.claude/agents/<role>.md` matches qops's own current copy
    (#183). A role file IS the agent's instructions for that session - unlike a
    skill or a config key, a stale one does not miss a feature, it makes the
    agent behave by rules the owner already replaced.

    `agents.<role>.accept_drift: true` in `.qops/config.yml` is the opt-out: a
    project may have legitimately customized a role (its own tools/model
    choice), and the check flags drift once, the owner reviews and either
    merges it or accepts it deliberately - it never auto-overwrites.
    """
    problems = []
    declared = cfg.get("agents") or {}
    for role in AGENT_ROLES:
        if bool((declared.get(role) or {}).get("accept_drift", False)):
            continue
        ref = AGENT_TEMPLATES / f"{role}.md"
        theirs = Path(root) / ".claude" / "agents" / f"{role}.md"
        if not theirs.exists():
            problems.append(f".claude/agents/{role}.md: missing")
            continue
        if _differs(ref, theirs):
            problems.append(
                f".claude/agents/{role}.md: drifted from qops's current copy — "
                f"merge the update by hand, or set `agents.{role}.accept_drift: "
                f"true` in .qops/config.yml if this is deliberate")
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


# A key the template fills with a value unique to the project, not a shared
# setting - a consumer missing it is not the drift #180 is about.
_CONFIG_KEY_EXCLUDE = {"project", "repo", "tripwires"}


def config_key_drift(cfg: dict) -> list[str]:
    """Every top-level (and `ci:`/`agents:`) key in config.yml.tmpl is present
    in this project's own `.qops/config.yml` (#180).

    `qops init` writes a config from the template once; a key the template
    grows afterward has no path onto an already-scaffolded config, and every
    reader defaults it safely (`cfg.get(key, default)`), so nothing breaks
    mechanically - the config just silently stops being a truthful copy of
    the schema. Advisory, not a hard failure: the owner still picks the
    value, same as `qops init` would have asked.
    """
    # `render_one` needs `project` to fill the template's own placeholder; a
    # config missing it (the case this check itself might be reporting) must
    # still render, so it gets a throwaway fallback for that render alone.
    template = yaml.safe_load(render_one("config.yml", {**cfg, "project": cfg.get("project", "x")}))
    problems = []
    for key in sorted(set(template) - _CONFIG_KEY_EXCLUDE - set(cfg)):
        problems.append(f"`.qops/config.yml` is missing `{key}:`, present in "
                        f"config.yml.tmpl — add it (see docs/reference/"
                        f"qops-contract.md)")
    for section in ("ci", "agents"):
        missing = set(template.get(section) or {}) - set(cfg.get(section) or {})
        for key in sorted(missing):
            problems.append(f"`.qops/config.yml` `{section}:` is missing "
                            f"`{key}:`, present in config.yml.tmpl")
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
_NAMESPACES = ("type", "state", "mission", "gate", "origin", "priority")


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


# `mission` is excluded: its values are each project's own vocabulary
# (`substrate`/`consumers`/`housekeeping` here, `core` in the template's
# placeholder), never a value the CLI, a hook or a workflow branches on. Every
# other namespace plus `flags` IS code qops itself depends on
# (`BLOCKING_FLAGS`, `eligible()`, the templates) and is what #178 is about.
_SHIPPED_NAMESPACES = ("type", "state", "gate", "origin", "priority")


def shipped_taxonomy() -> set[str]:
    """The taxonomy qops's own installed version ships, read from the
    package's own `config.yml.tmpl` — never a hardcoded list (#178), or this
    check goes stale the same way the drift it looks for did.

    `config.yml.tmpl` is package data (`pyproject.toml`), so this reads the
    schema of whatever qops version is actually installed, not this checkout.
    """
    import yaml
    text = (TEMPLATES / "config.yml.tmpl").read_text(encoding="utf-8")
    for key in ("project", "repo", "default_branch", "python"):
        text = text.replace("{{" + key + "}}", "x")
    labels = yaml.safe_load(text).get("labels") or {}
    out = {f"{ns}:{v}" for ns in _SHIPPED_NAMESPACES for v in labels.get(ns, [])}
    return out | set(labels.get("flags", []))


def consumer_checks(root: Path, cfg: dict) -> list[str]:
    """Run each `doctor_checks:` entry the consumer's own config declares
    (#209).

    `schema_drift` above is the only built-in check shaped by one project's
    domain data, and it has no seam for a second one. `doctor_checks:` is
    that seam: a list of `module:callable` strings, each imported from the
    consumer's root and called as `fn(root, cfg)`, returning the strings that
    merge into `doctor`'s problems exactly as a built-in check's would.

    A config declaring none returns before `importlib` or `sys.path` are
    touched at all. A declared entry that cannot be imported, or that does
    not return a `list[str]`, is itself reported by name — a check that
    silently declines to run is the exact failure `doctor` exists to catch.
    """
    entries = cfg.get("doctor_checks") or []
    if not entries:
        return []
    root = Path(root)
    problems = []
    sys.path.insert(0, str(root))
    try:
        for entry in entries:
            try:
                mod_name, _, fn_name = str(entry).partition(":")
                if not mod_name or not fn_name:
                    raise ValueError(f"expected 'module:callable', got {entry!r}")
                mod = importlib.import_module(mod_name)
                fn = getattr(mod, fn_name)
                result = fn(root, cfg)
                if not isinstance(result, list) or not all(isinstance(s, str) for s in result):
                    raise TypeError(
                        f"must return list[str], got {result!r}")
                problems += result
            except Exception as e:
                problems.append(
                    f"doctor_checks entry {entry!r} failed: {e}")
    finally:
        sys.path.remove(str(root))
    return problems


def taxonomy_drift(cfg: dict) -> list[str]:
    """A value in qops's shipped taxonomy and absent from this project's
    (#178). A consumer that bumps its pin without hand-diffing config.yml
    against qops's own gets a tracker silently missing labels the CLI and
    hooks assume exist — #105's `--labels` step no-opped exactly this way,
    against a taxonomy already a version stale.

    A project's taxonomy is free to be a superset (extra namespaces, extra
    values); only a value shipped and missing is reported.
    """
    missing = shipped_taxonomy() - taxonomy(cfg)
    return [f"label {s!r} is in qops's shipped taxonomy and missing from "
            f"this project's `labels:` — add it to .qops/config.yml"
            for s in sorted(missing)]


# A test file named anywhere in the issue body: `tests/test_x.py`, or a bare
# `test_something`. Deliberately loose - the assertion is that a sortie names
# the thing that will judge it, not that the name resolves today.
_NAMES_A_TEST = re.compile(r"tests?[/\\][\w./\\-]*\.py|\btest_\w+")

# Any flag that vetoes pickup regardless of everything else (#48/#122). It
# vetoes more than pickup now: `priority:parked` (#161) stops the picker, the
# planner and the decomposer together via this one set.
#
# `"blocked"` was a typo for `state:blocked` from the day this set was written
# (#73) — no label named bare `blocked` has ever shipped, so `state:blocked`
# vetoed nothing (#211). `blocking_flags_drift()` below is the assertion that
# would have caught it: every member here must be a label `shipped_taxonomy()`
# ships.
BLOCKING_FLAGS = {"no-auto", "state:blocked", "priority:parked"}


def blocking_flags_drift() -> list[str]:
    """Every member of `BLOCKING_FLAGS` is a label `shipped_taxonomy()` ships.

    Consumer-independent on purpose: the question is whether qops's own two
    surfaces (this set and `config.yml.tmpl`) agree with each other, not
    whether a consumer's config is stale (that's `taxonomy_drift`).
    """
    shipped = shipped_taxonomy()
    missing = sorted(f for f in BLOCKING_FLAGS if f not in shipped)
    return [f"BLOCKING_FLAGS names {s!r}, a label shipped_taxonomy() does not "
            f"ship" for s in missing]


def eligible(issue: dict) -> bool:
    """`ready:auto` is one route in; `origin:owner` naming a test is the other
    (ADR-0023). The second route writes no label — the filing was the grant,
    so it stays a predicate, never an edit. `gate:taste` never qualifies by
    that route: judgement is exactly what a named test cannot substitute for.

    Lives here, not in `scripts/qops_pickup.py`, so `doctor` (#71) can call
    the same predicate `pickup-loop` uses without `qops/` importing from
    `scripts/` — the dependency has to run the other way.

    `type:manual` refuses on both routes, before either branches: the label
    already states the deliverable is an owner action outside the repo, and
    an unattended coder pointed at one either writes code nobody asked for or
    burns three strikes and marks the row struck out (#49) — which reads as
    "the loop tried and this is hard" rather than "this was never the loop's
    to take" (#223).
    """
    labels = {l["name"] for l in issue.get("labels", [])}
    if "type:manual" in labels:
        return False
    if "state:planned" not in labels:
        return False
    if labels & BLOCKING_FLAGS:
        return False
    if "gate:none" in labels or not any(l.startswith("gate:") for l in labels):
        return False
    # R8 is a condition on *every* `ready:auto` row, not only on the
    # owner-filed route (ADR-0023: the grant "takes effect once R8 holds").
    # Short-circuiting on the label alone made this predicate disagree with
    # `doctor`'s own R8 invariant, and the two disagreeing is not a difference
    # of opinion: the picker launched five rows overnight whose gate could
    # never go green, leaving five PRs open and five rows on `state:building`.
    names_a_test = bool(_NAMES_A_TEST.search(issue.get("body") or ""))
    if "ready:auto" in labels:
        # `body` absent means the caller passed a fixture that cannot answer,
        # the same convention `doctor`'s R8 invariant uses.
        return names_a_test or issue.get("body") is None
    if "gate:taste" in labels or "origin:owner" not in labels:
        return False
    return names_a_test


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
_EXPECTED = re.compile(
    r"^[ \t]*Expected to touch:(?P<rest>.*?)(?:Must not touch:.*)?$",
    re.M | re.I,
)
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

# ADR-0029 §4: the interview stays the owner's; what happens under it does
# not. The trigger for decomposition must be a fact on the row, not an
# assumption that a `type:epic` row was interviewed just because it exists.
# The interview skill's own rule is that it "ends in something written down"
# - an ADR, for a Mission-routed row - so a reference to an ADR that actually
# exists on disk is that fact, checked mechanically rather than inferred from
# a label an unattended session could apply to itself.
#
# Lives here, not in `scripts/qops_pickup.py` (moved by #131): `qops pending`
# needs the same predicate the picker uses, and `qops/` may not import from
# `scripts/` - the dependency has to run the other way, same reason
# `eligible()`/`unwritable()` moved here for #71.
ADR_REF = re.compile(r"docs/adr/\d+-[\w-]+\.md")


def interviewed(root: Path, issue: dict) -> bool:
    """The epic's body names an ADR file that exists in this repo."""
    for m in ADR_REF.finditer(issue.get("body") or ""):
        if (Path(root) / m.group(0)).exists():
            return True
    return False


def plannable(issue: dict) -> bool:
    """The rows the loop may *plan* (#82, ADR-0029 §1).

    `state:triage` and nothing else: planning is the act that leaves triage, so
    a row anywhere else has already had it. The filing bar is the gate — a row
    whose body states no outcome cannot be planned into criteria, and guessing
    at one is how a plan invents work the owner never licensed (ADR-0028).

    `type:epic` is refused here rather than planned badly: an epic is where
    direction only the owner holds gets set, so it gets an interview and then
    #84's decomposition, never a plan instead of one (ADR-0029 §4).

    `no-auto` and `state:blocked` veto planning for the same reason they veto
    building — the flag says the owner is handling this one.
    """
    labels = {l["name"] for l in issue.get("labels", [])}
    if "state:triage" not in labels or "type:epic" in labels:
        return False
    if labels & BLOCKING_FLAGS:
        return False
    return states_an_outcome(issue.get("body") or "")


def decomposable(root: Path, issue: dict) -> bool:
    """The rows the loop may *decompose* (#84, ADR-0029 §4).

    `type:epic` and interviewed, same veto flags as `plannable()` - `no-auto`
    and `blocked` still mean the owner is handling this one.
    """
    labels = {l["name"] for l in issue.get("labels", [])}
    if "type:epic" not in labels or labels & BLOCKING_FLAGS:
        return False
    return interviewed(root, issue)


# Three consecutive failed runs on one row and the picker stops taking it.
# `strikes()`/`struck_out()` moved here alongside `plannable()`/`decomposable()`
# for the same reason (#131): `qops pending` reads the same strike history the
# picker refuses on, and duplicating the ledger walk would be a second
# definition that could disagree with the first.
STRIKES = 3

# A ledger grows forever, and an enablement six weeks ago is not this week's
# evidence. Releases older than this do not count toward a strike-out.
STRIKE_WINDOW_DAYS = 14


def strikes(root: Path, num: str, labels: frozenset[str] = frozenset(),
            now: str | None = None) -> int:
    """Consecutive failed runs on this row, most recent last.

    Consecutive, not cumulative: a `pickup` with no `pickup_release` after it
    is a run that worked, and it resets the count. The off-by-one here fails
    open - it keeps burning sessions - so the interleaved case is the one the
    tests lean on. A `pickup_skip` (#48) is not a strike: nothing was spent and
    no session ever attempted the row.

    `no-auto` absent means the owner cleared a prior strike-out (#99): the
    count then starts after the last `pickup_struck_out` event rather than
    from the top of the ledger, so the row gets a fresh budget instead of
    reading as struck out for the rest of `STRIKE_WINDOW_DAYS`. While
    `no-auto` is on the row, nothing has been cleared and the full history
    still counts.
    """
    cutoff = (datetime.fromisoformat(now) if now else datetime.now(timezone.utc)
              ) - timedelta(days=STRIKE_WINDOW_DAYS)
    records = [rec for rec in ledger.read(root) if str(rec.get("issue")) == str(num)]
    if "no-auto" not in labels:
        last_strike_out = max(
            (i for i, rec in enumerate(records) if rec.get("event") == "pickup_struck_out"),
            default=None)
        if last_strike_out is not None:
            records = records[last_strike_out + 1:]
    count, open_attempt = 0, False
    for rec in records:
        try:
            if datetime.fromisoformat(rec["ts"]) < cutoff:
                continue
        except (KeyError, ValueError):
            continue
        if rec.get("event") == "pickup":
            if open_attempt:      # the previous claim never released: it worked
                count = 0
            open_attempt = True
        elif rec.get("event") == "pickup_release":
            count += 1
            open_attempt = False
    return count


def struck_out(root: Path, num: str, labels: frozenset[str] = frozenset(),
               now: str | None = None) -> bool:
    return strikes(root, num, labels, now) >= STRIKES


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


def unlaunchable_and_auto_eligible(issues: list[dict],
                                   tracker_wide: bool = True) -> list[str]:
    """A row `pickup-loop` would pick and can never launch (#71).

    **Only on the tracker-wide run** (#173), for #92's reason in reverse. The
    substrate's own advice on such a row is "Work it in a session"
    (`report_unlaunchable`) — and an interactive session writes no claim, so
    the row is still `state:planned`, still auto-eligible, still names
    `.claude/` for the whole life of the PR that closes it. `gate` is a
    required status check, so reporting it on a *scoped* run holds the only
    fix there is. Only a human session can open that PR at all, which makes
    the finding stale by construction there. The daily sweep and a laptop
    `doctor` still see it, and that is where it is read.

    `eligible()` and `unwritable()` are pickup-loop's own predicates: a row
    is only worth reporting when *both* hold — auto-eligible alone is normal,
    and unwritable alone is any row whose deliverable touches `.claude/` and
    is not `ready:auto`, which is most of this backlog. Reporting either half
    alone would either miss the contradiction (#57) or make the whole backlog
    look broken (#167's failure for the status issue).
    """
    if not tracker_wide:
        return []
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


def _verbs_at(root: Path, ref: str) -> set[str] | None:
    out = subprocess.run(["git", "show", f"{ref}:qops/__main__.py"], cwd=root,
                         capture_output=True, text=True, timeout=30)
    if out.returncode != 0:
        return None
    return set(re.findall(r'^\s+"(\w+)":\s*\(', out.stdout, re.MULTILINE))


def _version_at(root: Path, ref: str) -> str | None:
    out = subprocess.run(["git", "show", f"{ref}:pyproject.toml"], cwd=root,
                         capture_output=True, text=True, timeout=30)
    if out.returncode != 0:
        return None
    m = re.search(r'^version\s*=\s*"([^"]+)"', out.stdout, re.MULTILINE)
    return m.group(1) if m else None


def version_bump_required(root: Path, base_ref: str | None = None,
                          head_ref: str | None = None) -> list[str]:
    """#182: a new verb is exactly what prior releases counted as
    version-worthy (README.md's v0.1.1 note; `qops migrate` landing 73
    commits before any tag caught up). A consumer pins to the latest tag —
    the only reproducible thing to pin to — so a verb that lands without a
    version bump is invisible to that pin until someone hits the missing
    verb by hand.

    This does not cut the tag; `test_the_tag_agrees_with_the_declared_version`
    (#40) already refuses a tag cut against a stale version. Together the two
    close the loop the file-only check would leave open: no verb lands
    without a bump, and no tag lands without matching the bump.
    """
    base_ref = base_ref if base_ref is not None else os.environ.get("GITHUB_BASE_REF")
    head_ref = head_ref if head_ref is not None else os.environ.get("GITHUB_HEAD_REF")
    if not base_ref or not head_ref:
        return []
    try:
        merge_base = subprocess.run(
            ["git", "merge-base", f"origin/{base_ref}", "HEAD"], cwd=root,
            capture_output=True, text=True, timeout=30, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return []
    base_verbs, head_verbs = _verbs_at(root, merge_base), _verbs_at(root, "HEAD")
    if base_verbs is None or head_verbs is None:
        return []
    new_verbs = head_verbs - base_verbs
    if not new_verbs:
        return []
    base_version, head_version = _version_at(root, merge_base), _version_at(root, "HEAD")
    if base_version == head_version:
        return [f"new verb(s) {sorted(new_verbs)} added but pyproject.toml "
                f"version is still {head_version} — bump it before this "
                f"merges (#182)"]
    return []


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


def conflicted_prs(cfg: dict, prs=None, tracker_wide: bool = True) -> list[str]:
    """Open PRs GitHub cannot build a merge ref for (#195).

    `mergeStateStatus: DIRTY` gets no `pull_request` event at all - not red,
    silent. `BLOCKED` (a required check pending, a review outstanding) and
    `BEHIND` (`reconcile.advance_behind` handles those) are working as
    designed, and `UNKNOWN` is a mergeability GitHub has not finished
    computing yet - none of those are problems here.

    `tracker_wide=False` is the #173 scoping rule: a conflict on some *other*
    PR is not this PR's row to fix, and `gate` is a required check, so
    reporting it on a scoped run would hold a PR red on something no sortie
    behind it can resolve.
    """
    if not tracker_wide:
        return []
    repo = cfg.get("repo")
    if not repo:
        print("doctor: skipping the conflicted-PR check — config names no `repo`")
        return []
    if prs is None:
        try:
            prs = reconcile.open_prs(repo, 200)
        except (OSError, subprocess.SubprocessError, RuntimeError,
                json.JSONDecodeError) as exc:
            print(f"doctor: skipping the conflicted-PR check — {exc}")
            return []
    problems = [f"PR #{pr.get('number')} (`{pr.get('headRefName')}`) is "
                f"DIRTY — GitHub cannot build a merge ref for it, no checks "
                f"will run"
                for pr in prs if pr.get("mergeStateStatus") == "DIRTY"]
    print(f"doctor: {len(prs)} open PRs checked for conflicts")
    return problems


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


_UNFETCHED = object()


def doctor(root: Path, cfg: dict, issues=_UNFETCHED) -> list[str]:
    """`issues`, when given, skips `open_issues()`'s own `gh issue list` call —
    `qops pending` (#131) already read the backlog once and passes it back
    here, so a run of both never issues the query twice."""
    problems = drift(root, cfg)
    problems += version_bump_required(root)
    if not wants_the_task(cfg):
        # A project that declared no picker is not drifting from one. It is
        # still checked for the opposite: a task registered under this
        # project's name while the config says there should be none is an
        # orphan, and an orphan of the expensive loop is the one worth naming.
        stray = registered_task(task_spec(root, cfg))
        if stray:
            problems.append(
                f"pickup task {task_id(task_spec(root, cfg))}: registered, but "
                f"the config says `pickup_task: false` — run "
                f"`qops install --unregister-task`")
    elif a_linked_worktree(root):
        # Not merely skipped for safety: from here the answer would be wrong.
        # The registered task names the main checkout and this root is not it,
        # so every sortie would read drift it has no way to fix.
        print("doctor: pickup task not judged from a linked worktree — it "
              "belongs to the main checkout")
    else:
        spec = task_spec(root, cfg)
        found = registered_task(spec)
        problems += task_problems(spec, found)
        # Reported, not judged: whether the expensive loop is on is the owner's
        # answer, and `doctor` neither changes it nor calls either state a
        # problem.
        print(f"doctor: pickup task {task_id(spec)} is {task_state_of(found)}")
    problems += skill_drift(root, cfg)
    problems += skill_body_drift(root, cfg)
    problems += agent_drift(root, cfg)
    problems += script_drift(root)
    problems += schema_drift(root, cfg)
    problems += config_key_drift(cfg)
    problems += undeclared_labels(cfg)
    problems += taxonomy_drift(cfg)
    problems += consumer_checks(root, cfg)
    problems += blocking_flags_drift()
    if issues is _UNFETCHED:
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
        problems += unlaunchable_and_auto_eligible(judged,
                                                   tracker_wide=judged is issues)
        problems += redundant_no_auto(judged)
        problems += r8_proof(root, issues)
        problems += conflicted_prs(cfg, tracker_wide=judged is issues)
    elif strict():
        # The reason is already printed by open_issues(), one line up. What was
        # missing is that the skip left no state behind: the step passed, and
        # "doctor: clean" sat directly under the skip line (#44).
        problems.append("the open-issue invariants were not evaluated - the "
                        "backlog was unreadable, see the skip above (QOPS_STRICT)")
    problems += [f"broken doc citation: {m}" for m in broken_doc_links(root)]
    problems += [f"broken ADR citation: {m}" for m in broken_adr_citations(root)]
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
    if "--unregister-task" in argv:
        print(unregister_task(root, cfg))
        return 0
    written = render_all(root, cfg) + render_adr_consumer(root)
    for p in written:
        print(f"rendered {Path(p).relative_to(Path(root))}")
    for msg in write_scripts(root):
        print(msg)
    print(register_task(root, cfg))
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
