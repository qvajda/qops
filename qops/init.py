"""`qops init` scaffolds a blank repo to a clean `doctor` (contract's five
mechanical preconditions), and prints the three the owner/machine still has to
do by hand.

No `.qops/config.yml` exists yet when this runs, so `qops/__main__.py`
special-cases this verb: it hands `init.main` the bare cwd instead of the
loaded config every other verb gets.
"""

import json
import sys
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
  - branch protection on {default_branch}, with the gate as a required check
  - "Allow auto-merge" and "Automatically delete head branches", both on
  - trust this workspace once (open Claude Code here interactively)
  - python -m qops install — renders the workflows AND registers pickup-loop's
    scheduled task on this host, disabled. Decline it with `pickup_task: false`
    in .qops/config.yml first; `qops init` has registered nothing.
  - enable the loop, once you want it: it is the one that costs money
""".strip("\n")


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
    (root / "skills-lock.json").write_text(
        json.dumps({"version": 1, "skills": {}}, indent=2) + "\n",
        encoding="utf-8", newline="\n")

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
