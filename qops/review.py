"""`qops review` — the reviewer's verdict, ADR-0028 §4 and #80.

The required checks on `master` are `test`, `gate`, `tripwires` and
`doc-links`. All four are mechanical, so nothing between a filing and `master`
reads for *meaning*. ADR-0028 accepted an unread chain with this as the
compensating control, and then it was never built; ADR-0029 §1 makes it the
prerequisite for unattended planning.

**The verdict is produced on the cron host; CI is only the check.** CI cannot
use the Claude subscription — credential resolution ends at an interactive
browser login or a short-lived token, so a reviewer in CI means a metered API
key, a second cost line that grows exactly as the loop gets busier. So the host
runs the model on the subscription (`scripts/qops_pickup.py --review`, the same
`claude -p` path the launch already uses) and writes the answer as a PR comment;
this module's `main()` runs in CI, reads that comment, and exits on it. It calls
no model and needs no secret.

The host writes a *comment*, never a commit status: `.claude/settings.json`
denies `gh api -X` against repo settings by a taken decision (ADR-0016/0020),
and `gh pr comment` is a plain verb that routes around nothing.

**The verdict is keyed on the head SHA.** A verdict on an older commit would
authorise whatever was pushed after it, which is a reviewer that approves code
it never read. A verdict for a different SHA is no verdict.

**The fail-open path is the load-bearing one.** This is a language model wired
into a check that will become required under `enforce_admins: true`, where
GitHub gives the owner no override. The asymmetry:

  a wrong fail-closed   one bad diff merges - reversible, on the tracker
  a wrong fail-open     every PR in the repo freezes, and the only exit is the
                        owner editing protection settings by hand, because
                        `.claude/settings.json` denies `gh api -X` against them

So a PR with no context, a config with no repo, unreadable comments, an
unparseable answer and — the one the split adds — a host that is asleep and has
posted no verdict for this SHA are all **green, and every one of them says so**.
A silent fail-open is worse than no check at all: it is indistinguishable from a
real pass, so it reads as a reader that read.

Only a verdict is a rejection.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from . import ledger, reconcile

MAX_DIFF = 60_000        # bytes; a diff past this is summarised by truncation
MARKER = "<!-- qops-reviewer:"
# Passes spent on one commit before the host stops asking. The pass is hourly
# and a PR can sit open for days waiting on the owner, so an unbounded retry is
# one model call an hour, forever, on a failure that will not fix itself.
MAX_ATTEMPTS = 3

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

Answer from what is below. Do not read files, run commands or use tools.

--- THE ROW ---
{row}

--- THE DIFF ---
{diff}
"""


def marker(sha: str) -> str:
    """The line that makes a comment a verdict, and ties it to one commit."""
    return f"{MARKER}{sha} -->"


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


def _gh(root: Path, *args: str, timeout: int = 60) -> str:
    out = subprocess.run(["gh", *args], cwd=root, capture_output=True,
                         text=True, encoding="utf-8", errors="replace",
                         timeout=timeout)
    if out.returncode:
        raise RuntimeError(out.stderr.strip() or f"gh {args[0]} failed")
    return out.stdout


def row_body(root: Path, repo: str, issue: str) -> str:
    return _gh(root, "issue", "view", issue, "--repo", repo,
               "--json", "body", "-q", ".body", timeout=30)


def pr_diff(root: Path, repo: str, number: str) -> str:
    """`gh pr diff` rather than `git diff`: the host judges other branches
    without checking any of them out, and CI never needs the diff at all."""
    return _gh(root, "pr", "diff", str(number), "--repo", repo)[:MAX_DIFF]


def comments(root: Path, repo: str, number: str) -> list[str]:
    """Oldest first, which is what `newest verdict wins` reads backwards."""
    raw = _gh(root, "pr", "view", str(number), "--repo", repo,
              "--json", "comments", "-q", ".comments[].body", timeout=30)
    # `-q` joins bodies with newlines, so split on the marker itself rather
    # than on a boundary that a comment's own text could forge. A verdict body
    # is one marker line plus the model's answer; everything before the first
    # marker is not a verdict.
    return [MARKER + part for part in raw.split(MARKER)[1:]]


def latest_verdict(bodies: list[str], sha: str) -> str | None:
    """The newest comment carrying this SHA's marker, parsed. None means no
    verdict for *this* commit — an older commit's verdict is not one."""
    for body in reversed(bodies):
        if marker(sha) in body:
            return verdict(body)
    return None


def judged(bodies: list[str], sha: str) -> bool:
    """Whether this commit has been *spoken about*, verdict or give-up.

    This, and not `latest_verdict`, is what stops the pass re-asking. The two
    differ on exactly one comment: the one the host writes when it gave up, and
    that comment exists so the hourly pass has somewhere to stop.
    """
    return any(marker(sha) in body for body in bodies)


def attempts(root: Path, num: str, sha: str) -> int:
    """How many passes already failed to judge this commit. Counted off the
    ledger, the same way `pickup-loop` counts a row's strikes."""
    return sum(1 for e in ledger.read(root)
               if e.get("event") == "review_unjudged"
               and e.get("pr") == num and e.get("sha") == sha)


def _unjudged(root: Path, repo: str, num: str, sha: str, why: str) -> None:
    """Record the failed attempt, and after `MAX_ATTEMPTS` stop asking.

    The hazard this closes: a PR that stays open (waiting on the owner, or
    never merged at all) whose review keeps failing would otherwise be one
    `claude -p` per hour, forever, on a failure that is not going to change by
    itself. Three tries covers a rate limit or a network blip; past that the
    host says so on the PR and goes quiet until someone pushes a commit, which
    is a new SHA and a fresh count.
    """
    n = attempts(root, num, sha) + 1
    ledger.append(root, "review_unjudged", {"pr": num, "sha": sha, "why": why,
                                            "n": n})
    print(f"reviewer: #{num} {sha[:8]} — not judged ({why}), "
          f"attempt {n}/{MAX_ATTEMPTS}.")
    if n < MAX_ATTEMPTS:
        return
    try:
        _gh(root, "pr", "comment", num, "--repo", repo, "--body",
            f"{marker(sha)}\n\n**No verdict.** The reviewer could not judge "
            f"this commit in {n} passes — last reason: {why}. It has stopped "
            f"asking; CI fails this open and says so. Push a commit to get a "
            f"new one.")
    except Exception as exc:      # the give-up comment is itself best-effort
        print(f"reviewer: #{num} — could not say so on the PR ({exc}).")


def ask(prompt: str, root: Path) -> str:
    """`claude -p` on the host's subscription — the same path
    `scripts/qops_pickup.py:launch_argv` uses. No tools are granted: this reads
    a diff that is already in the prompt, and a reviewer that can run commands
    is a wider grant than a reviewer needs."""
    out = subprocess.run(["claude", "-p", prompt, "--allowedTools", ""],
                         cwd=root, capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=600)
    if out.returncode:
        raise RuntimeError(out.stderr.strip() or f"claude exited {out.returncode}")
    return out.stdout


def open_prs(root: Path, repo: str) -> list[dict]:
    """Ready PRs only. A draft is not up for review, and #91 is the standing
    proof that a draft can be a shape the owner decided against."""
    raw = _gh(root, "pr", "list", "--repo", repo, "--state", "open",
              "--json", "number,headRefName,headRefOid,isDraft")
    return [pr for pr in json.loads(raw or "[]") if not pr.get("isDraft")]


def produce(root: Path, cfg: dict) -> int:
    """The host pass: judge every ready PR that has no verdict for its head SHA
    yet, and write each verdict as a comment.

    Nothing here is a check. A PR this cannot judge simply gets no comment, and
    CI fails that open, loudly — which is the same outcome as the host being
    asleep, and is why the split is safe.
    """
    repo = cfg.get("repo")
    if not repo:
        print("reviewer: config names no `repo` — nothing to review.")
        return 1
    try:
        prs = open_prs(root, repo)
    except Exception as exc:
        print(f"reviewer: could not list the open PRs ({exc}).")
        return 1
    # Named on every pass, judged or not. A pass that says nothing at all reads
    # the same whether there was nothing to judge or nothing was reachable —
    # the distinction `pickup-loop` already prints its root and tracker for.
    print(f"reviewer: {len(prs)} ready PR(s) on {repo}.")
    failed = 0
    for pr in prs:
        num, sha = str(pr["number"]), pr["headRefOid"]
        try:
            if judged(comments(root, repo, num), sha):
                print(f"reviewer: #{num} {sha[:8]} is already judged.")
                continue
            # The ledger, not the comment, is what stops the asking: if the
            # give-up comment itself could not be posted, a pass that trusted
            # the comment alone would run the model again every hour forever.
            if attempts(root, num, sha) >= MAX_ATTEMPTS:
                print(f"reviewer: #{num} {sha[:8]} — given up on after "
                      f"{MAX_ATTEMPTS} passes; not asking again.")
                continue
            issue = reconcile.issue_number(pr["headRefName"])
            if issue is None:
                why = f"the branch names no row (`{pr['headRefName']}`)"
            else:
                patch = pr_diff(root, repo, num)
                if not patch.strip():
                    why = "the diff is empty"
                else:
                    answer = ask(_PROMPT.format(
                        row=row_body(root, repo, issue), diff=patch), root)
                    call = verdict(answer)
                    if call is None:
                        # Not posted as a verdict: a rambling answer must not
                        # become one. Retried, then given up on loudly.
                        why = "the answer carried no verdict"
                    else:
                        _gh(root, "pr", "comment", num, "--repo", repo,
                            "--body", f"{marker(sha)}\n\n{answer.strip()}")
                        print(f"reviewer: #{num} {sha[:8]} — {call}.")
                        continue
        except Exception as exc:
            why = str(exc)
        _unjudged(root, repo, num, sha, why)
        failed += 1
    if failed:
        print(f"reviewer: {failed} PR(s) were not judged this pass.")
        return 1
    return 0


def _open(why: str) -> int:
    """Green, and it says why. The saying-why is the half that matters."""
    print(f"reviewer: fail-open — {why}. This check did not judge the diff.")
    return 0


def main(argv: list[str], root: Path, cfg: dict) -> int:
    """The CI half: read the host's verdict for this exact commit, exit on it."""
    number = os.environ.get("PR_NUMBER")
    sha = os.environ.get("PR_HEAD_SHA")
    if not number or not sha:
        return _open("no PR context")
    repo = cfg.get("repo")
    if not repo:
        return _open("config names no `repo`")
    try:
        bodies = comments(root, repo, number)
    except Exception as exc:
        return _open(f"could not read the PR's comments ({exc})")
    call = latest_verdict(bodies, sha)
    if call is None:
        if judged(bodies, sha):
            # The host spoke and had nothing to say: it gave up on this commit
            # after `MAX_ATTEMPTS`, and the reason is on the PR.
            return _open(f"the reviewer gave up on {sha[:8]} — its reason is a "
                         f"comment on this PR")
        return _open(f"no verdict posted for {sha[:8]} — the reviewer runs on "
                     f"the cron host, and a host that is asleep is a fail-open, "
                     f"not a hang")
    if call == "serves":
        print(f"reviewer: #{number} {sha[:8]} — the diff serves the row's "
              f"stated outcome.")
        return 0
    print(f"reviewer: #{number} {sha[:8]} — the diff does NOT serve the row's "
          f"stated outcome. The verdict is on the PR.")
    return 1
