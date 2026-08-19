"""`qops metrics` — S1/S2/S4/S9/S10, plus `--state` for PRD §1.2.

S1 adopts the Phase −1 findings §1 method **verbatim**, because a baseline
measured one way and re-measured another is not a baseline and Phase 6 compares
against it:

  session          one *.jsonl transcript
  main thread      records with `isSidechain` falsy — subagent traffic excluded
  read             a tool_use block named Read or NotebookRead
  productive       Write/Edit/MultiEdit/NotebookEdit, or a Bash/PowerShell
                   command matching git commit | pytest | -m pytest |
                   -m unittest | npm test
  S1               reads strictly before the first productive call
  >200-line read   a read whose result carries more than 200 newlines, before
                   the first productive call

Bash reads (cat, sed, head) are deliberately NOT counted, so S1 is a floor.
"""

import json
import os
import re
import statistics
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from . import ledger as ledgermod

READ_TOOLS = {"Read", "NotebookRead"}
EDIT_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
SHELL_TOOLS = {"Bash", "PowerShell"}
PRODUCTIVE_CMD = re.compile(r"git commit|pytest|-m pytest|-m unittest|npm test")
BIG_READ_LINES = 200
KICKOFF_DOCS = re.compile(r"docs/.*(kickoff|session-prompt|launch|brief|runbook)")


# --- S1 --------------------------------------------------------------------

def _blocks(rec: dict):
    msg = rec.get("message") or {}
    content = msg.get("content")
    if isinstance(content, list):
        return content
    return []


def _result_lines(block: dict) -> int:
    content = block.get("content")
    if isinstance(content, str):
        return content.count("\n")
    if isinstance(content, dict):
        text = content.get("file", {}).get("content", "") if isinstance(
            content.get("file"), dict) else content.get("text", "")
        return str(text).count("\n")
    if isinstance(content, list):
        return sum(str(b.get("text", "")).count("\n") for b in content
                   if isinstance(b, dict))
    return 0


def s1_for_transcript(path: Path) -> dict:
    reads = 0
    productive = False
    big_read = False
    pending_read = False
    for line in Path(path).read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("isSidechain"):
            continue                       # subagent traffic is not the owner's cost
        for block in _blocks(rec):
            btype = block.get("type")
            if btype == "tool_result" and pending_read and not productive:
                if _result_lines(block) > BIG_READ_LINES:
                    big_read = True
                pending_read = False
            if btype != "tool_use":
                continue
            name = block.get("name")
            if name in EDIT_TOOLS:
                productive = True
            elif name in SHELL_TOOLS:
                if PRODUCTIVE_CMD.search((block.get("input") or {}).get("command", "")):
                    productive = True
            elif name in READ_TOOLS and not productive:
                reads += 1
                pending_read = True
        if productive:
            break
    return {"reads": reads, "productive": productive, "big_read": big_read,
            "transcript": str(path)}


def _first_session_ts(path: Path) -> str | None:
    """Timestamp of the transcript's first user/assistant record — floors the
    session so a transcript sorts by when work started, not by mtime."""
    for line in Path(path).read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("type") in ("user", "assistant") and rec.get("timestamp"):
            return rec["timestamp"]
    return None


def _s1_summary(rows: list[dict]) -> dict:
    scored = [r for r in rows if r["productive"]]
    counts = [r["reads"] for r in scored]
    return {
        "sessions": len(rows),
        "scored": len(scored),
        "no_productive_call": len(rows) - len(scored),
        "median_reads": statistics.median(counts) if counts else None,
        "mean_reads": round(statistics.mean(counts), 2) if counts else None,
        "pct_with_big_read": round(100 * sum(r["big_read"] for r in scored)
                                   / len(scored)) if scored else None,
    }


def _transcript_dirs(root: Path) -> list[Path]:
    home = Path(os.path.expanduser("~")) / ".claude" / "projects"
    if not home.exists():
        return []
    slug = str(Path(root).resolve()).replace(":", "").replace("\\", "-").replace("/", "-")
    slug = slug.replace("--", "-").lstrip("-")
    return [d for d in home.iterdir()
            if d.is_dir() and Path(root).name.replace("_", "-") in d.name.lower()]


def s1(root: Path, since: str = "2026-07-14", until: str | None = None) -> dict:
    by_dir: dict[str, list[dict]] = {}
    for d in _transcript_dirs(root):
        rows = []
        for t in d.glob("*.jsonl"):
            ts = _first_session_ts(t)
            date = ts[:10] if ts else None
            if date is None:
                continue
            if since and date < since:
                continue
            if until and date > until:
                continue
            try:
                rows.append(s1_for_transcript(t))
            except OSError:
                continue
        by_dir[d.name] = rows
    all_rows = [r for rows in by_dir.values() for r in rows]
    return {
        "since": since,
        "until": until,
        **_s1_summary(all_rows),
        "by_dir": {name: _s1_summary(rows) for name, rows in by_dir.items()},
    }


# --- S2 / S4 / S9 / S10 ----------------------------------------------------

def s2(root: Path, since: str = "2026-07-14") -> int:
    out = subprocess.run(
        ["git", "log", f"--since={since}", "--diff-filter=A", "--name-only",
         "--pretty=format:"], cwd=root, capture_output=True, text=True).stdout
    return len({p for p in out.split() if KICKOFF_DOCS.search(p)})


def _gh(root: Path, *args: str):
    try:
        out = subprocess.run(["gh", *args], cwd=root, capture_output=True,
                             text=True, timeout=60)
        return json.loads(out.stdout) if out.returncode == 0 and out.stdout else None
    except Exception:
        return None


def _gate_green(rollup: list[dict]) -> bool:
    # Every applicable gate, not two named ones: naming `gate` and `test`
    # let a red guard.yml (tripwires, doc links) score as clean.
    conclusions = [c.get("conclusion") for c in rollup]
    return (bool(rollup)
            and not any(c in ("FAILURE", "TIMED_OUT", "CANCELLED",
                              "ACTION_REQUIRED", "STARTUP_FAILURE")
                        for c in conclusions)
            and "SUCCESS" in conclusions)


def s4(root: Path) -> dict:
    """PRs where review was requested before the gate check went green."""
    prs = _gh(root, "pr", "list", "--state", "all", "--limit", "50",
              "--json", "number,reviewRequests,statusCheckRollup,createdAt")
    if prs is None:
        return {"available": False}
    bad = [pr["number"] for pr in prs
           if pr.get("reviewRequests") and not _gate_green(pr.get("statusCheckRollup") or [])]
    return {"available": True, "requests_without_green_gate": bad,
            "total": len(prs)}


def s9(root: Path) -> dict:
    """state:planned -> first commit on the matching branch."""
    issues = _gh(root, "issue", "list", "--label", "state:building", "--limit", "20",
                 "--json", "number,title,updatedAt")
    if issues is None:
        return {"available": False}
    return {"available": True, "in_flight": [i["number"] for i in issues]}


def s10(root: Path, cfg: dict) -> dict:
    """Hot path: what enters context without being asked for."""
    claude_md = Path(root) / "CLAUDE.md"
    lines = len(claude_md.read_text(encoding="utf-8").splitlines()) if claude_md.exists() else 0
    from . import brief as briefmod
    brief_tokens = briefmod.tokens(briefmod.render(root, cfg))
    return {"claude_md_lines": lines, "claude_md_cap": cfg["claude_md_max_lines"],
            "claude_md_tokens": -(-claude_md.stat().st_size // 4) if claude_md.exists() else 0,
            "brief_tokens": brief_tokens,
            "within_cap": lines <= cfg["claude_md_max_lines"]}


# --- S11 / S12 / S13 — usage, not ROI (issue #115) --------------------------
#
# Payback-weeks is retired as the headline: it measured a projected saving,
# never whether the thing got used. These three measure use.

BRANCH_RE = re.compile(r"^(feat|fix|docs|chore|refactor|test)/\d+-")


def _minutes(start_ts: str, end_ts: str) -> float:
    try:
        start = datetime.fromisoformat(start_ts.replace("Z", "+00:00"))
        end = datetime.fromisoformat(end_ts.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return max(0.0, (end - start).total_seconds() / 60)


def owner_minutes(events: list[dict], since: str | None = None) -> dict:
    """Sums each `session_start` -> next `stop`/`session_end` pair. This is a
    ceiling, not a clock on the owner alone: a `gate:machine` session that runs
    unattended still counts, so a low number needs S13 alongside it to mean
    "unattended", not just "short"."""
    total = 0.0
    sessions = 0
    pending = None
    for rec in events:
        if since and rec.get("ts", "") < since:
            continue
        event = rec.get("event")
        if event == "session_start":
            pending = rec.get("ts")
        elif event in ("stop", "session_end") and pending:
            total += _minutes(pending, rec["ts"])
            sessions += 1
            pending = None
    return {"total_minutes": round(total, 1), "sessions": sessions}


def s11(root: Path, since: str | None = None) -> dict:
    """S11 — owner-minutes per merged PR."""
    prs = _gh(root, "pr", "list", "--state", "merged", "--limit", "50",
              "--json", "number,mergedAt")
    if prs is None:
        return {"available": False}
    if since:
        prs = [pr for pr in prs if pr.get("mergedAt", "")[:10] >= since]
    minutes = owner_minutes(ledgermod.read(root), since=since)
    merged = len(prs)
    return {"available": True, "merged_prs": merged, **minutes,
            "per_merged_pr": round(minutes["total_minutes"] / merged, 1) if merged else None}


def full_flow_share(prs: list[dict], branch_gone) -> dict:
    """Share of merged PRs whose branch matches `<type>/<issue>-<slug>`, whose
    gate ran green, and whose branch is gone post-merge — the direct read on
    what ADR-0019's hook and auto-delete enforce end to end."""
    full = [pr for pr in prs
            if BRANCH_RE.match(pr.get("headRefName", ""))
            and _gate_green(pr.get("statusCheckRollup") or [])
            and branch_gone(pr["headRefName"])]
    total = len(prs)
    return {"total": total, "full_flow": len(full),
            "pct": round(100 * len(full) / total) if total else None}


def _remote_branch_gone(root: Path, branch: str) -> bool:
    out = subprocess.run(["git", "ls-remote", "--heads", "origin", branch],
                         cwd=root, capture_output=True, text=True, timeout=15)
    return out.returncode == 0 and not out.stdout.strip()


def s12(root: Path, since: str | None = None) -> dict:
    """S12 — share of merged PRs that ran the full flow."""
    prs = _gh(root, "pr", "list", "--state", "merged", "--limit", "50",
              "--json", "number,headRefName,mergedAt,statusCheckRollup")
    if prs is None:
        return {"available": False}
    if since:
        prs = [pr for pr in prs if pr.get("mergedAt", "")[:10] >= since]
    return {"available": True,
            **full_flow_share(prs, lambda b: _remote_branch_gone(root, b))}


def owner_interruptions(events: list[dict], since: str | None = None) -> dict:
    """Extra `session_start`s on the same branch, beyond the first, are the
    owner (or a resumed agent) coming back mid-sortie. ADR-0017 wants this at
    zero on `gate:machine`: plan, build, PR, CI, one owner touch at review."""
    by_branch: dict[str, int] = {}
    for rec in events:
        if since and rec.get("ts", "") < since:
            continue
        if rec.get("event") == "session_start" and rec.get("branch"):
            by_branch[rec["branch"]] = by_branch.get(rec["branch"], 0) + 1
    sorties = len(by_branch)
    interruptions = sum(n - 1 for n in by_branch.values())
    return {"sorties": sorties, "interruptions": interruptions,
            "per_sortie": round(interruptions / sorties, 2) if sorties else None}


def s13(root: Path, since: str | None = None) -> dict:
    """S13 — owner interruptions per sortie."""
    return owner_interruptions(ledgermod.read(root), since=since)


# --- --state: PRD §1.2, the table becomes generated ------------------------

def _git(root: Path, *args: str) -> list[str]:
    """git with no shell between us and it. ADR-0009 says nothing may assume
    POSIX, and `bash` on the cron host is the WSL launcher: it printed
    "Windows Subsystem for Linux has no installed distributions." and this
    function recorded that as the measurement, nine times, in a table that
    looked measured. A non-zero exit now raises instead of being captured."""
    p = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} exited {p.returncode}: "
                           f"{p.stderr.strip()}")
    return [line for line in p.stdout.splitlines() if line.strip()]


def _unmerged(root: Path, cfg: dict) -> int | str:
    """Branches not merged into the default branch.

    A CI checkout is shallow and has no local `master`, so the ref genuinely
    does not exist there. That is a property of the checkout, not a broken
    probe: rendering FAILED would fail every CI run and teach the reader to
    ignore the marker, which is the GL-53 shape. `n/a` says so instead, and it
    is not a number, so nothing mistakes it for a measurement.
    """
    branch = cfg.get("default_branch", "master")
    for ref in (branch, f"origin/{branch}"):
        try:
            return len(_git(root, "branch", "--no-merged", ref))
        except RuntimeError:
            continue
    return "n/a"


def _lines(paths) -> int:
    return sum(len(p.read_text(encoding="utf-8", errors="ignore").splitlines())
               for p in paths)


# label, the equivalent shell command (documentation for the reader), probe.
# The probe is Python: most of these were a glob and a len, and the git ones
# call git directly.
_STATE_ROWS = [
    ("Plan/doc sprawl", "ls docs/*.md | wc -l",
     lambda root, cfg: len(list((root / "docs").glob("*.md")))),
    ("Plan/doc lines", "cat docs/*.md | wc -l",
     lambda root, cfg: _lines((root / "docs").glob("*.md"))),
    ("Per-session fixed cost", "wc -l CLAUDE.md",
     lambda root, cfg: _lines([root / "CLAUDE.md"])),
    ("Branches", "git branch | wc -l",
     lambda root, cfg: len(_git(root, "branch"))),
    ("Worktrees", "git worktree list | wc -l",
     lambda root, cfg: len(_git(root, "worktree", "list"))),
    ("Dirty paths", "git status --porcelain | wc -l",
     lambda root, cfg: len(_git(root, "status", "--porcelain"))),
    ("Unmerged branches", "git branch --no-merged master | wc -l", lambda root, cfg: _unmerged(root, cfg)),
    ("Test files", "ls tests/test_*.py | wc -l",
     lambda root, cfg: len(list((root / "tests").glob("test_*.py")))),
    ("Workflows", "ls .github/workflows/*.yml | wc -l",
     lambda root, cfg: len(list((root / ".github" / "workflows").glob("*.yml")))),
]


def state_report(root: Path, cfg: dict) -> tuple[str, list[str]]:
    """Returns the rendered table and the list of probes that failed.

    A failed probe renders as `FAILED` and is reported to the caller, which
    exits non-zero. It never renders as whatever text came back: a report whose
    rows are error strings still carries a `measured-at` commit, so it looks
    measured, which is worse than an empty one.
    """
    root = Path(root)
    head = _git(root, "rev-parse", "--short", "HEAD")[0]
    lines = ["# qops state report", "",
             "Generated by `qops metrics --state`. PRD v3 §1 is a pointer to this "
             "file, not a cache of it — a number without a `measured-at` is the "
             "defect.", "",
             f"measured-at: `{head}`", "", "| Symptom | Value | Command |",
             "|---|---|---|"]
    failures: list[str] = []
    for label, cmd, probe in _STATE_ROWS:
        try:
            raw = probe(root, cfg)
            value: object = raw if isinstance(raw, str) else int(raw)
        except Exception as exc:  # noqa: BLE001 - recorded, then fails the run
            value, _ = "FAILED", failures.append(f"{label}: {exc}")
        lines.append(f"| {label} | {value} | `{cmd}` |")
    text = chr(10).join(lines) + chr(10)
    (root / ".qops" / "state-report.md").write_text(text, encoding="utf-8")
    return text, failures


_FLAGS_WITH_VALUE = {"--since", "--until"}
_FLAGS = {"--state", "--json"} | _FLAGS_WITH_VALUE


def main(argv: list[str], root: Path, cfg: dict) -> int:
    unknown = [a for a in argv if a.startswith("--") and a not in _FLAGS]
    if unknown:
        sys.stderr.write(f"qops metrics: unrecognised flag {unknown[0]}\n")
        return 1
    if "--state" in argv:
        text, failures = state_report(root, cfg)
        sys.stdout.write(text)
        for f in failures:
            print(f"qops metrics --state: probe failed — {f}", file=sys.stderr)
        return 1 if failures else 0
    since = argv[argv.index("--since") + 1] if "--since" in argv else "2026-07-14"
    until = argv[argv.index("--until") + 1] if "--until" in argv else None
    report = {"S1_resume_cost": s1(root, since=since, until=until),
              "S2_kickoff_docs": s2(root, since=since),
              "S4_review_before_gate": s4(root), "S9_planned_to_working": s9(root),
              "S10_hot_path": s10(root, cfg),
              "S11_owner_minutes_per_merged_pr": s11(root, since=since),
              "S12_full_flow_share": s12(root, since=since),
              "S13_owner_interruptions_per_sortie": s13(root, since=since)}
    if "--json" in argv:
        print(json.dumps(report, indent=2))
        return 0
    for key, value in report.items():
        print(f"{key}: {json.dumps(value)}")
    return 0
