#!/usr/bin/env python3
"""Import .qops/issues.md into GitHub Issues.

    python scripts/qops_import.py --labels        # create the label taxonomy
    python scripts/qops_import.py --validate      # never writes
    python scripts/qops_import.py --execute       # creates, labels, closes

`--labels` runs first in a fresh repo and needs no issue corpus. Nothing else
in the substrate creates labels, and a repo without them makes `pickup-loop`'s
query return empty and exit 0 forever.

The validator is the gate, not a formality: it refuses the import if any OPEN
row is missing a `type:`, a `state:` or a `gate:` label, if any row carries
`ready:auto` (review finding D1 — auto-eligibility is a control, and assigning
it at import bypasses the control before anyone has used it), or if a row names
a label or milestone that `.qops/config.yml` does not define.

Idempotent: `.qops/state.json` maps GL id -> issue number, so a re-run skips
what already exists. That file is gitignored on purpose (PRD v3 decision 21).

ponytail: this is `qops import` before the CLI exists. Phase 4 folds it in.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

try:
    from qops import config as qconfig
except ModuleNotFoundError:      # not installed: running from a checkout
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from qops import config as qconfig

# Not Path(__file__).parents[1] — as a pinned dependency that is site-packages,
# not the repo being imported into (P8.1 leak 3).
ROOT = qconfig.find_root()
CORPUS = ROOT / ".qops" / "issues.md"
CONFIG = ROOT / ".qops" / "config.yml"
STATE = ROOT / ".qops" / "state.json"

BLOCK = re.compile(
    r"<!--qops id=(?P<id>GL-[0-9a-z]+) state=(?P<state>\w+) type=(?P<type>\w+) "
    r"milestone=(?P<milestone>[\w-]+) labels=(?P<labels>.*?)-->\n(?P<body>.*?)<!--/qops-->",
    re.S,
)


def load_taxonomy() -> tuple[set[str], set[str]]:
    """Labels and milestones config.yml allows. Deliberately not a YAML parser —
    the file is ours and flat, and a dependency for five lines is not lazy."""
    text = CONFIG.read_text(encoding="utf-8")
    labels: set[str] = set()
    for ns in ("type", "state", "mission", "gate", "origin", "priority"):
        m = re.search(rf"^  {ns}: \[(.*?)\]", text, re.M)
        if m:
            labels |= {f"{ns}:{v.strip()}" for v in m.group(1).split(",")}
    m = re.search(r"^  flags: \[(.*?)\]", text, re.M)
    if m:
        labels |= {v.strip() for v in m.group(1).split(",")}
    m = re.search(r"^milestones: \[(.*?)\]", text, re.M)
    milestones = {v.strip() for v in m.group(1).split(",")} if m else set()
    return labels, milestones


def title_for(gid: str, body: str) -> str:
    """A readable title derived from the verbatim cell — a truncation, never new
    prose. The corpus H1 is 'GL-n - board row, <section>' for all 86 rows, which
    would make an unusable tracker."""
    m = re.search(r"## Board row, verbatim\s*(.*)", body, re.S)
    text = (m.group(1) if m else body).strip()
    text = text.split(" | ")[0]
    # the 12 closed one-liners carry a synthesized wrapper; the title wants the
    # payload, not the wrapper
    text = re.sub(r"^.{0,3}\s*CLOSED \(one-line traceability row on the board\):\s*", "", text)
    text = text.split("  Detail, if ever needed")[0]
    text = re.sub(r"[*`~]", "", text)  # not `_` — it lives inside identifiers
    text = re.sub(r"^\W+", "", text)                      # leading emoji / bullets
    text = re.sub(r"\s+", " ", text).strip()
    cut = text.find(". ")
    head = text[: cut + 1] if 20 <= cut <= 90 else text[:90].rstrip() + ("…" if len(text) > 90 else "")
    return f"{gid} — {head}".strip()


def parse() -> list[dict]:
    text = CORPUS.read_text(encoding="utf-8")
    rows = []
    for m in BLOCK.finditer(text):
        d = m.groupdict()
        d["labels"] = [l.strip() for l in d["labels"].split(",") if l.strip()]
        d["title"] = title_for(d["id"], d["body"])
        rows.append(d)
    return rows


def validate(rows: list[dict]) -> list[str]:
    labels_ok, milestones_ok = load_taxonomy()
    errs: list[str] = []
    seen: set[str] = set()
    for r in rows:
        gid, labs = r["id"], r["labels"]
        if gid in seen:
            errs.append(f"{gid}: duplicate id")
        seen.add(gid)
        if "ready:auto" in labs:
            errs.append(f"{gid}: carries ready:auto — refused at import (finding D1)")
        if r["state"] != "closed":
            for ns in ("type:", "state:", "gate:"):
                if not any(l.startswith(ns) for l in labs):
                    errs.append(f"{gid}: open row missing {ns}")
        for l in labs:
            if l not in labels_ok:
                errs.append(f"{gid}: label {l!r} is not in config.yml")
        if r["milestone"] not in milestones_ok:
            errs.append(f"{gid}: milestone {r['milestone']!r} is not in config.yml")
        if not r["title"].strip().endswith(tuple("….")) and len(r["title"]) < 12:
            errs.append(f"{gid}: title did not derive")
    return errs


def gh(*args: str, stdin: str | None = None) -> str:
    p = subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True, encoding="utf-8")
    if p.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)}\n{p.stderr.strip()}")
    return p.stdout.strip()


def execute(rows: list[dict]) -> None:
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    for r in rows:
        gid = r["id"]
        if gid in state:
            print(f"  {gid}: already #{state[gid]}, skipped")
            continue
        body = f"{r['body'].strip()}\n\n---\nImported from `.qops/issues.md` by `scripts/qops_import.py`."
        args = ["issue", "create", "--title", r["title"], "--body-file", "-",
                "--milestone", r["milestone"]]
        for l in r["labels"]:
            args += ["--label", l]
        url = gh(*args, stdin=body)
        num = url.rstrip("/").rsplit("/", 1)[-1]
        state[gid] = int(num)
        STATE.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
        if r["state"] == "closed":
            reason = "not planned" if "state:cancelled" in r["labels"] else "completed"
            gh("issue", "close", num, "--reason", reason)
        print(f"  {gid}: #{num} {'closed' if r['state'] == 'closed' else 'open'}")


def create_labels() -> int:
    """Create every label the taxonomy declares. Idempotent (`--force`).

    Nothing else in the substrate makes them, and `gh issue create --label`
    fails on a label the repo does not have. A repo with no labels makes
    `pickup-loop`'s query return empty and **exit 0** — an hourly task
    reporting "nothing eligible" is indistinguishable from a healthy idle
    queue, which is a silent failure by construction (PRD P8.4b step 3).

    Milestones are NOT created here: `gh` has no non-`api` verb for them, and
    `gh api -X` is denied in .claude/settings.json by a taken decision. An
    import naming an absent milestone fails loudly, which is the acceptable
    half of that trade.
    """
    labels, _ = load_taxonomy()
    repo = qconfig.load(ROOT).get("repo", "")
    if not repo:
        print("qops_import --labels: config names no `repo`", file=sys.stderr)
        return 2
    listed = subprocess.run(["gh", "label", "list", "--repo", repo, "--limit",
                             "200", "--json", "name"],
                            capture_output=True, text=True)
    if listed.returncode:
        print(f"qops_import --labels: {listed.stderr.strip()}", file=sys.stderr)
        return 1
    existing = {l["name"] for l in json.loads(listed.stdout or "[]")}
    # Create what is missing; never `--force`. An existing label carries a
    # hand-picked colour and a description, and re-creating it is a write with
    # nothing to gain — idempotence by not touching, not by overwriting.
    rc, made = 0, 0
    for name in sorted(labels - existing):
        p = subprocess.run(["gh", "label", "create", name, "--repo", repo],
                           capture_output=True, text=True)
        if p.returncode:
            print(f"  FAILED {name}: {p.stderr.strip()}", file=sys.stderr)
            rc = 1
        else:
            print(f"  created {name}")
            made += 1
    print(f"{made} created, {len(labels & existing)} already there — "
          f"{len(labels)} declared for {repo}")
    return rc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--labels", action="store_true",
                    help="create the label taxonomy; needs no issue corpus")
    a = ap.parse_args()

    # Before the corpus: a fresh repo has labels to create and nothing to import.
    if a.labels:
        return create_labels()

    rows = parse()
    print(f"{len(rows)} rows parsed from {CORPUS.relative_to(ROOT)}")
    opens = [r for r in rows if r["state"] != "closed"]
    print(f"  open {len(opens)} | closed {len(rows) - len(opens)}")

    errs = validate(rows)
    if errs:
        print(f"\nVALIDATION FAILED — {len(errs)} problem(s):")
        for e in errs:
            print(f"  {e}")
        return 1
    print("validation green")

    if a.execute:
        print("\nimporting…")
        execute(rows)
        print("done")
    elif not a.validate:
        print("\nnothing to do — pass --validate or --execute")
    return 0


if __name__ == "__main__":
    sys.exit(main())
