"""qops — the per-project ways-of-working layer. One CLI, eleven verbs.

    qops brief      what a session gets at SessionStart, <=400 tokens
    qops ledger     append a session event (hook payload on stdin)
    qops resume     print or regenerate .qops/resume.md
    qops guard      PreToolUse hook; `qops guard scan` is the CI half
    qops close      close a sortie: label state:done and close the issue
    qops init       scaffold a blank repo to a clean doctor
    qops install    render .github/workflows from templates + .qops/config.yml
    qops doctor     detect drift, broken doc links, an uninstalled hook
    qops metrics    S1/S2/S4/S9/S10; --state regenerates the PRD §1 table
    qops reconcile  advance the row of every merged sortie whose PR landed
    qops migrate    propose a taxonomy migration over open rows, apply nothing
                    until --execute (--dry-run/--execute/--verify, ADR-0030)
    qops pending    what is waiting on the owner, and what the loop takes next
"""

import sys
from pathlib import Path

from . import (brief, close, config, guard, init, install, ledger, metrics,
               migrate, pending, reconcile, review)

VERBS = {
    "brief": (brief.main, "session brief for SessionStart (<=400 tokens)"),
    "ledger": (ledger.main, "append a session event; no args prints the tail"),
    "resume": (ledger.resume_main, "print .qops/resume.md; --write regenerates"),
    "guard": (guard.main, "PreToolUse hook; `scan` greps the tree for tripwires"),
    "close": (close.main, "close a sortie issue and label it state:done"),
    "init": (init.main, "scaffold a blank repo to a clean doctor"),
    "install": (install.main, "render .github/workflows/ from the config"),
    "doctor": (install.doctor_main, "drift, broken doc links, hooks, hot-path cap"),
    "review": (review.main, "does the PR's diff serve its row's stated outcome"),
    "metrics": (metrics.main, "S1/S2/S4/S9/S10; --state writes the state report"),
    "reconcile": (reconcile.main, "advance merged sorties whose row is not state:done"),
    "migrate": (migrate.main, "--dry-run/--execute/--verify a taxonomy migration"),
    "pending": (pending.main, "waiting on you, and what the loop takes next; read-only"),
}

# init runs before any .qops/config.yml exists — the one verb that must not
# have the root walked or the config loaded before its own main runs.
NO_CONFIG = {"init"}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    verb, rest = argv[0], argv[1:]
    if verb not in VERBS:
        print(f"qops: unknown verb {verb!r}\n{__doc__}", file=sys.stderr)
        return 2
    fn, help_text = VERBS[verb]
    if "--help" in rest or "-h" in rest:
        print(f"qops {verb} — {help_text}")
        return 0
    if verb in NO_CONFIG:
        return fn(rest, Path.cwd(), {})
    root = config.find_root()
    try:
        return fn(rest, root, config.load(root))
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    sys.exit(main())
