"""`qops brief` — what a session is given at SessionStart instead of reading
its way in (CONTEXT.md: *Brief*).

Two contracts, both asserted in tests/test_qops.py:
  1. never more than 400 tokens — it is hot path, and hot path is what S10
     measures;
  2. it leads with a dirty-tree violation rather than papering over it.
"""

import re
import subprocess
import sys
from pathlib import Path

TOKEN_CAP = 400
BYTES_PER_TOKEN = 4          # PRD §2.1's own divisor, so the numbers compare


def tokens(text: str) -> int:
    return -(-len(text) // BYTES_PER_TOKEN)


def _git(root: Path, *args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=root, capture_output=True,
                              text=True, timeout=10).stdout.strip()
    except Exception:
        return ""


def _porcelain(root: Path) -> list[str]:
    """Paths from `git status --porcelain`. NOT via _git: its .strip() eats the
    leading space of an unstaged first line and takes the path's first char
    with it (`.qops/config.yml` -> `qops/config.yml`)."""
    try:
        out = subprocess.run(["git", "status", "--porcelain"], cwd=root,
                             capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return []
    return [line[3:] for line in out.splitlines() if len(line) > 3]


def issue_from_branch(branch: str):
    """The active sortie, from `<type>/<issue#>-<slug>` (ADR-0019).

    The ledger cannot answer this: only `qops close` ever writes an `issue`, so
    reading it back gives the last CLOSED sortie. `no-issue/` is the recorded
    escape and deliberately has no number.
    """
    m = re.match(r"[a-z]+/(\d+)-", branch or "")
    return int(m.group(1)) if m else None


def routing(labels: list[str]) -> str:
    """The ADR-0017 verdict for one issue, from its labels alone.

    Pure, so it is testable without a network and cheap enough for hot path.
    """
    if "type:epic" in labels:
        return "Mission - interview before any issue is written."
    gate = next((l.split(":", 1)[1] for l in labels
                 if l.startswith("gate:")), None)
    if gate == "machine":
        # ready:auto is the stronger claim - an unattended pickup - and is
        # legal only on a gated issue. gate:none blocks it (finding B7).
        auto = " `ready:auto`: proceed unattended." if "ready:auto" in labels else ""
        return "gate:machine - no owner contact before review." + auto
    if gate == "taste":
        return ("gate:taste - the owner sees the artefact, not the diff; "
                "machine gate green first.")
    return "Unrouted - no `gate:` label, so not eligible for `ready:auto`."


def _labels(root: Path, issue) -> list[str]:
    """Labels for the active issue. Any failure is no labels: `gh` may be
    absent, offline or slow, and a brief that fails is worse than one with no
    verdict — it runs at SessionStart, before anything else."""
    if not issue:
        return []
    try:
        out = subprocess.run(
            ["gh", "issue", "view", str(issue), "--json", "labels",
             "-q", ".labels[].name"],
            cwd=root, capture_output=True, text=True, timeout=5)
    except Exception:
        return []
    return out.stdout.split() if out.returncode == 0 else []


def collect(root: Path, cfg: dict) -> dict:
    dirty = _porcelain(root)
    worktrees = max(len(_git(root, "worktree", "list").splitlines()) - 1, 0)
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    ahead = _git(root, "rev-list", "--count", "@{u}..HEAD") or "0"
    issue = issue_from_branch(branch)
    resume = ""
    p = Path(root) / ".qops" / "resume.md"
    if p.exists():
        body = [l for l in p.read_text(encoding="utf-8").splitlines()
                if l.startswith("- ")]
        resume = "\n".join(body[-3:])
    return {"branch": branch, "dirty": dirty, "worktrees": worktrees,
            "ahead": int(ahead or 0), "issue": issue, "resume": resume,
            "labels": _labels(root, issue), "pointers": _pointers(root)}


# Where a session looks things up, if the repo has them. Order is the order
# they are read in.
_POINTERS = (("CONTEXT.md", "Vocabulary: CONTEXT.md"),
             ("docs/adr", "decisions: docs/adr/"),
             ("CLAUDE.md", "constraints: CLAUDE.md"))


def _pointers(root: Path) -> list[str]:
    return [text for path, text in _POINTERS if (Path(root) / path).exists()]


def render_from(state: dict, cfg: dict) -> str:
    lines: list[str] = []
    dirty = state.get("dirty") or []
    if dirty:
        shown = ", ".join(dirty[:6]) + (" ..." if len(dirty) > 6 else "")
        lines.append(f"**Dirty tree - {len(dirty)} path(s): {shown}.** "
                     f"Commit, stash or ignore before starting new work.")
    if state.get("branch") in cfg.get("protected_branches", []):
        lines.append(f"On `{state['branch']}` (protected). Branch before committing.")
    if state.get("worktrees", 0) >= cfg.get("max_worktrees", 99):
        lines.append(f"{state['worktrees']} worktrees live — at the cap.")

    head = f"qops | `{state.get('branch','?')}`"
    if state.get("ahead"):
        head += f" | {state['ahead']} unpushed"
    if state.get("issue"):
        head += f" | sortie #{state['issue']}"
    lines.append(head)
    if state.get("labels"):
        lines.append(routing(state["labels"]))
    # Which tracker, every time. From Phase 8 there are two, and a session
    # reading the wrong one is the dominant new failure mode (PRD §Risks).
    repo = cfg.get("repo") or "no `repo:` in .qops/config.yml"
    line = f"Issues are the source of truth: `gh issue list` on **{repo}**."
    # Only point at what is there. A fixed list of filenames is a dangling
    # pointer in the first repo that does not happen to have one of them, and
    # it sits in the hot path of every session.
    if state.get("pointers"):
        line += " " + " | ".join(state["pointers"]) + "."
    lines.append(line)
    if state.get("resume"):
        lines.append("Last session:\n" + state["resume"])

    text = "\n\n".join(lines) + "\n"
    cap = TOKEN_CAP * BYTES_PER_TOKEN
    if len(text) > cap:
        text = text[: cap - 4].rstrip() + " ...\n"
    return text


def render(root: Path, cfg: dict) -> str:
    return render_from(collect(root, cfg), cfg)


def main(argv: list[str], root: Path, cfg: dict) -> int:
    sys.stdout.write(render(root, cfg))
    return 0
