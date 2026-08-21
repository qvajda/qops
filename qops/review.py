"""`qops review` — the reviewer's verdict, ADR-0028 §4 and #80.

The required checks on `master` are `test`, `gate`, `tripwires` and
`doc-links`. All four are mechanical, so nothing between a filing and `master`
reads for *meaning*. ADR-0028 accepted an unread chain with this as the
compensating control, and then it was never built; ADR-0029 §1 makes it the
prerequisite for unattended planning.

**The fail-open path is the load-bearing one.** This is a language model wired
into a check that will become required under `enforce_admins: true`, where
GitHub gives the owner no override. The asymmetry:

  a wrong fail-closed   one bad diff merges - reversible, on the tracker
  a wrong fail-open     every PR in the repo freezes, and the only exit is the
                        owner editing protection settings by hand, because
                        `.claude/settings.json` denies `gh api -X` against them

So an outage, a missing credential, an unparseable answer and a PR with no row
are all **green, and every one of them says so**. A silent fail-open is worse
than no check at all: it is indistinguishable from a real pass, so it reads as
a reader that read.

Only a verdict is a rejection.

No new dependency: one POST through `urllib`, so `INSTALL_DEPS` is unchanged
and every rendered job that installs qops does not grow an SDK for this.
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from . import reconcile

API = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-5"
MAX_DIFF = 60_000        # bytes; a diff past this is summarised by truncation

_PROMPT = """You are reviewing one pull request against the issue it implements.

Judge ONE question: does this diff serve the outcome the row states? Not
whether the code is good, not whether you would have written it differently,
not whether more could be done. Only whether it serves the stated outcome.

Reply with exactly one first line, then a short reason:

VERDICT: serves
VERDICT: does-not-serve

Use `does-not-serve` only when you can name what the row asked for that the
diff does not do. Uncertainty is `serves` — a reviewer that blocks when unsure
is a reviewer that blocks everything.

--- THE ROW ---
{row}

--- THE DIFF ---
{diff}
"""


def verdict(text: str | None) -> str | None:
    """`serves`, `does-not-serve`, or None for anything else.

    Strict on purpose. A lenient parser turns a rambling answer into a merge
    decision, and the two failure directions here are not symmetric.
    """
    for line in (text or "").splitlines():
        line = line.strip()
        if line.startswith("VERDICT:"):
            value = line[len("VERDICT:"):].strip().lower()
            return value if value in ("serves", "does-not-serve") else None
    return None


def row_body(root: Path, repo: str, issue: str) -> str:
    out = subprocess.run(["gh", "issue", "view", issue, "--repo", repo,
                          "--json", "body", "-q", ".body"], cwd=root,
                         capture_output=True, text=True, encoding="utf-8",
                         timeout=30)
    if out.returncode:
        raise RuntimeError(out.stderr.strip() or "gh issue view failed")
    return out.stdout


def diff(root: Path, base_ref: str) -> str:
    out = subprocess.run(["git", "diff", f"origin/{base_ref}...HEAD"], cwd=root,
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=60)
    if out.returncode:
        raise RuntimeError(out.stderr.strip() or "git diff failed")
    return out.stdout[:MAX_DIFF]


def ask(prompt: str, key: str, model: str = MODEL) -> str:
    body = json.dumps({"model": model, "max_tokens": 1024,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(API, data=body, headers={
        "content-type": "application/json",
        "anthropic-version": "2023-06-01",
        "x-api-key": key})
    with urllib.request.urlopen(req, timeout=120) as resp:
        payload = json.loads(resp.read())
    return "".join(part.get("text", "") for part in payload.get("content", []))


def _open(why: str) -> int:
    """Green, and it says why. The saying-why is the half that matters."""
    print(f"reviewer: fail-open — {why}. This check did not judge the diff.")
    return 0


def main(argv: list[str], root: Path, cfg: dict) -> int:
    base_ref = os.environ.get("GITHUB_BASE_REF")
    head_ref = os.environ.get("GITHUB_HEAD_REF")
    if not base_ref or not head_ref:
        return _open("no PR context")
    issue = reconcile.issue_number(head_ref)
    if issue is None:
        return _open(f"branch names no row (`{head_ref}`)")
    repo = cfg.get("repo")
    if not repo:
        return _open("config names no `repo`")
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        # A fork's PR gets no secret, and a repo that has not set one yet gets
        # none either. Both are green, loudly.
        return _open("no credential — ANTHROPIC_API_KEY is not set")
    try:
        row, patch = row_body(root, repo, issue), diff(root, base_ref)
    except Exception as exc:
        return _open(f"could not read the row or the diff ({exc})")
    if not patch.strip():
        return _open("the diff is empty")
    try:
        answer = ask(_PROMPT.format(row=row, diff=patch), key)
    except Exception as exc:
        return _open(f"the model call failed ({exc})")
    call = verdict(answer)
    if call is None:
        return _open("the answer carried no verdict")
    if call == "serves":
        print(f"reviewer: #{issue} — the diff serves the row's stated outcome.")
        return 0
    print(f"reviewer: #{issue} — the diff does NOT serve the row's stated "
          f"outcome.\n{answer.strip()}")
    return 1
