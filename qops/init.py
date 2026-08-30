"""`qops init` scaffolds a blank repo to a clean `doctor` (contract's five
mechanical preconditions), and prints the three the owner/machine still has to
do by hand.

No `.qops/config.yml` exists yet when this runs, so `qops/__main__.py`
special-cases this verb: it hands `init.main` the bare cwd instead of the
loaded config every other verb gets.
"""

import json
import sys
from importlib.metadata import metadata as _qops_metadata
from importlib.metadata import version as _qops_version
from pathlib import Path

from . import install

TEMPLATES = Path(__file__).parent / "templates"
SKILLS = ("interview", "spec-to-issue", "triage", "pending")

NEXT_STEPS = """
Not done by `qops init` — the owner's or the machine's, not the package's:

  - create the {repo} repo, if it does not exist yet
  - python scripts/qops_import.py --labels
  - open a Claude Code session in this folder and run /interview to set the
    first goal
  - branch protection on {default_branch}, with the gate as a required check,
    plus "Allow auto-merge" and "Automatically delete head branches", both on.
    Both are CADR-0003's recorded table, so they are two commands rather than
    a browser trip. Run them at a keyboard: the guard refuses a `gh api` write
    in an unattended run and prompts you in an attended one (#235).

    Save this as protection.json — a file rather than a heredoc, because the
    shell on this host is not assumed (ADR-0009):

      {{
        "required_status_checks": {{
          "strict": true,
          "contexts": ["test", "gate", "tripwires", "doc-links"]
        }},
        "enforce_admins": true,
        "required_pull_request_reviews": {{"required_approving_review_count": 0}},
        "restrictions": null,
        "allow_force_pushes": false,
        "allow_deletions": false
      }}

    then:

      gh api -X PUT repos/{repo}/branches/{default_branch}/protection --input protection.json
      gh api -X PATCH repos/{repo} -F allow_auto_merge=true -F delete_branch_on_merge=true

    The approval count is 0 on purpose and 1 is the deadlock CADR-0003 records:
    GitHub does not let a PR's author approve it, and with `enforce_admins` on
    a one-maintainer repo there is no bypass either.
  - trust this workspace once (open Claude Code here interactively)
  - python -m qops install — renders the workflows AND registers pickup-loop's
    scheduled task on this host, disabled. Decline it with `pickup_task: false`
    in .qops/config.yml first; `qops init` has registered nothing.
  - enable the loop, once you want it: it is the one that costs money
""".strip("\n")


def _qops_pin() -> str:
    """The `requirements.txt` line a consumer pins qops with.

    #260: this was `qops=={version}` for one release, which is a PyPI pin, and
    qops is not on PyPI — `pip install -r requirements.txt` resolved to nothing
    and CI stayed red on the first push anyway, so #252 moved its failure from
    ModuleNotFoundError to the install step rather than closing it. The name is
    also unclaimed on PyPI, so a bare pin is a dependency-confusion exposure
    the day anyone publishes under it. The git URL is the only form that
    installs, and it is the one `README.md`'s install line already shows.

    Both halves are derived from the running package rather than written down a
    second time: the version from `_qops_version`, the URL from the
    `Project-URL` entry `pyproject.toml`'s `[project.urls]` produces.
    `Home-page` is `None` under both install shapes measured — a git-tag
    install and an editable one — so `Project-URL` is the entry to read.
    """
    homepage = next(
        value.split(",", 1)[1].strip()
        for key, value in _qops_metadata("qops").items()
        if key == "Project-URL" and value.lower().startswith("homepage,"))
    return f"qops @ git+{homepage}@v{_qops_version('qops')}"


def _render(name: str, values: dict) -> str:
    text = (TEMPLATES / name).read_text(encoding="utf-8")
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", str(value))
    return text


def _parse(argv: list[str]) -> dict:
    values = {"default_branch": "master"}
    it = iter(argv)
    for arg in it:
        if arg == "--project":
            values["project"] = next(it, None)
        elif arg == "--repo":
            values["repo"] = next(it, None)
        elif arg == "--python":
            values["python"] = next(it, None)
        elif arg == "--default-branch":
            values["default_branch"] = next(it, values["default_branch"])
    interactive = sys.stdin.isatty()
    prompts = {"project": "project name", "repo": "GitHub owner/name",
              "python": "the interpreter command this host's hooks should "
                       "invoke (ADR-0009 — nothing else in qops guesses one)"}
    for key, label in prompts.items():
        if values.get(key):
            continue
        if not interactive:
            raise SystemExit(f"qops init: --{key.replace('_', '-')} is "
                             f"required (no tty to prompt on)")
        try:
            values[key] = input(f"{label}: ").strip()
        except EOFError:
            raise SystemExit(f"qops init: --{key.replace('_', '-')} is "
                             f"required (stdin closed before it was answered)")
    missing = [k for k in prompts if not values.get(k)]
    if missing:
        raise SystemExit(f"qops init: missing {', '.join(missing)}")
    return values


def main(argv: list[str], root: Path, cfg: dict) -> int:
    root = Path(root)
    existing = root / ".qops" / "config.yml"
    if existing.exists():
        print(f"qops init: {existing} already exists — nothing to scaffold",
              file=sys.stderr)
        return 2
    try:
        values = _parse(argv)
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return 2

    config_text = _render("config.yml.tmpl", values)
    import yaml
    new_cfg = yaml.safe_load(config_text)

    (root / ".qops").mkdir(parents=True, exist_ok=True)
    (root / ".qops" / "config.yml").write_text(config_text, encoding="utf-8",
                                                newline="\n")
    (root / "CLAUDE.md").write_text(_render("claude_md.tmpl", values),
                                    encoding="utf-8", newline="\n")
    claude_dir = root / ".claude"
    claude_dir.mkdir(exist_ok=True)
    # `.claude/settings.json` is NOT written here: `install.render_all` below
    # renders it from the same template plus the parsed config, and it is the
    # only renderer (#158). A second one is how the scaffold and the template
    # drifted in the first place — `init` wrote a copy nothing ever re-read,
    # and `_render` takes `values`, which cannot see `permissions.extra`.

    skills_dir = claude_dir / "skills"
    for name in SKILLS:
        dest = skills_dir / name
        dest.mkdir(parents=True, exist_ok=True)
        body = (TEMPLATES / "skills" / name / "SKILL.md").read_text(
            encoding="utf-8")
        (dest / "SKILL.md").write_text(body, encoding="utf-8", newline="\n")
    # The six role files, on a fresh repo only. `agent_drift` reports a missing
    # one as a problem, so a scaffold that never wrote them makes every fresh
    # install start red - and the row's "never overwrite a consumer's" holds:
    # a repo being initialized has none to overwrite.
    agents_dir = claude_dir / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    for role in install.AGENT_ROLES:
        body = (install.AGENT_TEMPLATES / f"{role}.md").read_text(encoding="utf-8")
        (agents_dir / f"{role}.md").write_text(body, encoding="utf-8",
                                               newline="\n")

    (root / "skills-lock.json").write_text(
        json.dumps({"version": 1, "skills": {}}, indent=2) + "\n",
        encoding="utf-8", newline="\n")

    # #252: the install block's first branch (already correct, ADR-0024) only
    # fires in a repo that pins qops — a fresh scaffold had neither
    # requirements.txt nor pyproject.toml, took the "qops is a subdirectory"
    # branch instead, and every rendered job died with ModuleNotFoundError.
    # Pinned to the qops that is actually running `init`, not a hardcoded
    # string that would go stale at the next tag.
    (root / "requirements.txt").write_text(_qops_pin() + "\n",
                                           encoding="utf-8", newline="\n")

    # #264: INSTALL_DEPS installs `requirements-dev.txt` when it exists, and
    # nothing wrote one — so the seeded test below was a test the rendered CI
    # could not execute, and `test` and `gate` both died with "No module named
    # pytest". pytest belongs here and not in `requirements.txt`: a substrate
    # that drags a test runner into every consumer's runtime install is worse
    # than the defect it fixes.
    (root / "requirements-dev.txt").write_text("pytest\n", encoding="utf-8",
                                               newline="\n")

    # ci.test_command defaults to `python -m pytest -q`, which exits 5 ("no
    # tests ran") on an empty tree — a new repo needs a test file to exist
    # anyway, since R8 refuses `ready:auto` on a body that names none.
    tests_dir = root / "tests"
    tests_dir.mkdir(exist_ok=True)
    (tests_dir / "test_config.py").write_text(
        _render("test_config.py.tmpl", values), encoding="utf-8", newline="\n")

    for p in install.render_all(root, new_cfg) + install.render_adr_consumer(root):
        print(f"rendered {Path(p).relative_to(root)}")
    for msg in install.write_scripts(root):
        print(msg)
    print(f"wrote {values['project']}'s .qops/config.yml, CLAUDE.md, "
          f".claude/settings.json, .claude/skills/, skills-lock.json, "
          f"scripts/, docs/adr/consumer/")
    print()
    print(NEXT_STEPS.format(repo=values["repo"],
                            default_branch=values["default_branch"]))
    return 0
