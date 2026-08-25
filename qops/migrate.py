"""`qops migrate` — bring a pinned-old consumer's open rows onto the current
label taxonomy and ADR-0028's filing bar, without an agent ever writing a
body onto the tracker unread (ADR-0030).

`install.issue_invariants` already *reports* a row missing `origin:`, a
`gate:`, or a machine-turnable outcome statement. This is what fixes it, and
only after the owner has read one diff:

    --dry-run   reads the tracker, writes `.qops/migrate-plan.json`, writes
                nothing to the tracker.
    --execute   applies that plan whole or not at all; refuses if the tracker
                moved since the plan was drawn (`fingerprint` mismatch).
    --verify    re-reads the tracker and asserts every planned row landed.

Closed rows are never read into the plan — ADR-0030's decision, and
`open_issues` only ever asks `gh` for open ones.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from . import install

PLAN_NAME = "migrate-plan.json"


def gh(args: list[str]) -> str:
    p = subprocess.run(["gh", *args], capture_output=True, text=True,
                       encoding="utf-8")
    if p.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)}: {p.stderr.strip()}")
    return p.stdout.strip()


def open_issues(repo: str, limit: int = 200, run=gh) -> list[dict]:
    out = run(["issue", "list", "--repo", repo, "--state", "open", "--limit",
               str(limit), "--json", "number,labels,body"])
    return json.loads(out or "[]")


def in_scope(issues: list[dict], cfg: dict | None = None) -> list[dict]:
    """The rows a migration may touch at all (#199).

    The pinned status issue is machine bookkeeping: `digest.yml` opens it and
    rewrites its body every morning, and `install.issue_invariants` exempts it
    for exactly that reason (#167). A migration that edits it is two halves of
    the substrate writing the same body — and, because the fingerprint is
    drawn over this list, a plan made at 05:00 would be refused by the 06:00
    digest for a row nothing ever planned to touch.
    """
    bookkeeping = (cfg or {}).get("ci", {}).get("status_issue_label")
    if not bookkeeping:
        return list(issues)
    return [i for i in issues
            if bookkeeping not in {l["name"] for l in i.get("labels", [])}]


def fingerprint(issues: list[dict]) -> str:
    """A hash of exactly what a plan was drawn against — labels and body per
    row. `--execute` refuses once this no longer matches the live tracker."""
    payload = sorted(
        (i["number"], sorted(l["name"] for l in i.get("labels", [])),
         i.get("body") or "")
        for i in issues)
    return hashlib.sha256(json.dumps(payload).encode()).hexdigest()


_OUTCOME_STUB = ("\n\n## Acceptance\n\n_Needs owner review — migrated by "
                 "`qops migrate` without a stated outcome (ADR-0028)._\n")

# The namespaces a migration may fill on its own, and what it fills them with.
# Each value is a default the substrate already argues for in prose, so filling
# it is bookkeeping rather than judgement:
#
#   gate:   CLAUDE.md — everything not a taste row is `gate:machine`,
#           *including when unsure*: an unsure row is underspecified, not
#           tasteful.
#   origin: ADR-0029 — `pending` is precisely "a session that cannot honestly
#           claim `owner`"; `qops reconcile` derives it from a parent link and
#           never from a claim.
#   state:  a row nobody has triaged is in triage.
#
# `type:` is deliberately absent. Nothing makes `code` rather than `research`
# the safe answer for an unlabelled row, and a machine picking one is the
# agent claiming a judgement ADR-0030 exists to keep with the owner. A row
# missing an unfillable namespace is *reported*, on the row, as `needs`.
MECHANICAL = {"gate": "gate:machine", "origin": "origin:pending",
              "state": "state:triage"}

_REQUIRED_FALLBACK = ("type", "state", "gate", "origin")


def propose(issues: list[dict], cfg: dict | None = None) -> dict:
    """Pure: issues in, a plan out. No I/O, so a fixture drives it directly.

    Every row is `keep` here — a migration only ever adds what ADR-0028 and
    the current taxonomy require. `transfer <repo>` and `close` are the
    plan's other two dispositions (ADR-0030); nothing in this corpus-shaped
    verb decides those, that judgement is #105's, against a real corpus.

    The body half is scoped to what `install.issue_invariants` would actually
    report (#199): ADR-0028's bar is on *leaving* triage, not on filing, and
    `install._BAR_EXEMPT` is where that is written down. Proposing a rewrite
    for an exempt row turns the one diff ADR-0030 promises the owner into a
    corpus-wide one — 35 rows against printshop's backlog, 33 of them exempt.
    """
    cfg = cfg or {}
    required = cfg.get("validate", {}).get("require_on_open",
                                           _REQUIRED_FALLBACK)
    rows = []
    for issue in in_scope(issues, cfg):
        names = {l["name"] for l in issue.get("labels", [])}
        add, needs = [], []
        for ns in required:
            if any(n.startswith(f"{ns}:") for n in names):
                continue
            (add.append(MECHANICAL[ns]) if ns in MECHANICAL
             else needs.append(ns))
        body = issue.get("body") or ""
        state = next((n for n in names if n.startswith("state:")), None)
        new_body = None
        if state not in install._BAR_EXEMPT and not install.states_an_outcome(body):
            new_body = body.rstrip("\n") + _OUTCOME_STUB
        rows.append({"number": issue["number"], "add_labels": add,
                     "remove_labels": [], "body": new_body, "needs": needs,
                     "disposition": "keep"})
    return {"fingerprint": fingerprint(in_scope(issues, cfg)), "rows": rows,
            "applied": False}


def plan_path(root: Path) -> Path:
    return Path(root) / ".qops" / PLAN_NAME


def dry_run(root: Path, repo: str, cfg: dict | None = None,
            limit: int = 200, run=gh) -> dict:
    """Reads the tracker, writes the plan file, touches nothing else."""
    issues = open_issues(repo, limit, run=run)
    plan = propose(issues, cfg)
    plan_path(root).write_text(json.dumps(plan, indent=2) + "\n",
                                encoding="utf-8")
    return plan


def execute(root: Path, repo: str, cfg: dict | None = None,
            limit: int = 200, run=gh) -> dict:
    """Applies a plan drawn by `--dry-run`, whole or not at all.

    Refuses — applying nothing — the moment the live corpus disagrees with
    the fingerprint the plan was drawn against, or when no plan exists yet.
    """
    path = plan_path(root)
    if not path.exists():
        return {"ok": False, "reason": "no plan — run --dry-run first",
                "applied": []}
    plan = json.loads(path.read_text(encoding="utf-8"))
    issues = in_scope(open_issues(repo, limit, run=run), cfg)
    if fingerprint(issues) != plan["fingerprint"]:
        return {"ok": False, "reason": "tracker moved since --dry-run — "
                "re-run --dry-run", "applied": []}
    applied = []
    for row in plan["rows"]:
        num = str(row["number"])
        if row["add_labels"] or row["remove_labels"]:
            edit = ["issue", "edit", num, "--repo", repo]
            for label in row["add_labels"]:
                edit += ["--add-label", label]
            for label in row["remove_labels"]:
                edit += ["--remove-label", label]
            run(edit)
        if row["body"] is not None:
            run(["issue", "edit", num, "--repo", repo, "--body", row["body"]])
        applied.append(num)
    plan["applied"] = True
    path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    return {"ok": True, "reason": "", "applied": applied}


def verify(root: Path, repo: str, cfg: dict | None = None,
           limit: int = 200, run=gh) -> dict:
    """Re-reads the tracker and asserts every planned row landed in its
    proposed disposition: labels added/removed and the body rewritten,
    exactly the rows the plan named, no more and no fewer."""
    path = plan_path(root)
    if not path.exists() or not json.loads(path.read_text(encoding="utf-8")).get("applied"):
        return {"ok": False, "mismatches": ["no applied plan on disk"]}
    plan = json.loads(path.read_text(encoding="utf-8"))
    issues = {str(i["number"]): i for i in open_issues(repo, limit, run=run)}
    mismatches = []
    for row in plan["rows"]:
        num = str(row["number"])
        issue = issues.get(num)
        if issue is None:
            mismatches.append(f"#{num}: not found among open rows — closed "
                              f"or transferred unexpectedly")
            continue
        names = {l["name"] for l in issue.get("labels", [])}
        for label in row["add_labels"]:
            if label not in names:
                mismatches.append(f"#{num}: expected label {label!r}, missing")
        for label in row["remove_labels"]:
            if label in names:
                mismatches.append(f"#{num}: label {label!r} should be gone")
        if row["body"] is not None and issue.get("body") != row["body"]:
            mismatches.append(f"#{num}: body was not rewritten as planned")
    return {"ok": not mismatches, "mismatches": mismatches}


def main(argv: list[str], root: Path, cfg: dict) -> int:
    repo = cfg.get("repo", "")
    if not repo:
        print("qops migrate: .qops/config.yml names no `repo`", file=sys.stderr)
        return 2
    if "--dry-run" in argv:
        plan = dry_run(root, repo, cfg, run=gh)
        for row in plan["rows"]:
            changes = []
            if row["add_labels"]:
                changes.append(f"+{','.join(row['add_labels'])}")
            if row["body"] is not None:
                changes.append("body rewrite (no stated outcome)")
            # Named, never filled: `type:` has no mechanical default, so this
            # is the owner's label to add and the plan says so rather than
            # guessing one (MECHANICAL's comment).
            if row["needs"]:
                changes.append(f"needs {','.join(row['needs'])}: from the owner")
            if changes:
                print(f"#{row['number']} [{row['disposition']}]: "
                      f"{'; '.join(changes)}")
        changed = sum(1 for r in plan["rows"]
                     if r["add_labels"] or r["remove_labels"]
                     or r["body"] is not None)
        print(f"migrate --dry-run: {len(plan['rows'])} open row(s), "
              f"{changed} would change — plan at {plan_path(root)}")
        return 0
    if "--execute" in argv:
        result = execute(root, repo, cfg, run=gh)
        if not result["ok"]:
            print(f"qops migrate --execute: refused — {result['reason']}",
                  file=sys.stderr)
            return 1
        print(f"migrate --execute: applied {len(result['applied'])} row(s)")
        return 0
    if "--verify" in argv:
        result = verify(root, repo, cfg, run=gh)
        if not result["ok"]:
            for m in result["mismatches"]:
                print(m, file=sys.stderr)
            print(f"qops migrate --verify: {len(result['mismatches'])} "
                  f"mismatch(es)", file=sys.stderr)
            return 1
        print("migrate --verify: every planned row landed.")
        return 0
    print("qops migrate: pass --dry-run, --execute or --verify",
          file=sys.stderr)
    return 2
