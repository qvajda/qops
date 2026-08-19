"""`qops close <issue>` — the scribe step, without a session.

Labels the issue state:done, closes it, and records it in the ledger so the
next brief knows the sortie ended.
"""

import subprocess
import sys
from pathlib import Path

from . import ledger


def main(argv: list[str], root: Path, cfg: dict) -> int:
    nums = [a.lstrip("#") for a in argv if a.lstrip("#").isdigit()]
    if not nums:
        print("usage: qops close <issue-number> [--comment TEXT]", file=sys.stderr)
        return 2
    comment = ""
    if "--comment" in argv:
        comment = argv[argv.index("--comment") + 1]
    rc = 0
    for num in nums:
        for state in cfg["labels"]["state"]:
            if state != "done":
                subprocess.run(["gh", "issue", "edit", num, "--remove-label",
                                f"state:{state}"], cwd=root, capture_output=True)
        add = subprocess.run(["gh", "issue", "edit", num, "--add-label", "state:done"],
                             cwd=root, capture_output=True, text=True)
        cmd = ["gh", "issue", "close", num]
        if comment:
            cmd += ["--comment", comment]
        done = subprocess.run(cmd, cwd=root, capture_output=True, text=True)
        if done.returncode or add.returncode:
            print((done.stderr or add.stderr).strip(), file=sys.stderr)
            rc = 1
            continue
        ledger.append(root, "close", {"issue": num})
        print(f"closed #{num}")
    return rc
