"""pickup-loop — pick the next sortie an unattended agent may start.

Default OFF. Registered as a disabled Windows scheduled task, one per repo root,
so that turning it on is one `schtasks /change /enable` and not a build. The
registration is a machine fact the repo cannot see - `docs/reference/loops.md`
carries the exact command, because a machine fact recorded nowhere is one a code
change can invalidate silently, and one did.

**The task names its root; it never derives one.** `--root <path>` plus a
matching WorkingDirectory. Both are refused-if-wrong rather than guessed at
(`repo_root` below): with two roots on one cron host, a picker that resolves its
root from wherever the scheduler started it either reads the wrong backlog or
reads nothing, and exits 0 doing it.

Eligibility is deliberately narrow, and every condition is the owner's to grant:

    state:planned  AND  NOT no-auto  AND  gate: is not none
    AND ( ready:auto  OR  ( origin:owner  AND  NOT gate:taste  AND  body names a test ) )

`ready:auto` is never applied by the triager (see .claude/agents/triager.md) —
only the owner grants it. `gate:none` blocks pickup because a sortie with no
named gate has no definition of done. The second route (ADR-0023) is the
owner's filing itself standing as the grant on an `origin:owner` row: no label
is written, so there is nothing to clean up afterwards.

**Every run also produces the reviewer's verdict** for each ready PR and posts
it as a PR comment (#80, `qops/review.py`), and `--review` runs that pass alone
and picks nothing. It rides this run rather than a second scheduled task
because a registration is a hand-made machine fact the repo cannot see (#12),
so the registered command line is unchanged; and it runs on the host rather
than in CI because the model call needs the subscription this host has and CI
does not.

`--launch` is what actually starts an agent. Without it this prints what it
would have picked and exits 0, which is also how the scheduled task is proved
to run without starting anything.

The launch carries a **scoped** write grant (#122): the coder role's toolset and
nothing else. It removes the interactive prompt, it does not widen what is
permitted — the PreToolUse guard and branch protection stay the real controls,
and a blanket bypass (`--dangerously-skip-permissions`) is never passed.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# The substrate that ships with this root, ahead of whatever is installed on
# the host. `python <root>/scripts/qops_pickup.py` puts *the script's
# directory* on sys.path[0], not the repo root, so `import qops` reached past
# the repo into site-packages - and every unattended run this week executed
# this repo's scripts against a `0.1.0` library while the repo declared `0.2.0`
# (#74). WorkingDirectory does not help: cwd is not sys.path[0] for a script.
#
# This used to be a `try/except ModuleNotFoundError` fallback, which could
# never fire: a stale install is not a missing one, so the module imported and
# the names did not. Unconditional, because a run operates on the root it named
# and there is no second candidate worth preferring. On a root that pins qops
# instead of vendoring it, the inserted path holds no `qops/` and the import
# falls through to site-packages exactly as before.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qops import config as qconfig, install, ledger, review  # noqa: E402

# eligible(), unwritable() and UNWRITABLE live in qops/install.py (#71): doctor
# needs the same predicates pickup-loop uses, and qops/ may not import from
# scripts/, so the dependency runs the other way and this re-exports them.
from qops.install import BLOCKING_FLAGS, UNWRITABLE, eligible, unwritable  # noqa: E402,F401

# The coder role's tools (.claude/agents/coder.md), verbatim. A sortie branches,
# edits, commits and opens a PR with these; anything wider is #123's question,
# not this launch's grant.
LAUNCH_TOOLS = "Read,Edit,Write,Grep,Glob,Bash"

# Any flag that trades the guard for convenience. Asserted absent, not merely
# omitted - the wrong fix for #122 was one of these.
BLANKET_BYPASS = ("--dangerously-skip-permissions", "--dangerously-bypass-permissions")


def candidates(root: Path) -> list[dict] | None:
    """The eligible issues, or None when the backlog could not be read.

    The distinction is the whole hazard: an empty list is an idle queue and a
    failed query is a broken picker, the picker exits 0 on both, and until this
    returned None they printed the same line. A repo with no labels makes the
    query itself return empty, which is the same shape one level down
    (`scripts/qops_import.py --labels` is what a fresh repo runs first).
    """
    out = subprocess.run(
        ["gh", "issue", "list", "--state", "open", "--limit", "100",
         "--json", "number,title,labels,updatedAt,body"],
        cwd=root, capture_output=True, text=True)
    if out.returncode:
        print(out.stderr.strip(), file=sys.stderr)
        return None
    return [i for i in json.loads(out.stdout or "[]") if eligible(i)]


def repo_root(argv: list[str]) -> Path:
    """`--root <path>`, else the nearest ancestor of cwd holding .qops/config.yml.

    NOT `Path(__file__).parents[1]`: once qops is a pinned dependency that is
    site-packages, not the repo whose backlog is being picked. One scheduled
    task per consuming repo passes `--root`; a hand run in a checkout needs
    nothing (P8.1 leak 3).

    **A resolved root that holds no config is refused, and it says where the
    root came from.** The registered task's WorkingDirectory was empty, so the
    walk up from cwd started wherever the scheduler happened to launch the
    process - and `find_root()` returns cwd when it finds nothing. There are
    two roots on this host now, so the two silent outcomes of that are the
    wrong repo's backlog, or a query against a directory that is not a repo at
    all. The task names its root; it does not derive one.
    """
    if "--root" in argv:
        i = argv.index("--root") + 1
        if i >= len(argv):
            raise SystemExit("pickup-loop: --root takes a path")
        root, how = Path(argv[i]).resolve(), "--root"
    else:
        root, how = qconfig.find_root(), "the walk up from the working directory"
    if not qconfig.path(root).exists():
        raise SystemExit(
            f"pickup-loop: {root} is not a qops root - {qconfig.path(root)} "
            f"does not exist. That root came from {how}. A scheduled task must "
            f"pass --root: with no WorkingDirectory set it starts wherever the "
            f"scheduler puts it.")
    return root


def report_unlaunchable(root: Path, num: str, paths: list[str]) -> None:
    """Say it on the row, once. Skipping in silence would read as an idle
    queue, and repeating it hourly would be noise the owner learns to ignore.
    """
    marker = "pickup-loop: this row cannot be worked unattended"
    seen = subprocess.run(["gh", "issue", "view", num, "--json", "comments",
                           "--jq", ".comments[].body"],
                          cwd=root, capture_output=True, text=True, encoding="utf-8")
    if marker in (seen.stdout or ""):
        return
    subprocess.run(
        ["gh", "issue", "comment", num, "--body",
         f"{marker}. Its `Expected to touch:` names "
         + ", ".join(f"`{p}`" for p in paths)
         + ", and the launch runs under `--permission-mode acceptEdits`, which "
           "does not grant writes to the files that configure Claude Code "
           "itself. Skipped before the claim, so no session was spent and the "
           "row is untouched. Work it in a session, or split the part that "
           "needs no such write (#48)."],
        cwd=root, capture_output=True, text=True)
    ledger.append(root, "pickup_skip", {"issue": num, "paths": paths})


# Three consecutive failed runs on one row and the picker stops taking it.
#
# The Loop Doctor's finding 1 made the claim the no-progress stop: claim before
# launching, so an hourly fire cannot re-pick the same sortie forever. #122
# then made a failed run release that claim, so a row is never stuck at
# state:building where no later fire can reach it. Both are right, and together
# they mean a row that fails DETERMINISTICALLY is picked every hour forever -
# #47 burned four sessions an hour apart and nothing counted (#49).
STRIKES = 3

# A ledger grows forever, and an enablement six weeks ago is not this week's
# evidence. Releases older than this do not count toward a strike-out.
STRIKE_WINDOW_DAYS = 14


def strikes(root: Path, num: str, now: str | None = None) -> int:
    """Consecutive failed runs on this row, most recent last.

    Consecutive, not cumulative: a `pickup` with no `pickup_release` after it
    is a run that worked, and it resets the count. The off-by-one here fails
    open - it keeps burning sessions - so the interleaved case is the one the
    tests lean on. A `pickup_skip` (#48) is not a strike: nothing was spent and
    no session ever attempted the row.
    """
    cutoff = (datetime.fromisoformat(now) if now else datetime.now(timezone.utc)
              ) - timedelta(days=STRIKE_WINDOW_DAYS)
    count, open_attempt = 0, False
    for rec in ledger.read(root):
        if str(rec.get("issue")) != str(num):
            continue
        try:
            if datetime.fromisoformat(rec["ts"]) < cutoff:
                continue
        except (KeyError, ValueError):
            continue
        if rec.get("event") == "pickup":
            if open_attempt:      # the previous claim never released: it worked
                count = 0
            open_attempt = True
        elif rec.get("event") == "pickup_release":
            count += 1
            open_attempt = False
    return count


def struck_out(root: Path, num: str, now: str | None = None) -> bool:
    return strikes(root, num, now) >= STRIKES


def strike_out(root: Path, num: str, count: int, why: str) -> None:
    """Stop picking this row, and say on it that a machine wrote an owner flag.

    `no-auto` already means "the owner is handling this one" and already vetoes
    the pickup, the merge, the close and the relabel, so it is the right flag
    and not a new one. It is still a widening: every other `no-auto` in this
    substrate is the owner's. It is defensible only because the alternative is
    an unbounded spend, and a widening done quietly is worse than the spend.
    """
    subprocess.run(["gh", "issue", "comment", num, "--body",
                    f"pickup-loop: **{count} consecutive unattended runs "
                    f"failed** on this row, the last one with `{why}`. No "
                    f"further attempts - `no-auto` applied so the queue moves "
                    f"on.\n\nThis flag is normally the **owner's** alone. A "
                    f"loop wrote it here because the alternative is a session "
                    f"an hour, indefinitely, on a row that has already refused "
                    f"three (#49). Remove `no-auto` to hand it back to the "
                    f"loop once the cause is understood; the run logs are the "
                    f"place to start."],
                   cwd=root, capture_output=True, text=True)
    subprocess.run(["gh", "issue", "edit", num, "--add-label", "no-auto"],
                   cwd=root, capture_output=True, text=True)
    ledger.append(root, "pickup_struck_out", {"issue": num, "strikes": count})
    print(f"pickup-loop: #{num} struck out after {count} failed runs.",
          file=sys.stderr)


def first_launchable(root: Path, picks: list[dict]) -> dict | None:
    """The least-recently-updated row the launch can actually work.

    A skipped row is not an idle queue: `nothing eligible` means the backlog
    was read and nothing qualified, and printing that sentence for a backlog
    whose every row was skipped would collapse two states loops.md's reading
    table keeps apart.
    """
    for issue in sorted(picks, key=lambda i: i["updatedAt"]):
        num = str(issue["number"])
        # A row already struck out is skipped in silence: strike_out() said it
        # once on the row and applied `no-auto`, so this only fires in the gap
        # before that label lands, or if it was removed by hand.
        if struck_out(root, num):
            print(f"pickup-loop: skipping #{num} - struck out after "
                  f"{strikes(root, num)} failed runs (#49).")
            continue
        paths = unwritable(issue.get("body") or "")
        if not paths:
            return issue
        print(f"pickup-loop: skipping #{num} - the launch cannot write "
              f"{', '.join(paths)}.")
        report_unlaunchable(root, num, paths)
    return None


def main(argv: list[str]) -> int:
    """The heartbeat is here, and it is the whole of #76.

    Every silence the picker had already fixed assumes the process got far
    enough to print. This one records that a run *finished* — whatever it
    decided — so the absence of a recent record is readable as state by
    `qops brief`. It cannot be written by a run that died at import, which is
    exactly the property wanted: four dead runs on 2026-08-21 left nothing
    anywhere, and the loop was as dead as a disabled task and said as much.

    A failing run still counts as one that spoke: it returned, so it reported.
    `repo_root()` raising is before this and stays silent here, because a root
    that is not a qops root has nowhere to write a ledger.
    """
    root = repo_root(argv)
    # The verdict pass rides the *registered* run, and adds no registration
    # (#12, #80): a scheduled task is a hand-made machine fact the repo cannot
    # see, and a second one is a second copy of that problem. It is here rather
    # than in CI because here is where the Claude subscription is. `--review`
    # runs it alone, which is also how it is proved by hand.
    if "--review" in argv:
        return _review(root)
    rc = _run(argv, root)
    ledger.append(root, "pickup_ran", {"rc": rc})
    # After the pickup, so a PR this run just opened is judged this run - and
    # behind `--launch`, by the rule this script already follows: a dry run
    # says what it would have done and writes nothing anywhere. The first
    # non-zero wins, because the scheduler gets one exit code and a reviewer
    # that could not judge is not a quieter failure than a picker that could
    # not pick.
    if "--launch" not in argv:
        return rc
    return rc or _review(root)


def _review(root: Path) -> int:
    rc = review.produce(root, qconfig.load(root))
    ledger.append(root, "review_ran", {"rc": rc})
    return rc


def _run(argv: list[str], root: Path) -> int:
    cfg = qconfig.load(root)
    # Named on every run, eligible or not. A log line that says which root and
    # which tracker were read is what separates a healthy idle queue from a
    # picker pointed at the wrong place; both of those exit 0.
    print(f"pickup-loop: root {root}, tracker {cfg.get('repo', '(none in config)')}")
    picks = candidates(root)
    if picks is None:
        print("pickup-loop: could not read the backlog - nothing was picked and "
              "the queue state is UNKNOWN, which is not the same as empty.",
              file=sys.stderr)
        return 1
    if not picks:
        print("pickup-loop: nothing eligible (state:planned + ready:auto + a real gate).")
        return 0
    issue = first_launchable(root, picks)
    if issue is None:
        print("pickup-loop: every eligible row names a path the launch may not "
              "write - nothing was picked, and this is NOT an idle queue (#48).")
        return 0
    print(f"pickup-loop: #{issue['number']} {issue['title']}")
    if "--launch" not in argv:
        print("pickup-loop: dry run, not launching. Pass --launch to start an agent.")
        return 0
    # Claim it BEFORE launching. Without this the next hourly fire picks the
    # same issue again - the run does not change the issue, so it stays the
    # least-recently-updated eligible one forever, one session per hour.
    num = str(issue["number"])
    claim = subprocess.run(["gh", "issue", "edit", num,
                            "--remove-label", "state:planned",
                            "--add-label", "state:building"],
                           cwd=root, capture_output=True, text=True)
    if claim.returncode:
        print(f"pickup-loop: could not claim #{num}, not launching: "
              f"{claim.stderr.strip()}", file=sys.stderr)
        return 1
    log = run_log_path(root, num)
    ledger.append(root, "pickup", {"issue": num, "log": str(log)})
    print(f"pickup-loop: run log {log}")
    # Straight to the file rather than captured in memory, so the account
    # survives a run that is killed rather than one that returns.
    with log.open("w", encoding="utf-8", errors="replace") as fh:
        rc = subprocess.run(launch_argv(launch_prompt(num)), cwd=root,
                            env=launch_env(), stdout=fh,
                            stderr=subprocess.STDOUT).returncode
    # produced_work stays the thing that decides. Capturing output must not
    # become it: an empty branch scoring as success is how #57 and #71 died.
    if rc or not produced_work(root, num):
        why = f"exit {rc}" if rc else "no commit and no PR"
        release(root, num, why, log)
        # Counted after the release, so this run is included in the count.
        if struck_out(root, num):
            strike_out(root, num, strikes(root, num), why)
        return rc or 1
    return 0


BRANCH_PREFIXES = ("feat", "fix", "docs", "chore", "refactor", "test")


def launch_prompt(num: str) -> str:
    """The instruction half of #128. `automerge-loop` is the assertion half —
    an instruction in a prompt is a preference, not a control (GL-53).

    The branch clause exists because the first unattended run read `type:code`
    off the issue and branched `code/116-...`: the pattern matched, but a label
    is not a commit type. The link line is `Refs`, never `Closes` — a merge is
    not a judgement, so the loop advances the label and the owner closes."""
    return (f"Work sortie #{num} to its stated acceptance criteria. "
            f"Branch first as `<type>/{num}-<slug>` where <type> is a commit "
            f"type — one of {'|'.join(BRANCH_PREFIXES)} — never an issue label. "
            f"Commit, open a PR whose body says `Refs #{num}` (not `Closes`), "
            f"and stop. Do not request a GitHub review — the repo has one "
            f"collaborator and GitHub rejects a self-review request; "
            f"`automerge-loop` labels the issue `state:review` when the owner's "
            f"eyes are needed (#151). Do not merge. "
            f"Run only the tests you touched — the full suite takes ~3.5 "
            f"minutes, longer than a Bash call may run, and `test.yml` runs it "
            f"on every push, which is the gate. Never background a command and "
            f"wait for it: this session ends when your turn does, so a "
            f"backgrounded run never reports and the sortie dies uncommitted.")


def launch_argv(prompt: str) -> list[str]:
    return ["claude", "-p", prompt,
            "--permission-mode", "acceptEdits",
            "--allowedTools", LAUNCH_TOOLS]


def launch_env() -> dict:
    """The launched session is unattended, and says so. `qops guard` reads this
    to refuse a sandbox escape that an interactive owner could still allow."""
    return {**os.environ, "QOPS_UNATTENDED": "1"}


def produced_work(root: Path, num: str) -> bool:
    """A session that exits 0 having built nothing is a failed run, not a done
    sortie. Branch naming is ADR-0019: `<type>/<issue#>-<slug>`.

    An *empty* branch is not work. Both 2026-08-18 sorties (#57, #71) wrote
    their whole change, backgrounded the full test suite, and ended the turn
    waiting for a notification that a `-p` run can never receive - the branch
    existed, pointed at master's tip, and read here as success. The claim was
    never released and neither issue said anything was wrong. Count the
    commits, not the ref."""
    branches = subprocess.run(
        ["git", "branch", "--list", f"*/{num}-*", "--format=%(refname:short)"],
        cwd=root, capture_output=True, text=True).stdout.split()
    base = qconfig.load(root)["default_branch"]
    for branch in branches:
        ahead = subprocess.run(["git", "rev-list", "--count", f"{base}..{branch}"],
                               cwd=root, capture_output=True, text=True).stdout.strip()
        if ahead.isdigit() and int(ahead) > 0:
            return True
    prs = subprocess.run(["gh", "pr", "list", "--search", num, "--json", "number"],
                         cwd=root, capture_output=True, text=True).stdout.strip()
    return bool(json.loads(prs or "[]"))


def run_log_path(root: Path, num: str) -> Path:
    """Where a launched run's output goes: `.qops/runs/<issue>-<utc>.log`.

    `subprocess.run(...)` used to pass the launch's stdout straight to the
    scheduled task's console, which Task Scheduler discards. So the most
    expensive part of a run was the part with no record, and diagnosing #47
    meant reading raw session transcripts out of ~/.claude/projects by hand.

    Ignored by git, and that is a control rather than hygiene: this repo is
    public (ADR-0022) and the file is whatever the session printed.
    """
    d = Path(root) / ".qops" / "runs"
    d.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return d / f"{num}-{stamp}.log"


def release(root: Path, num: str, why: str, log: Path | None = None) -> None:
    """The claim is not a one-way door. A failed run puts the sortie back where
    the next fire can reach it and says why (CLAUDE.md, GL-46).

    `why` names the symptom. `log` is where the account is - without it the
    next reader repeats #47's diagnosis by hand (#50)."""
    subprocess.run(["gh", "issue", "edit", num,
                    "--remove-label", "state:building",
                    "--add-label", "state:planned"],
                   cwd=root, capture_output=True, text=True)
    where = f" The run log is `{log}`." if log else ""
    subprocess.run(["gh", "issue", "comment", num, "--body",
                    f"pickup-loop: unattended run produced nothing ({why}). "
                    f"Claim released, back to `state:planned`.{where}"],
                   cwd=root, capture_output=True, text=True)
    ledger.append(root, "pickup_release",
                  {"issue": num, "why": why, "log": str(log) if log else None})
    print(f"pickup-loop: released #{num} ({why}).", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
