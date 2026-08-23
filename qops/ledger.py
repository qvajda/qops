"""`.qops/ledger.jsonl` — append-only session state, and the resume file built
from it. This is what retired the Remember plugin (ADR-0014): one writer.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

MAX_RESUME_EVENTS = 40


def _dir(root: Path) -> Path:
    d = Path(root) / ".qops"
    d.mkdir(exist_ok=True)
    return d


def append(root: Path, event: str, data: dict | None = None) -> dict:
    rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "event": event}
    rec.update(data or {})
    with (_dir(root) / "ledger.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def read(root: Path, limit: int | None = None) -> list[dict]:
    p = Path(root) / ".qops" / "ledger.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out[-limit:] if limit else out


def last_session_branch(root: Path, session_id: str) -> str | None:
    """The branch this `session_id` last recorded - `session_start`, `stop`
    and the guard's own `checkout` events all carry one (#130)."""
    branch = None
    for rec in read(root):
        if rec.get("session_id") == session_id and rec.get("branch"):
            branch = rec["branch"]
    return branch


def _payload() -> dict:
    """Hook payload on stdin, if the caller is a hook."""
    if sys.stdin is None or sys.stdin.isatty():
        return {}
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


def write_resume(root: Path) -> str:
    """One page: what the last session was doing, and where it stopped."""
    events = read(root, MAX_RESUME_EVENTS)
    branch = ""
    for rec in reversed(events):
        if rec.get("branch"):
            branch = rec["branch"]
            break
    lines = [f"# resume — {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC", ""]
    if branch:
        lines.append(f"Branch: `{branch}`")
    notes = [r for r in events if r.get("event") in ("note", "session_start", "stop")]
    if notes:
        lines += ["", "## last events", ""]
        for rec in notes[-12:]:
            detail = rec.get("text") or rec.get("issue") or rec.get("branch") or ""
            lines.append(f"- `{rec['ts']}` {rec['event']} {detail}".rstrip())
    text = "\n".join(lines) + "\n"
    (_dir(root) / "resume.md").write_text(text, encoding="utf-8")
    return text


def main(argv: list[str], root: Path, cfg: dict) -> int:
    """`qops ledger [event] [k=v ...]` — appends; with no event, prints the tail."""
    payload = _payload()
    args = [a for a in argv if not a.startswith("-")]
    if not args:
        for rec in read(root, 20):
            print(json.dumps(rec, ensure_ascii=False))
        return 0
    event = args[0]
    data = dict(kv.split("=", 1) for kv in args[1:] if "=" in kv)
    if payload:
        for key in ("session_id", "cwd", "permission_mode"):
            if payload.get(key):
                data.setdefault(key, payload[key])
    if "branch" not in data:
        try:
            data["branch"] = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=root,
                capture_output=True, text=True, timeout=10).stdout.strip()
        except Exception:
            pass
    append(root, event, data)
    return 0


def resume_main(argv: list[str], root: Path, cfg: dict) -> int:
    """`qops resume` prints the file; `qops resume --write` regenerates it."""
    p = Path(root) / ".qops" / "resume.md"
    if "--write" in argv or not p.exists():
        text = write_resume(root)
    else:
        text = p.read_text(encoding="utf-8")
    if "--quiet" not in argv:
        sys.stdout.write(text)
    return 0
