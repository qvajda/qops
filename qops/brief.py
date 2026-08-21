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
from datetime import datetime, timezone
from importlib import metadata as _metadata
from pathlib import Path

from . import ledger

TOKEN_CAP = 400
BYTES_PER_TOKEN = 4          # PRD §2.1's own divisor, so the numbers compare


def qops_version() -> str:
    """The installed substrate's version — what `pip show qops` would report,
    read the same way, so a session can tell a stale pin from the brief it
    already reads (v0.1.1 shipped mislabelled; nothing surfaced that fact)."""
    try:
        return _metadata.version("qops")
    except _metadata.PackageNotFoundError:
        return "dev"


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


def picker_silence(root: Path, cfg: dict) -> str | None:
    """One line when `pickup-loop` has not completed a run in a while.

    Read as state, never as a reaction to an event. The failures this exists
    for are the ones no in-process handler can report — the task returned 1 at
    09:00, 10:00, 11:00 and 12:00 on 2026-08-21 because the script raised at
    import, and every silence the picker had already fixed (`candidates()`
    returning None, #48, #49, #50) assumes it got far enough to print (#76).

    A repo whose loop has never run says nothing: it ships disabled, and a line
    the reader learns to skip is worse than no line (#167). One report however
    many runs died, because a state read cannot count them and does not need to.
    """
    runs = [r for r in ledger.read(root) if r.get("event") == "pickup_ran"]
    if not runs:
        return None
    try:
        last = datetime.fromisoformat(runs[-1]["ts"])
    except (KeyError, TypeError, ValueError):
        return None
    hours = (datetime.now(timezone.utc) - last).total_seconds() / 3600
    if hours < cfg.get("pickup_max_silence_hours", 3):
        return None
    return (f"**pickup-loop: no completed run in {hours:.0f}h** "
            f"(last {runs[-1]['ts']}). A run that dies before it prints says "
            f"nothing else — check the task's last result.")


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
            "labels": _labels(root, issue), "pointers": _pointers(root),
            "picker": picker_silence(root, cfg)}


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
    # Above the fold, with the dirty tree: a dead loop is the same class of
    # thing — the session's assumptions are wrong before it starts.
    if state.get("picker"):
        lines.append(state["picker"])
    if state.get("branch") in cfg.get("protected_branches", []):
        lines.append(f"On `{state['branch']}` (protected). Branch before committing.")
    if state.get("worktrees", 0) >= cfg.get("max_worktrees", 99):
        lines.append(f"{state['worktrees']} worktrees live — at the cap.")

    head = f"qops {qops_version()} | `{state.get('branch','?')}`"
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
