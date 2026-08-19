"""pickup-loop — pick the next sortie an unattended agent may start.

Default OFF. Registered as a disabled Windows scheduled task (`qops-pickup-loop`)
so that turning it on is one `schtasks /change /enable` and not a build.

Eligibility is deliberately narrow, and every condition is the owner's to grant:

    state:planned  AND  ready:auto  AND  NOT no-auto  AND  gate: is not none

`ready:auto` is never applied by the triager (see .claude/agents/triager.md) —
only the owner grants it. `gate:none` blocks pickup because a sortie with no
named gate has no definition of done.

`--launch` is what actually starts an agent. Without it this prints what it
would have picked and exits 0, which is also how the scheduled task is proved
to run without starting anything.

The launch carries a **scoped** write grant (#122): the coder role's toolset and
nothing else. It removes the interactive prompt, it does not widen what is
permitted — the PreToolUse guard and branch protection stay the real controls,
and a blanket bypass (`--dangerously-skip-permissions`) is never passed.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

try:
    from qops import config as qconfig, ledger
except ModuleNotFoundError:      # not installed: running from a checkout
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from qops import config as qconfig, ledger

BLOCKING_FLAGS = {"no-auto", "blocked"}

# The coder role's tools (.claude/agents/coder.md), verbatim. A sortie branches,
# edits, commits and opens a PR with these; anything wider is #123's question,
# not this launch's grant.
LAUNCH_TOOLS = "Read,Edit,Write,Grep,Glob,Bash"

# Any flag that trades the guard for convenience. Asserted absent, not merely
# omitted - the wrong fix for #122 was one of these.
BLANKET_BYPASS = ("--dangerously-skip-permissions", "--dangerously-bypass-permissions")


def eligible(issue: dict) -> bool:
    labels = {l["name"] for l in issue.get("labels", [])}
    if "state:planned" not in labels or "ready:auto" not in labels:
        return False
    if labels & BLOCKING_FLAGS:
        return False
    return "gate:none" not in labels and any(l.startswith("gate:") for l in labels)


def candidates(root: Path) -> list[dict]:
    out = subprocess.run(
        ["gh", "issue", "list", "--state", "open", "--limit", "100",
         "--json", "number,title,labels,updatedAt"],
        cwd=root, capture_output=True, text=True)
    if out.returncode:
        print(out.stderr.strip(), file=sys.stderr)
        return []
    return [i for i in json.loads(out.stdout or "[]") if eligible(i)]


def repo_root(argv: list[str]) -> Path:
    """`--root <path>`, else the nearest ancestor of cwd holding .qops/config.yml.

    NOT `Path(__file__).parents[1]`: once qops is a pinned dependency that is
    site-packages, not the repo whose backlog is being picked. One scheduled
    task per consuming repo passes `--root`; a hand run in a checkout needs
    nothing (P8.1 leak 3).
    """
    if "--root" in argv:
        return Path(argv[argv.index("--root") + 1]).resolve()
    return qconfig.find_root()


def main(argv: list[str]) -> int:
    root = repo_root(argv)
    cfg = qconfig.load(root)
    picks = candidates(root)
    if not picks:
        print("pickup-loop: nothing eligible (state:planned + ready:auto + a real gate).")
        return 0
    issue = sorted(picks, key=lambda i: i["updatedAt"])[0]
    print(f"pickup-loop: #{issue['number']} {issue['title']}")
    if "--launch" not in argv:
        print("pickup-loop: dry run, not launching. Pass --launch to start an agent.")
        return 0
    # Claim it BEFORE launching. Without this the next hourly fire picks the
    # same issue again - the run does not change the issue, so it stays the
    # least-recently-updated eligible one forever, one session per hour.
    num = str(issue["number"])
    claim = subprocess.run(["gh", "issue", "edit", num,
                            "--remove-label", "state:planned",
                            "--add-label", "state:building"],
                           cwd=root, capture_output=True, text=True)
    if claim.returncode:
        print(f"pickup-loop: could not claim #{num}, not launching: "
              f"{claim.stderr.strip()}", file=sys.stderr)
        return 1
    ledger.append(root, "pickup", {"issue": num})
    rc = subprocess.run(launch_argv(launch_prompt(num)),
                        cwd=root, env=launch_env()).returncode
    if rc or not produced_work(root, num):
        release(root, num, f"exit {rc}" if rc else "no commit and no PR")
        return rc or 1
    return 0


BRANCH_PREFIXES = ("feat", "fix", "docs", "chore", "refactor", "test")


def launch_prompt(num: str) -> str:
    """The instruction half of #128. `automerge-loop` is the assertion half —
    an instruction in a prompt is a preference, not a control (GL-53).

    The branch clause exists because the first unattended run read `type:code`
    off the issue and branched `code/116-...`: the pattern matched, but a label
    is not a commit type. The link line is `Refs`, never `Closes` — a merge is
    not a judgement, so the loop advances the label and the owner closes."""
    return (f"Work sortie #{num} to its stated acceptance criteria. "
            f"Branch first as `<type>/{num}-<slug>` where <type> is a commit "
            f"type — one of {'|'.join(BRANCH_PREFIXES)} — never an issue label. "
            f"Commit, open a PR whose body says `Refs #{num}` (not `Closes`), "
            f"and stop. Do not request a GitHub review — the repo has one "
            f"collaborator and GitHub rejects a self-review request; "
            f"`automerge-loop` labels the issue `state:review` when the owner's "
            f"eyes are needed (#151). Do not merge. "
            f"Run only the tests you touched — the full suite takes ~3.5 "
            f"minutes, longer than a Bash call may run, and `test.yml` runs it "
            f"on every push, which is the gate. Never background a command and "
            f"wait for it: this session ends when your turn does, so a "
            f"backgrounded run never reports and the sortie dies uncommitted.")


def launch_argv(prompt: str) -> list[str]:
    return ["claude", "-p", prompt,
            "--permission-mode", "acceptEdits",
            "--allowedTools", LAUNCH_TOOLS]


def launch_env() -> dict:
    """The launched session is unattended, and says so. `qops guard` reads this
    to refuse a sandbox escape that an interactive owner could still allow."""
    return {**os.environ, "QOPS_UNATTENDED": "1"}


def produced_work(root: Path, num: str) -> bool:
    """A session that exits 0 having built nothing is a failed run, not a done
    sortie. Branch naming is ADR-0019: `<type>/<issue#>-<slug>`.

    An *empty* branch is not work. Both 2026-08-18 sorties (#57, #71) wrote
    their whole change, backgrounded the full test suite, and ended the turn
    waiting for a notification that a `-p` run can never receive - the branch
    existed, pointed at master's tip, and read here as success. The claim was
    never released and neither issue said anything was wrong. Count the
    commits, not the ref."""
    branches = subprocess.run(
        ["git", "branch", "--list", f"*/{num}-*", "--format=%(refname:short)"],
        cwd=root, capture_output=True, text=True).stdout.split()
    base = qconfig.load(root)["default_branch"]
    for branch in branches:
        ahead = subprocess.run(["git", "rev-list", "--count", f"{base}..{branch}"],
                               cwd=root, capture_output=True, text=True).stdout.strip()
        if ahead.isdigit() and int(ahead) > 0:
            return True
    prs = subprocess.run(["gh", "pr", "list", "--search", num, "--json", "number"],
                         cwd=root, capture_output=True, text=True).stdout.strip()
    return bool(json.loads(prs or "[]"))


def release(root: Path, num: str, why: str) -> None:
    """The claim is not a one-way door. A failed run puts the sortie back where
    the next fire can reach it and says why (CLAUDE.md, GL-46)."""
    subprocess.run(["gh", "issue", "edit", num,
                    "--remove-label", "state:building",
                    "--add-label", "state:planned"],
                   cwd=root, capture_output=True, text=True)
    subprocess.run(["gh", "issue", "comment", num, "--body",
                    f"pickup-loop: unattended run produced nothing ({why}). "
                    f"Claim released, back to `state:planned`."],
                   cwd=root, capture_output=True, text=True)
    ledger.append(root, "pickup_release", {"issue": num, "why": why})
    print(f"pickup-loop: released #{num} ({why}).", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
