"""`qops reconcile` — the row-advance backstop (#150, finding 3).

`automerge.yml`'s `advance` job fires on `pull_request` + `closed` + `merged`.
When the `enable` job turns on auto-merge with `GITHUB_TOKEN`, GitHub raises
the merge as `github-actions[bot]` and **starts no workflow run from it**, so
no `closed` event exists and `advance` never runs. Every unattended sortie
therefore shipped and stayed `state:building` + `ready:auto`.

The repair is a reconciler and not a trigger, on purpose: the failure is an
event that was never observed, so a mechanism that *reads state* beats any
mechanism that reacts to events. It also repairs the row however the PR merged
— bot, human or hand-merge — and it is the only candidate that would have
surfaced #115 unaided. (A PAT was rejected: a stored credential in a public
repo cuts against the posture E13a settled on.)

`advance` stays. It is the fast path on a human-token merge; this is the
backstop that runs on `digest_cron`, and running both is a no-op by design.

It never reads `Closes #n`: the branch carries the issue number (ADR-0019) and
#116 proved a prompt instruction is a preference, not a control.

**Amended, ADR-0025: it closes a `gate:machine` row.** ADR-0020's reasoning for
auto-merge is that a green `gate:machine` PR leaves nothing for a human to
judge — the gate already judged it. The same reasoning applies to closing that
issue once its PR is merged: there is no taste read left to give. A
`gate:taste` row still only reaches `state:done`; closing that one is a
judgement, and stays the owner's. `no-auto` still vetoes the close, same as it
vetoes the merge.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

# ADR-0019: `<type>/<issue#>-<slug>`. `no-issue/<slug>` has no number and no
# sortie, so it matches nothing and is skipped with a reason.
BRANCH_ISSUE = re.compile(r"^[a-z]+/(\d+)-")

# Removing a label the issue does not carry is a no-op, so the whole family
# goes in one call rather than a read-then-write. Same list as `advance`.
STATE_LABELS = ["state:triage", "state:planned", "state:building", "state:gate",
                "state:review", "state:blocked"]
DONE = "state:done"


def gh(args: list[str]) -> str:
    p = subprocess.run(["gh", *args], capture_output=True, text=True,
                       encoding="utf-8")
    if p.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)}: {p.stderr.strip()}")
    return p.stdout.strip()


def issue_number(branch: str) -> str | None:
    m = BRANCH_ISSUE.match(branch or "")
    return m.group(1) if m else None


# ADR-0029: `origin:` is derived from a native sub-issue link, never claimed.
# A row files `origin:pending` when its session cannot honestly claim `owner`
# and a parent is intended (guard.py:origin_refusal); this is what resolves it.


def pending_issues(repo: str, limit: int, run=gh) -> list[str]:
    out = run(["issue", "list", "--repo", repo, "--label", "origin:pending",
               "--state", "open", "--limit", str(limit), "--json", "number"])
    return [str(i["number"]) for i in json.loads(out or "[]")]


def parent_origin(repo: str, issue: str, run=gh) -> str | None:
    """The parent's `origin:` value, from the REST sub-issues `parent`
    endpoint — a tracker fact, not prose. No link (404) or a parent that is
    itself not yet `owner`/`agent` both mean "no licence to derive yet"."""
    try:
        parent = json.loads(run(["api", f"repos/{repo}/issues/{issue}/parent"]))
    except Exception as exc:
        # A row with no parent 404s, and that is an answer: no licence to
        # derive yet. Anything else is not an answer, and returning None for it
        # would report an outage as "no parent link" - a swallowed exception
        # leaving a state change that says the wrong thing (CLAUDE.md).
        if "404" in str(exc) or "Not Found" in str(exc):
            return None
        raise
    labels = {l["name"] for l in parent.get("labels", [])}
    for value in ("owner", "agent"):
        if f"origin:{value}" in labels:
            return value
    return None


def derive_origin(repo: str, limit: int = 50, run=gh) -> dict:
    """Resolve every `origin:pending` row whose sub-issue link now names an
    `origin:owner`/`origin:agent` parent. A row with no such link stays
    `origin:pending` — briefly un-pickable is correct (ADR-0029).

    It reports failures rather than raising them. This runs ahead of
    `reconcile()` in `main()`, so an exception here would take the row-advance
    backstop down with it — a transient `gh` error in the origin sweep would
    stop merged rows reaching `state:done`, which is the one job this module
    exists to do. Per-item, a status and a reason, and the run still fails once
    after the loop (CLAUDE.md).
    """
    report = {"derived": [], "skipped": [], "failed": []}
    try:
        issues = pending_issues(repo, limit, run=run)
    except Exception as exc:
        report["failed"].append(("origin:pending", f"could not list: {exc}"))
        return report
    for issue in issues:
        try:
            origin = parent_origin(repo, issue, run=run)
            if origin is None:
                report["skipped"].append((issue,
                                          "no origin:owner/agent parent link"))
                continue
            run(["issue", "edit", issue, "--repo", repo, "--add-label",
                 f"origin:{origin}", "--remove-label", "origin:pending"])
            report["derived"].append((issue, origin))
        except Exception as exc:
            report["failed"].append((issue, str(exc)))
    return report


def merged_prs(repo: str, limit: int, run=gh) -> list[dict]:
    out = run(["pr", "list", "--repo", repo, "--state", "merged",
               "--limit", str(limit), "--json", "number,headRefName"])
    return json.loads(out or "[]")


def open_prs(repo: str, limit: int, run=gh) -> list[dict]:
    out = run(["pr", "list", "--repo", repo, "--state", "open",
               "--limit", str(limit), "--json",
               "number,headRefName,mergeStateStatus,autoMergeRequest"])
    return json.loads(out or "[]")


def advance_behind(repo: str, limit: int = 50, run=gh) -> dict:
    """#102: GitHub's native auto-merge only advances a stale branch when the
    repo has `allow_update_branch` on, which this repo does not (an owner
    setting, not ours to flip). A queued `gate:machine` PR that loses a merge
    race to a sibling sortie is `BEHIND` forever otherwise - not failing, not
    blocked, raising no signal. `DIRTY` is a human's; `BEHIND` is the only
    case this advances.
    """
    report = {"advanced": [], "skipped": [], "failed": []}
    for pr in open_prs(repo, limit, run=run):
        num, branch = pr.get("number"), pr.get("headRefName", "")
        issue = issue_number(branch)
        if not issue:
            report["skipped"].append((str(num), "branch names no issue"))
            continue
        if pr.get("mergeStateStatus") != "BEHIND":
            report["skipped"].append((issue, "not BEHIND"))
            continue
        if not pr.get("autoMergeRequest"):
            report["skipped"].append((issue, "auto-merge not enabled"))
            continue
        try:
            data = json.loads(run(["issue", "view", issue, "--repo", repo,
                                   "--json", "labels"]))
            labels = {l["name"] for l in data.get("labels", [])}
            if "gate:machine" not in labels:
                report["skipped"].append((issue, "not gate:machine"))
                continue
            if "no-auto" in labels:
                report["skipped"].append((issue, "no-auto"))
                continue
            run(["pr", "update-branch", str(num), "--repo", repo])
            report["advanced"].append((issue, str(num)))
        except Exception as exc:  # noqa: BLE001 - reported, never swallowed
            try:
                run(["issue", "comment", issue, "--repo", repo, "--body",
                     f"`qops reconcile` could not update-branch PR #{num} "
                     f"(`{branch}`), stuck `BEHIND`: `{exc}`"])
            except Exception:  # noqa: BLE001 - the report is the fallback
                pass
            report["failed"].append((issue, str(exc)))
    return report


def _closeable(labels: set[str]) -> bool:
    """ADR-0025: the gate already judged this row, so closing it judges
    nothing new. `gate:taste` and `no-auto` both withhold that."""
    return "gate:machine" in labels and "no-auto" not in labels


def reconcile(repo: str, limit: int = 50, run=gh) -> dict:
    """Advance every merged sortie whose row is not yet terminal, and close
    the ones a `gate:machine` merge leaves nothing left to judge.

    Returns a report; the caller decides the exit code. Idempotent: a row
    already closed is skipped, so nothing is relabelled and no second comment
    is written.
    """
    report = {"advanced": [], "closed": [], "skipped": [], "failed": []}
    for pr in merged_prs(repo, limit, run=run):
        num, branch = pr.get("number"), pr.get("headRefName", "")
        issue = issue_number(branch)
        if not issue:
            report["skipped"].append((str(num), "branch names no issue"))
            continue
        try:
            data = json.loads(run(["issue", "view", issue, "--repo", repo,
                                   "--json", "state,labels"]))
            labels = {l["name"] for l in data.get("labels", [])}
            if data.get("state") == "CLOSED":
                report["skipped"].append((issue, "issue already closed"))
                continue
            if "no-auto" in labels:
                # #12: a PR merged against this issue's branch number does not
                # mean this issue's full scope shipped - a partial fix (#32)
                # got re-labelled state:done on the next reconcile run,
                # clobbering a deliberate correction back to state:planned.
                # no-auto already means "the owner is handling this one"; it
                # now vetoes the relabel too, not just the merge.
                report["skipped"].append((issue, "no-auto"))
                continue
            if DONE in labels:
                if _closeable(labels):
                    run(["issue", "close", issue, "--repo", repo, "--comment",
                         f"Closed by `qops reconcile`: `gate:machine`, "
                         f"already `state:done`, PR #{num} (`{branch}`) is "
                         f"merged — nothing left to judge (ADR-0025)."])
                    report["closed"].append((issue, str(num)))
                else:
                    report["skipped"].append((issue, "already state:done"))
                continue
            edit = ["issue", "edit", issue, "--repo", repo,
                    "--add-label", DONE, "--remove-label", "ready:auto"]
            for label in STATE_LABELS:
                edit += ["--remove-label", label]
            run(edit)
            if _closeable(labels):
                run(["issue", "close", issue, "--repo", repo, "--comment",
                     f"Advanced to `state:done` and closed by `qops "
                     f"reconcile`: PR #{num} (`{branch}`) is merged, "
                     f"`gate:machine` — nothing left to judge (ADR-0025)."])
                report["closed"].append((issue, str(num)))
            else:
                run(["issue", "comment", issue, "--repo", repo, "--body",
                     f"Advanced to `state:done` by `qops reconcile`: PR #{num} "
                     f"(`{branch}`) is merged. Labels only — closing stays the "
                     f"owner's, `gate:taste` (ADR-0020)."])
                report["advanced"].append((issue, str(num)))
        except Exception as exc:  # noqa: BLE001 - reported, never swallowed
            # CLAUDE.md: a swallowed per-item exception writes a status and a
            # reason onto the row, and the run still fails once after the loop.
            try:
                run(["issue", "comment", issue, "--repo", repo, "--body",
                     f"`qops reconcile` could not advance this row against "
                     f"merged PR #{num}: `{exc}`"])
            except Exception:  # noqa: BLE001 - the report is the fallback
                pass
            report["failed"].append((issue, str(exc)))
    return report


def main(argv: list[str], root: Path, cfg: dict) -> int:
    repo = cfg.get("repo", "")
    if not repo:
        print("qops reconcile: .qops/config.yml names no `repo`", file=sys.stderr)
        return 2
    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else 50
    origin_report = derive_origin(repo, limit=limit)
    for issue, origin in origin_report["derived"]:
        print(f"derived #{issue}: origin:{origin}")
    for issue, why in origin_report["skipped"]:
        print(f"origin skipped #{issue}: {why}")
    for issue, why in origin_report["failed"]:
        print(f"origin FAILED #{issue}: {why}", file=sys.stderr)
    report = reconcile(repo, limit=limit)
    for issue, pr in report["advanced"]:
        print(f"advanced #{issue}: PR #{pr} merged")
    for issue, pr in report["closed"]:
        print(f"closed #{issue}: PR #{pr} merged, gate:machine")
    # A skip prints its reason. A reconciler that silently reconciles nothing
    # is the defect it exists to fix, wearing a different hat.
    for issue, why in report["skipped"]:
        print(f"skipped #{issue}: {why}")
    print(f"reconcile: {len(report['advanced'])} advanced, "
          f"{len(report['closed'])} closed, "
          f"{len(report['skipped'])} skipped, {len(report['failed'])} failed")
    if report["failed"]:
        for issue, why in report["failed"]:
            print(f"FAILED #{issue}: {why}", file=sys.stderr)
    behind_report = advance_behind(repo, limit=limit)
    for issue, pr in behind_report["advanced"]:
        print(f"update-branch #{issue}: PR #{pr} was BEHIND")
    for issue, why in behind_report["failed"]:
        print(f"BEHIND FAILED #{issue}: {why}", file=sys.stderr)
    # Either sweep failing fails the run, once, after all three have finished.
    # The origin sweep must not stop the backstop, and must not pass silently
    # either.
    return 1 if (report["failed"] or origin_report["failed"]
                 or behind_report["failed"]) else 0
