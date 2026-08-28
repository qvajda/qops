"""`qops pending` — what is waiting on the owner, and what the loop takes next
(#131).

The facts were already computable — `install.eligible()`, `install.plannable()`,
`install.decomposable()`, `install.struck_out()`, `install.unwritable()` and
`install.issue_invariants()` each answer part of it — but nothing assembled
them into one answer, so a session reconstructed it by hand instead. This
assembles them, and nothing else: no label, no comment, no ledger write. A
status verb that changes state cannot be run freely, and being runnable freely
is the point.

One `gh issue list` call. The "waiting on you" section and the three queues
are filters over that one list, the way `scripts/qops_pickup.py` already
splits its build/plan/decompose queues (#82) — not four round trips. The
`gh issue list` result is also handed to `install.doctor()` so its own
`open_issues()` fetch is skipped, keeping the one-call promise whole.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from . import install, ledger, reconcile

LIMIT = 200


def backlog(repo: str) -> list[dict] | None:
    """Every open row, or `None` when the tracker could not be read.

    The distinction matters the same way it does in `scripts/qops_pickup.py`:
    an empty queue and an unreadable tracker must not print alike.
    """
    try:
        p = subprocess.run(
            ["gh", "issue", "list", "--repo", repo, "--state", "open",
             "--limit", str(LIMIT), "--json",
             "number,title,labels,updatedAt,body"],
            capture_output=True, text=True, encoding="utf-8", timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"qops pending: could not read the backlog on {repo} ({exc})",
              file=sys.stderr)
        return None
    if p.returncode != 0:
        print(f"qops pending: gh exited {p.returncode}: {p.stderr.strip()}",
              file=sys.stderr)
        return None
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError as exc:
        print(f"qops pending: unreadable gh output ({exc})", file=sys.stderr)
        return None


def _labels(row: dict) -> set[str]:
    return {l["name"] for l in row.get("labels", [])}


def _no_auto_reason(labels: set[str]) -> str:
    """Which act `no-auto` is withholding — the state names the queue it
    would otherwise sit in."""
    if "type:epic" in labels:
        return "decomposition"
    if "state:triage" in labels:
        return "planning"
    if "state:planned" in labels:
        return "the build"
    return "advancing this row"


def is_claimed(labels: set[str]) -> bool:
    """A live claim (ADR-0031 §3): an owner session already has this row, so
    it is *with* him, not waiting on him. `state:building` + `no-auto`
    together mark a build/fix session; `state:review` alone marks a review
    one — the same pair the alerter (#120) fires on, taught here so claiming
    a row does not create a fresh edge into its own trigger set.

    `gate:taste` at `state:review` is the exception (ADR-0036): the sortie
    that built it already stopped (CLAUDE.md — an unattended sortie opens a
    PR and stops), so no live session holds it the way a `gate:machine` row's
    reviewing session does. That row is still waiting on him, not with him —
    `waiting_on_owner()` is where the alert fires."""
    return (("state:building" in labels and "no-auto" in labels)
            or ("state:review" in labels and "gate:taste" not in labels))


def _session_for_issue(root: Path, num: int) -> str | None:
    """The most recent session whose branch names this issue
    (`<type>/<num>-slug>`, CLAUDE.md) — read from `session_start`, the record
    every session already writes, no new ledger flag needed."""
    needle = f"/{num}-"
    session = None
    for rec in ledger.read(root):
        if rec.get("event") == "session_start" and needle in (rec.get("branch") or ""):
            session = rec.get("session_id")
    return session


def claimed_rows(root: Path, rows: list[dict]) -> list[tuple[dict, str | None]]:
    """Rows with a live claim, each with the session holding it if known."""
    return [(row, _session_for_issue(root, row["number"])) for row in rows
            if is_claimed(_labels(row))]


def waiting_on_owner(root: Path, rows: list[dict]) -> list[str]:
    """Each row that needs the owner, with the action — not just the state."""
    out = []
    for row in rows:
        num, title = row["number"], row.get("title", "")
        labels = _labels(row)
        if is_claimed(labels):
            continue
        if "priority:parked" in labels:
            continue
        if "state:review" in labels:
            out.append(f"#{num} {title} — state:review: the loop asked for eyes")
        if "no-auto" in labels:
            out.append(f"#{num} {title} — no-auto: withholds {_no_auto_reason(labels)}")
        if (reconcile.DONE in labels and "gate:machine" in labels
                and "no-auto" not in labels):
            out.append(f"#{num} {title} — gate:machine, state:done, still open: "
                       f"its PR merged and the row cannot close itself")
        n = install.strikes(root, str(num), labels)
        if n >= install.STRIKES:
            out.append(f"#{num} {title} — struck out after {n} failed runs (#49)")
    return out


def _queue(root: Path, rows: list[dict], pred) -> list[dict]:
    """Rows `pred` accepts, least-recently-updated first, struck-out rows
    skipped — the same order `first_launchable()`/`first_plannable()` pick in.
    """
    picks = [r for r in rows if pred(r)]
    return [r for r in sorted(picks, key=lambda r: r["updatedAt"])
            if not install.struck_out(root, str(r["number"]), _labels(r))]


def queues(root: Path, rows: list[dict]) -> dict[str, list[dict]]:
    return {
        "build": _queue(root, rows, install.eligible),
        "plan": _queue(root, rows, install.plannable),
        # ponytail: skips the "already has sub-issues" dedup
        # `first_decomposable()` applies via a live `gh api` call per
        # candidate — add if a decomposed epic starts reappearing here.
        "decompose": _queue(root, rows, lambda r: install.decomposable(root, r)),
    }


def render(root: Path, cfg: dict) -> tuple[list[str], int]:
    repo = cfg.get("repo", "")
    if not repo:
        return ["qops pending: .qops/config.yml names no `repo`"], 1
    rows = backlog(repo)
    if rows is None:
        return ["qops pending: the tracker could not be read — queue state "
                "is UNKNOWN, not empty"], 1

    lines = ["## Waiting on you"]
    owner_rows = waiting_on_owner(root, rows)
    lines += owner_rows if owner_rows else ["  nothing"]

    parked = [r for r in rows if "priority:parked" in _labels(r)]
    if parked:
        lines.append(f"## Parked ({len(parked)})")
        lines.append("  quiet, not invisible — `gh issue list --label priority:parked`")

    claims = claimed_rows(root, rows)
    if claims:
        lines.append("## With you (already claimed)")
        for row, session in claims:
            who = f"session {session}" if session else "an open session"
            lines.append(f"  #{row['number']} {row.get('title', '')} — with {who}")

    problems = install.doctor(root, cfg, issues=rows)
    if problems:
        lines.append("## qops doctor")
        lines += [f"  {p}" for p in problems]

    lines.append("## What the loop takes next")
    q = queues(root, rows)
    for name in ("build", "plan", "decompose"):
        picks = q[name]
        if picks:
            lines.append(f"  {name}: " + ", ".join(
                f"#{r['number']} {r.get('title', '')}" for r in picks))
        else:
            lines.append(f"  {name}: empty")
    return lines, 0


def main(argv: list[str], root: Path, cfg: dict) -> int:
    lines, rc = render(root, cfg)
    for line in lines:
        print(line, file=sys.stderr if rc else sys.stdout)
    return rc
