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

Limits, inherited from ADR-0020: it **labels, it never closes** — a merge means
the code landed, not that the sortie is judged. And it never reads `Closes #n`:
the branch carries the issue number (ADR-0019) and #116 proved a prompt
instruction is a preference, not a control.
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


def merged_prs(repo: str, limit: int, run=gh) -> list[dict]:
    out = run(["pr", "list", "--repo", repo, "--state", "merged",
               "--limit", str(limit), "--json", "number,headRefName"])
    return json.loads(out or "[]")


def reconcile(repo: str, limit: int = 50, run=gh) -> dict:
    """Advance every merged sortie whose row is not yet terminal.

    Returns a report; the caller decides the exit code. Idempotent: a row
    already carrying `state:done` is skipped, so nothing is relabelled and no
    second comment is written.
    """
    report = {"advanced": [], "skipped": [], "failed": []}
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
            if DONE in labels:
                report["skipped"].append((issue, "already state:done"))
                continue
            edit = ["issue", "edit", issue, "--repo", repo,
                    "--add-label", DONE, "--remove-label", "ready:auto"]
            for label in STATE_LABELS:
                edit += ["--remove-label", label]
            run(edit)
            run(["issue", "comment", issue, "--repo", repo, "--body",
                 f"Advanced to `state:done` by `qops reconcile`: PR #{num} "
                 f"(`{branch}`) is merged. Labels only — closing stays the "
                 f"owner's (ADR-0020)."])
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
    report = reconcile(repo, limit=limit)
    for issue, pr in report["advanced"]:
        print(f"advanced #{issue}: PR #{pr} merged")
    # A skip prints its reason. A reconciler that silently reconciles nothing
    # is the defect it exists to fix, wearing a different hat.
    for issue, why in report["skipped"]:
        print(f"skipped #{issue}: {why}")
    print(f"reconcile: {len(report['advanced'])} advanced, "
          f"{len(report['skipped'])} skipped, {len(report['failed'])} failed")
    if report["failed"]:
        for issue, why in report["failed"]:
            print(f"FAILED #{issue}: {why}", file=sys.stderr)
        return 1
    return 0
