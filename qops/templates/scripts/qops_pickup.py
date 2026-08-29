"""pickup-loop — pick the next sortie an unattended agent may start.

Default OFF. Registered as a disabled scheduled task, one per repo root, so
that turning it on is one `Enable-ScheduledTask` and not a build. `qops install`
renders and registers it from `.qops/config.yml` (ADR-0032) and `qops doctor`
checks it against what the config renders: it used to be hand-made, naming a
machine's interpreter and a machine's checkout under a name with no project in
it, and a code change invalidated that definition once with nothing to notice.

**The task names its root; it never derives one.** `--root <path>` plus a
matching WorkingDirectory. Both are refused-if-wrong rather than guessed at
(`repo_root` below): with two roots on one cron host, a picker that resolves its
root from wherever the scheduler started it either reads the wrong backlog or
reads nothing, and exits 0 doing it.

Eligibility is deliberately narrow, and every condition is the owner's to grant:

    state:planned  AND  NOT no-auto  AND  gate: is not none
    AND ( ready:auto  OR  ( origin:owner  AND  body names a test ) )

`ready:auto` is never applied by the triager (see .claude/agents/triager.md) —
only the owner grants it. `gate:none` blocks pickup because a sortie with no
named gate has no definition of done. The second route (ADR-0023) is the
owner's filing itself standing as the grant on an `origin:owner` row: no label
is written, so there is nothing to clean up afterwards.

**Every run also produces the reviewer's verdict** for each ready PR and posts
it as a PR comment (#80, `qops/review.py`), and `--review` runs that pass alone
and picks nothing. It rides this run rather than a second scheduled task
because one loop is one registration (#12), so the registered command line is
unchanged; and it runs on the host rather
than in CI because the model call needs the subscription this host has and CI
does not.

`--launch` is what actually starts an agent. Without it this prints what it
would have picked and exits 0, which is also how the scheduled task is proved
to run without starting anything — so the task passes it only when
`pickup_launch:` says so, default off. Baked into the schedule, as it was, the
dry run was unreachable from the schedule.

The launch carries a **scoped** write grant (#122): the coder role's toolset and
nothing else. It removes the interactive prompt, it does not widen what is
permitted — the PreToolUse guard and branch protection stay the real controls,
and a blanket bypass (`--dangerously-skip-permissions`) is never passed.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
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

from qops import config as qconfig, install, ledger, pending, reconcile, review  # noqa: E402

# eligible(), unwritable(), UNWRITABLE, plannable(), decomposable(),
# interviewed(), strikes() and struck_out() live in qops/install.py (#71,
# #131): doctor and `qops pending` need the same predicates pickup-loop uses,
# and qops/ may not import from scripts/, so the dependency runs the other way
# and this re-exports them.
from qops.install import (BLOCKING_FLAGS, STRIKES, STRIKE_WINDOW_DAYS,  # noqa: E402,F401
                          UNWRITABLE, decomposable, eligible, interviewed,
                          plannable, strikes, struck_out, unwritable)

# The coder role's tools (.claude/agents/coder.md), verbatim. A sortie branches,
# edits, commits and opens a PR with these; anything wider is #123's question,
# not this launch's grant.
LAUNCH_TOOLS = "Read,Edit,Write,Grep,Glob,Bash"

# Any flag that trades the guard for convenience. Asserted absent, not merely
# omitted - the wrong fix for #122 was one of these.
BLANKET_BYPASS = ("--dangerously-skip-permissions", "--dangerously-bypass-permissions")


def backlog(root: Path) -> list[dict] | None:
    """Every open row, or None when the backlog could not be read.

    The distinction is the whole hazard: an empty list is an idle queue and a
    failed query is a broken picker, the picker exits 0 on both, and until this
    returned None they printed the same line. A repo with no labels makes the
    query itself return empty, which is the same shape one level down
    (`scripts/qops_import.py --labels` is what a fresh repo runs first).

    One query per pass. The build queue and the plan queue are two filters over
    this list (#82), not two round trips.
    """
    out = subprocess.run(
        ["gh", "issue", "list", "--state", "open", "--limit", "100",
         "--json", "number,title,labels,updatedAt,body"],
        cwd=root, capture_output=True, text=True, encoding="utf-8")
    if out.returncode:
        print(out.stderr.strip(), file=sys.stderr)
        return None
    if out.stdout is None:
        print("gh issue list: stdout could not be decoded", file=sys.stderr)
        return None
    return json.loads(out.stdout or "[]")


def candidates(root: Path) -> list[dict] | None:
    """The rows the loop may *build*: `install.eligible()` over the backlog."""
    rows = backlog(root)
    return None if rows is None else [i for i in rows if eligible(i)]


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
#
# `STRIKES`/`STRIKE_WINDOW_DAYS`/`strikes()`/`struck_out()` live in
# qops/install.py (#131) and are re-exported above.


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
        labels = {l["name"] for l in issue.get("labels", [])}
        # A row already struck out is skipped in silence: strike_out() said it
        # once on the row and applied `no-auto`, so this only fires in the gap
        # before that label lands, or if it was removed by hand.
        if struck_out(root, num, labels):
            print(f"pickup-loop: skipping #{num} - struck out after "
                  f"{strikes(root, num, labels)} failed runs (#49).")
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
    if "--unreached-triage" in argv:
        return _print_unreached_triage(root)
    cfg = qconfig.load(root)
    # Rides this same registered run, ahead of `_run` (#241): the merge that
    # triggered this fire cannot see itself on a later PR event, so a
    # bot-merged row would otherwise sit `state:building` until the next PR
    # event or the daily floor. `--launch`'s rule applies unchanged - a dry
    # run reconciles nothing, same as `_review`.
    reconcile_rc = _reconcile(root, cfg) if "--launch" in argv else 0
    rc = _run(argv, root)
    ledger.append(root, "pickup_ran", {"rc": rc})
    # The alert pass rides this run too (#120), and it owns its own read
    # rather than reusing `_run`'s: it must still fire when `_run` bailed
    # early, and `pending.backlog()` already prints the unreadable-vs-empty
    # distinction the pass needs.
    alert_rc = _alert(argv, root, cfg)
    # After the pickup and the alert, so a PR this run just opened is judged
    # this run - and behind `--launch`, by the rule this script already
    # follows: a dry run says what it would have done and writes nothing
    # anywhere. The first non-zero wins, because the scheduler gets one exit
    # code and a reviewer that could not judge is not a quieter failure than
    # a picker that could not pick.
    if "--launch" not in argv:
        return rc or alert_rc
    return reconcile_rc or rc or alert_rc or _review(root)


def _reconcile(root: Path, cfg: dict) -> int:
    """The reconcile pass, ridden ahead of the picker rather than a second
    scheduled task (#241). A config naming no tracker is a config defect
    `doctor` already reports, not a picker failure repeated hourly - the same
    reasoning `_alert` follows for the same check."""
    if not cfg.get("repo"):
        return 0
    rc = reconcile.main([], root, cfg)
    ledger.append(root, "reconcile_ran", {"rc": rc})
    return rc


def _review(root: Path) -> int:
    rc = review.produce(root, qconfig.load(root))
    ledger.append(root, "review_ran", {"rc": rc})
    return rc


# The set is `pending.waiting_on_owner()`, never re-derived (ADR-0031 §1):
# two lists that agree today diverge at the next edit, and `qops/pending.py`
# is fenced out of this row anyway. The alerter holds no trigger predicate of
# its own — `test_the_alerter_holds_no_trigger_predicate` reads these
# functions' own source to say so.

ALERT_NAME_MAX = 80  # a session name the owner scans, not a full render


def alert_session_name(project: str, num: int, clause: str) -> str:
    """The triage surface (ADR-0031 §4). Several rows may wait at once, and
    the name is how the owner tells them apart without opening the tracker
    first — a struck-out row reads differently from a taste-judgement one.

    Named from `cfg["project"]`, not the root directory or the git remote:
    config is the only project-specific surface (#215)."""
    clause = " ".join(clause.split())
    return f"{project} #{num} {clause}"[:ALERT_NAME_MAX]


def alert_prompt(num: int, clause: str) -> str:
    """The row plus a drafted proposal (ADR-0031 §4), never the full
    `pending` render — two concurrent alerts would each recite the other.
    Short by construction: the issue body is never embedded in argv,
    `review.WINDOWS_CMDLINE_MAX` is a live failure mode on this host
    (#111).

    The review clause (`pending.py`'s "the loop asked for eyes") gets its own
    text (ADR-0036 §5): that clause is the row's one owner moment end to end,
    artefact in hand, not a proposal-and-wait. Every other clause keeps
    today's text. Matched on the clause's own wording, not a new label
    literal — the alerter still holds no trigger predicate of its own."""
    if clause.endswith("the loop asked for eyes"):
        return (
            f"Read issue #{num} on this repo's tracker and its open PR - "
            f"the loop asked for eyes. Present the artefact and what the "
            f"row asked for. If the owner approves, merge. If the owner "
            f"rejects, ask what comes next (abandon / retry in-session / "
            f"record the feedback on the row / something else the context "
            f"suggests) - do not choose for them.")
    return (
        f"Read issue #{num} on this repo's tracker - it is waiting on the "
        f"owner ({clause}). State the situation in a few lines, propose "
        f"exactly one recommendation with at most four options, then wait "
        f"for the owner - this reaches them, it does not act on their "
        f"behalf.")


def alert_argv(num: int, clause: str, name: str) -> list[str]:
    return ["claude", "--remote-control", name, alert_prompt(num, clause)]


def _alert(argv: list[str], root: Path, cfg: dict) -> int:
    """Launch a remote-control session for one row waiting on the owner
    (#120, ADR-0031), when there is one.

    One launch per pass (ADR-0031 §4 step 3): several rows may wait at once,
    and that is delegation, not contention - the hourly loop drains them one
    at a time rather than opening a fan of sessions on the first activation.
    The claim below is the record of having fired, taken **before** the
    launch: a claimed row is absent from `waiting_on_owner()` on the next
    pass, so silence needs no local state.
    """
    repo = cfg.get("repo", "")
    if not repo:
        # A config naming no tracker is a config defect, and `doctor` is where
        # it is reported. This pass rides the pickup run's exit code, so
        # failing here would report that defect as a picker failure, hourly,
        # forever - and `_run` carries on past the same config for the same
        # reason, printing `(none in config)`.
        print("pickup-loop: .qops/config.yml names no tracker - "
              "nothing to alert on.")
        return 0
    rows = pending.backlog(repo)
    if rows is None:
        print("pickup-loop: could not read the backlog for alerting - queue "
              "state is UNKNOWN, which is not the same as empty.",
              file=sys.stderr)
        return 1
    reap_rc = _reap(argv, root, cfg, rows)
    waiting = pending.waiting_on_owner(root, rows)
    if not waiting:
        print("pickup-loop: nothing waiting on the owner.")
        return reap_rc
    line = waiting[0]
    num = int(line.split()[0].lstrip("#"))
    clause = line.split(" — ", 1)[1]
    name = alert_session_name(cfg.get("project", "qops"), num, clause)
    print(f"pickup-loop: #{num} is waiting on the owner - {clause}")
    if "--launch" not in argv:
        print(f"pickup-loop: dry run, not alerting. Would launch {name!r}.")
        return reap_rc
    row = next((r for r in rows if r["number"] == num), None)
    existing = {l["name"] for l in (row or {}).get("labels", [])}
    prior_state = next((l for l in existing if l.startswith("state:")), None)
    added = ["state:building", "no-auto"]
    claim = ["gh", "issue", "edit", str(num)]
    if prior_state:
        claim += ["--remove-label", prior_state]
    for label in added:
        claim += ["--add-label", label]
    claimed = subprocess.run(claim, cwd=root, capture_output=True, text=True)
    if claimed.returncode:
        why = f"could not claim #{num}: {claimed.stderr.strip()}"
        print(f"pickup-loop: {why}", file=sys.stderr)
        ledger.append(root, "alert_failed", {"issue": num, "why": why})
        return 1
    # Detached: an interactive session never returns, and the hourly task
    # must not block on it. `cwd` is ROOT, not `loop_worktree()` (#9) - this
    # is the owner's session, in his tree, and the loop worktree is reset by
    # the next build pass.
    try:
        proc = subprocess.Popen(
            alert_argv(num, clause, name), cwd=root,
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
            start_new_session=(os.name != "nt"))
    except OSError as exc:
        why = f"could not launch {name!r}: {exc}"
        print(f"pickup-loop: {why}", file=sys.stderr)
        ledger.append(root, "alert_failed", {"issue": num, "why": why})
        return 1
    # `pid` and `added`/`prior_state` are what a later pass needs to tell
    # this claim's session apart from a live one and to release it (#147) -
    # `session` (the display name) alone carries neither.
    ledger.append(root, "alert_launched",
                  {"issue": num, "session": name, "pid": proc.pid,
                   "prior_state": prior_state, "added": added})
    print(f"pickup-loop: launched {name!r} for #{num}.")
    return reap_rc


def _pid_alive(pid: int, image: str) -> bool | None:
    """Whether the process an alert launch started is still running.

    `None` means *could not tell* and is never treated as `False` - per #147,
    an absent `session_end` record cannot be trusted either way, so liveness
    is read from the host's process list, not the ledger. POSIX: signal 0,
    the standard existence probe. Windows has no such call, so `tasklist` is
    asked instead and the pid must appear under `image` - narrowing this to
    the process `alert_argv()` actually launched, so a pid reused by an
    unrelated program after a reboot does not read as the same session.

    ponytail: no check beyond image name (working directory, start time), so
    a killed session whose pid is reused by another `claude` process within
    the same boot still reads as alive. Narrow further if that proves to
    matter in practice.
    """
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except OSError:
            return None
        return True
    exe = image if image.lower().endswith(".exe") else image + ".exe"
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FI", f"IMAGENAME eq {exe}", "/NH"],
            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode:
        return None
    return str(pid) in out.stdout


def _reap(argv: list[str], root: Path, cfg: dict, rows: list[dict]) -> int:
    """Release a claim whose session is gone (#147, ADR-0031 §5).

    Runs ahead of `waiting_on_owner()` (called from `_alert`, before it reads
    the waiting set): the released row is **not** mutated in `rows`, so this
    same pass's `waiting_on_owner()` still treats it as claimed, and the
    alert fires on the *next* pass - which is the acceptance criterion as
    filed, not this one re-alerting on a row it just touched.

    A claim with no `alert_launched` record is never reaped: it may be the
    owner's own claim, or a build claim `_run()` took that this pass never
    launched, and taking it would be a second stall wearing the costume of a
    fix. `added`/`prior_state` are replayed from that record rather than
    written here, so this function names no label of its own.
    """
    unreadable = False
    for row, _ in pending.claimed_rows(root, rows):
        num = row["number"]
        launch = None
        for rec in ledger.read(root):
            if rec.get("event") == "alert_launched" and rec.get("issue") == num:
                launch = rec
        if launch is None or "pid" not in launch:
            continue
        image = Path(alert_argv(0, "", "")[0]).name
        alive = _pid_alive(launch["pid"], image)
        if alive is None:
            unreadable = True
            continue
        if alive:
            continue
        if "--launch" not in argv:
            print(f"pickup-loop: dry run, would release #{num} - session "
                  f"{launch['pid']} is gone.")
            continue
        claim = ["gh", "issue", "edit", str(num)]
        for label in launch.get("added", []):
            claim += ["--remove-label", label]
        prior_state = launch.get("prior_state")
        if prior_state:
            claim += ["--add-label", prior_state]
        released = subprocess.run(claim, cwd=root, capture_output=True, text=True)
        if released.returncode:
            print(f"pickup-loop: could not release #{num}: "
                  f"{released.stderr.strip()}", file=sys.stderr)
            unreadable = True
            continue
        ledger.append(root, "alert_released", {"issue": num, "pid": launch["pid"]})
        print(f"pickup-loop: released #{num} - session {launch['pid']} is gone.")
    if unreadable:
        print("pickup-loop: could not tell whether every claimed session is "
              "alive - reaping nothing rather than guessing.", file=sys.stderr)
    return 1 if unreadable else 0


def _run(argv: list[str], root: Path) -> int:
    cfg = qconfig.load(root)
    # Named on every run, eligible or not. A log line that says which root and
    # which tracker were read is what separates a healthy idle queue from a
    # picker pointed at the wrong place; both of those exit 0.
    print(f"pickup-loop: root {root}, tracker {cfg.get('repo', '(none in config)')}")
    rows = backlog(root)
    if rows is None:
        print("pickup-loop: could not read the backlog - nothing was picked and "
              "the queue state is UNKNOWN, which is not the same as empty.",
              file=sys.stderr)
        return 1
    repo = cfg.get("repo", "")
    picks = [i for i in rows if eligible(i) and not clarification(root, repo, i)]
    # **Building is never starved by planning** (#82). The plan pass runs only
    # where the run would previously have stopped: nothing eligible, or nothing
    # eligible that the launch may write.
    issue = first_launchable(root, picks) if picks else None
    if issue is None:
        if not picks:
            print("pickup-loop: nothing eligible to build (state:planned + a real gate).")
        elif all(struck_out(root, str(i["number"]),
                            {l["name"] for l in i.get("labels", [])}) for i in picks):
            print("pickup-loop: every eligible row struck out - nothing was "
                  "built, and this is NOT an idle queue (#49).")
        else:
            print("pickup-loop: every eligible row names a path the launch may not "
                  "write - nothing was built, and this is NOT an idle queue (#48).")
        return _answer(argv, root, cfg, rows)
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
    before = launch_evidence(root, num)
    launch_cwd = loop_worktree(root, cfg)
    with log.open("w", encoding="utf-8", errors="replace") as fh:
        rc = subprocess.run(launch_argv(launch_prompt(num), cfg), cwd=launch_cwd,
                            env=launch_env("coder"), stdout=fh,
                            stderr=subprocess.STDOUT).returncode
    # produced_work stays the thing that decides. Capturing output must not
    # become it: an empty branch scoring as success is how #57 and #71 died.
    if rc or not produced_work(root, num, before):
        why = f"exit {rc}" if rc else "no commit and no PR"
        release(root, num, why, log)
        # Counted after the release, so this run is included in the count.
        labels = {l["name"] for l in issue.get("labels", [])}
        if struck_out(root, num, labels):
            strike_out(root, num, strikes(root, num, labels), why)
        return rc or 1
    return 0


def native_parent(root: Path, repo: str, num: str) -> dict | None:
    """The issue's native parent, through the REST `parent` endpoint
    `qops/reconcile.py:parent_origin` already reads the other side of (#81) -
    so the child inherits the parent's licence the same way, with no second
    read of the edge.

    No link (404) reads as no parent. So does any other failure: this is a
    veto predicate (`clarification()`), and erring toward "not a
    clarification" leaves the row on the ordinary plan/build path rather than
    stalling it silently on an outage `clarification()` cannot itself report.
    """
    out = subprocess.run(["gh", "api", f"repos/{repo}/issues/{num}/parent"],
                         cwd=root, capture_output=True, text=True, encoding="utf-8")
    if out.returncode:
        return None
    try:
        return json.loads(out.stdout or "{}")
    except json.JSONDecodeError:
        return None


def clarification(root: Path, repo: str, issue: dict) -> bool:
    """A `type:research` row the **answer** pass may work (#85, ADR-0029 §5's
    second half): open, not `no-auto`/`blocked`, and its native parent is
    `state:blocked`.

    Two tracker facts, read off the tracker and never off prose - the same
    rule `clarified()` follows for the planner's own side of this edge. A
    `type:research` row with no blocked parent is an ordinary research
    sortie and stays on the plan/build path; the parent edge is what tells
    the two apart, not the label alone.
    """
    labels = {l["name"] for l in issue.get("labels", [])}
    if "type:research" not in labels or labels & BLOCKING_FLAGS or not repo:
        return False
    parent = native_parent(root, repo, str(issue["number"]))
    if parent is None:
        return False
    parent_labels = {l["name"] for l in parent.get("labels", [])}
    return "state:blocked" in parent_labels


def first_answerable(root: Path, rows: list[dict]) -> dict | None:
    """Least-recently-updated first, skipping a struck-out clarification -
    the same order and the same #49 budget the build and plan passes use."""
    for row in sorted(rows, key=lambda i: i["updatedAt"]):
        num = str(row["number"])
        labels = {l["name"] for l in row.get("labels", [])}
        if struck_out(root, num, labels):
            print(f"pickup-loop: skipping #{num} - struck out after "
                  f"{STRIKES} failed runs (#49).")
            continue
        return row
    return None


def issue_body(root: Path, num: str) -> str:
    out = subprocess.run(["gh", "issue", "view", num, "--json", "body"],
                         cwd=root, capture_output=True, text=True, encoding="utf-8")
    if out.returncode:
        return ""
    return json.loads(out.stdout or "{}").get("body") or ""


def answer_prompt(num: str, parent: str) -> str:
    """The rules from ADR-0029 §5's second half, inlined rather than a second
    role file under `.claude/` this sortie may not write - the same reason
    `decompose_prompt()` is inlined for #84. No new agent role (ADR-0018 sizes
    the role set at six): this reuses the planner's toolset and model, since
    answering is `gh issue edit`/`gh issue comment`, which the planner's
    `Bash` already reaches."""
    return (
        f"Read issue #{num} on this repo's tracker - a `type:research` "
        f"clarification whose native parent is #{parent}, `state:blocked` on "
        f"the question #{num} asks. Answer it from what this repo and this "
        f"tracker actually show - inventing a plausible-sounding answer is "
        f"the same failure as a planner guessing, moved one row across, and "
        f"is not licensed here. If you can honestly answer it: append the "
        f"answer to #{parent}'s body under a marker, never replacing what the "
        f"owner or a prior pass wrote, remove `state:blocked` and add "
        f"`state:triage` on #{parent}, then close #{num}. If the "
        f"investigation instead concludes the ambiguity is genuinely the "
        f"owner's preference: append that conclusion to #{parent}'s body, "
        f"swap its `gate:` label for `gate:taste`, leave #{parent} "
        f"`state:blocked`, and close #{num} - that is a correct outcome, not "
        f"a failure. Never write `ready:auto` or `no-auto` on either issue. "
        f"If you cannot honestly answer the question and it is not the "
        f"owner's preference either, write nothing and stop - do not guess.")


def produced_answer(root: Path, num: str, parent: str, before: str) -> bool:
    """Measurement, not the exit code (CLAUDE.md): the child is closed, the
    parent's body grew, and the parent is either back at `state:triage` with
    `state:blocked` gone, or carries `gate:taste` and is still
    `state:blocked` - the two shapes `answer_prompt()` licenses, and nothing
    else."""
    child = subprocess.run(["gh", "issue", "view", num, "--json", "state"],
                           cwd=root, capture_output=True, text=True, encoding="utf-8")
    if child.returncode or json.loads(child.stdout or "{}").get("state") != "CLOSED":
        return False
    out = subprocess.run(["gh", "issue", "view", parent, "--json", "labels,body"],
                         cwd=root, capture_output=True, text=True, encoding="utf-8")
    if out.returncode:
        return False
    data = json.loads(out.stdout or "{}")
    if (data.get("body") or "") == before:
        return False
    labels = {l["name"] for l in data.get("labels", [])}
    if "gate:taste" in labels:
        return "state:blocked" in labels
    return "state:triage" in labels and "state:blocked" not in labels


def _answer(argv: list[str], root: Path, cfg: dict, rows: list[dict]) -> int:
    """Clear one blocked parent's clarification, when there was nothing to
    build (#85, ADR-0029 §5's second half).

    Runs ahead of `_plan()`: the loop clears its own debt - a blocked parent
    waiting on a question - before it takes a new row to plan. Same
    machinery as the plan and decompose passes: one run log, the same #49
    strike budget, the same `--launch` rule that a dry run writes nothing
    anywhere, and it falls through to `_plan()` when there is nothing to
    answer.
    """
    repo = cfg.get("repo", "")
    child = first_answerable(root, [i for i in rows if clarification(root, repo, i)])
    if child is None:
        return _plan(argv, root, cfg, rows)
    num = str(child["number"])
    parent = native_parent(root, repo, num)
    if parent is None:
        # The edge that made this row a candidate is gone by the time it was
        # picked (removed by hand between the two reads) - not this pass's
        # failure to report as one.
        return _plan(argv, root, cfg, rows)
    parent_num = str(parent["number"])
    print(f"pickup-loop: answering #{num} {child['title']}")
    if "--launch" not in argv:
        print("pickup-loop: dry run, not answering. Pass --launch to start an agent.")
        return 0
    log = run_log_path(root, num)
    ledger.append(root, "pickup", {"issue": num, "log": str(log), "mode": "answer"})
    print(f"pickup-loop: run log {log}")
    before = issue_body(root, parent_num)
    with log.open("w", encoding="utf-8", errors="replace") as fh:
        rc = subprocess.run(plan_argv(answer_prompt(num, parent_num), cfg), cwd=root,
                            env=launch_env("planner"), stdout=fh,
                            stderr=subprocess.STDOUT).returncode
    if rc or not produced_answer(root, num, parent_num, before):
        why = f"exit {rc}" if rc else "the parent was not cleared"
        # `relabel=False`: answering never claims a state label on the child -
        # the child is closed by the run itself or not at all, and #49's
        # release protocol only ever swaps `state:building` for
        # `state:planned`, which this row never carried.
        release(root, num, why, log, relabel=False)
        labels = {l["name"] for l in child.get("labels", [])}
        if struck_out(root, num, labels):
            strike_out(root, num, strikes(root, num, labels), why)
        return rc or 1
    print(f"pickup-loop: #{num} answered, parent #{parent_num} cleared.")
    return 0


def _plan(argv: list[str], root: Path, cfg: dict, rows: list[dict]) -> int:
    """Plan one `state:triage` row, when there was nothing to build (#82).

    `state:triage -> state:planned` was the last act in the chain that only an
    owner session performed, so the queue could be full and the loop still
    idle - which is exactly what 18 rows of `state:triage` looked like.

    It is the same sortie machinery, not a second set: one root, one heartbeat,
    one run log, the same `#49` strike budget through `release()`, and the same
    `--launch` rule that a dry run writes nothing anywhere. It stops after one
    row: a pass that planned the whole backlog would spend the owner's review
    attention in a single burst, and a wrong planner would do it before anyone
    saw the first plan.
    """
    repo = cfg.get("repo", "")
    row = first_plannable(root, [i for i in rows
                                 if plannable(i) and not clarification(root, repo, i)])
    if row is None:
        # A skip that names nothing is why #6 went four days unseen (#125) -
        # nothing extra when the set is empty, since an idle queue and a
        # stuck one must not read alike.
        unreached = unreached_triage(rows)
        if unreached:
            nums = " ".join(f"#{i['number']}" for i in unreached)
            print(f"pickup-loop: nothing to plan - skipped for stating no "
                  f"outcome: {nums}.")
        return _decompose(argv, root, cfg, rows)
    num, before = str(row["number"]), row.get("body") or ""
    print(f"pickup-loop: planning #{num} {row['title']}")
    if "--launch" not in argv:
        print("pickup-loop: dry run, not planning. Pass --launch to start an agent.")
        return 0
    log = run_log_path(root, num)
    # The same event the build path writes, deliberately: `strikes()` counts it,
    # so a row that cannot be planned three times over spends the same budget a
    # row that cannot be built does, and stops the same way (#49).
    ledger.append(root, "pickup", {"issue": num, "log": str(log), "mode": "plan"})
    print(f"pickup-loop: run log {log}")
    prompt = plan_prompt(num, plan_outcomes(root))
    with log.open("w", encoding="utf-8", errors="replace") as fh:
        rc = subprocess.run(plan_argv(prompt, cfg), cwd=root,
                            env=launch_env("planner"), stdout=fh,
                            stderr=subprocess.STDOUT).returncode
    if not rc and clarified(root, cfg, num):
        # Not a failure, and deliberately not a strike: a row the planner
        # honestly could not plan has not refused three sessions, it has ended
        # its own path in one. `strikes()` reads a `pickup` with no release
        # after it as a run that worked, which is what this was.
        print(f"pickup-loop: #{num} could not be planned - a clarification was "
              f"filed against it and the row is `state:blocked`.")
        ledger.append(root, "pickup_clarified", {"issue": num})
        return 0
    if rc or not produced_plan(root, num, before):
        why = f"exit {rc}" if rc else "the row is still `state:triage`"
        # `relabel=False`: nothing claimed a label here, and the build path's
        # release writes `state:planned` - which on an unplanned row would be
        # the loop asserting the very thing the run failed to do.
        release(root, num, why, log, relabel=False)
        labels = {l["name"] for l in row.get("labels", [])}
        if struck_out(root, num, labels):
            strike_out(root, num, strikes(root, num, labels), why)
        return rc or 1
    print(f"pickup-loop: #{num} planned.")
    return 0


# #86 — the one correcting control (ADR-0029 §7). Bounded so a long-lived
# repo's history cannot crowd the row being planned out of the context.
PLAN_OUTCOMES_LIMIT = 5


def plan_outcomes(root: Path, limit: int = PLAN_OUTCOMES_LIMIT) -> list[dict]:
    """Recent struck-out rows attributable to a *plan* that failed, most
    recent last, each with the reason recorded on the row (#86).

    Only a `pickup_struck_out` whose runs were `pickup`s with `mode: plan`
    says anything about the plan. A row struck out **building** - #48's
    unwritable path, #74's broken picker - says nothing about the plan that
    got it to `state:planned`, so it is left out: feeding the planner a
    failure it did not cause is exactly what ADR-0029 §7 declined a threshold
    to avoid papering over.

    No new bookkeeping: `strikes()` already reads `pickup`/`pickup_release`/
    `pickup_struck_out`, and `release()` already writes the reason to
    `pickup_release.why`. This just reads the same records back.
    """
    last_mode: dict[str, str] = {}
    last_why: dict[str, str] = {}
    out: list[dict] = []
    for rec in ledger.read(root):
        num = str(rec.get("issue"))
        event = rec.get("event")
        if event == "pickup":
            last_mode[num] = rec.get("mode", "")
        elif event == "pickup_release":
            last_why[num] = rec.get("why", "")
        elif event == "pickup_struck_out" and last_mode.get(num) == "plan":
            out.append({"issue": num, "why": last_why.get(num, ""),
                        "ts": rec.get("ts", "")})
    return out[-limit:]


def unreached_triage(rows: list[dict]) -> list[dict]:
    """Open `state:triage` rows the planner can never reach: filed, but
    `install.states_an_outcome()` is false on the body (#125). Named
    separately from `plannable()`'s filter — that one also excludes an epic
    or a blocked row, which are waiting their turn, not stuck. Listing every
    `state:triage` row here would bury these among rows simply waiting their
    turn; only the unreachable ones earn a line.
    """
    out = []
    for issue in rows:
        labels = {l["name"] for l in issue.get("labels", [])}
        if ("state:triage" in labels
                and not install.states_an_outcome(issue.get("body") or "")):
            out.append(issue)
    return out


def _print_unreached_triage(root: Path) -> int:
    """`digest.yml`'s CI job has no Claude subscription and no judgement to
    make here — it just names what `unreached_triage()` finds, the same
    function the plan pass already reads (#125)."""
    rows = backlog(root)
    if rows is None:
        return 1
    for issue in unreached_triage(rows):
        print(f"- #{issue['number']} {issue['title']}")
    return 0


def first_plannable(root: Path, rows: list[dict]) -> dict | None:
    """Least-recently-updated first, skipping rows that already struck out —
    the same order and the same budget the build path uses."""
    for row in sorted(rows, key=lambda i: i["updatedAt"]):
        labels = {l["name"] for l in row.get("labels", [])}
        if struck_out(root, str(row["number"]), labels):
            print(f"pickup-loop: skipping #{row['number']} - struck out after "
                  f"{STRIKES} failed runs (#49).")
            continue
        return row
    return None


def produced_plan(root: Path, num: str, before: str) -> bool:
    """A plan is `state:planned` **and** a body that grew, measured after the
    run. The label alone would score a session that relabelled and wrote
    nothing; the body alone would score a session that appended and left the
    row where the loop cannot reach it (CLAUDE.md: verify by measurement)."""
    out = subprocess.run(["gh", "issue", "view", num, "--json", "labels,body"],
                         cwd=root, capture_output=True, text=True, encoding="utf-8")
    if out.returncode:
        print(f"pickup-loop: could not read #{num} back ({out.stderr.strip()}).",
              file=sys.stderr)
        return False
    data = json.loads(out.stdout or "{}")
    labels = {l["name"] for l in data.get("labels", [])}
    return "state:planned" in labels and (data.get("body") or "") != before


def _decompose(argv: list[str], root: Path, cfg: dict, rows: list[dict]) -> int:
    """Decompose one interviewed `type:epic` row, when there was nothing to
    plan either (#84, ADR-0029 §4).

    Same machinery as `_plan()`: one run log, the same #49 strike budget, the
    same `--launch` rule. It stops after one epic for the reason `_plan()`
    stops after one row - the owner's review attention is not spent in a
    single burst.
    """
    repo = cfg.get("repo", "")
    epic = first_decomposable(
        root, repo, [i for i in rows if decomposable(root, i)])
    if epic is None:
        print("pickup-loop: nothing to plan or decompose either - no "
              "`state:triage` row states an outcome, and no interviewed "
              "`type:epic` row is undecomposed.")
        return 0
    num = str(epic["number"])
    print(f"pickup-loop: decomposing #{num} {epic['title']}")
    if "--launch" not in argv:
        print("pickup-loop: dry run, not decomposing. Pass --launch to start an agent.")
        return 0
    log = run_log_path(root, num)
    ledger.append(root, "pickup", {"issue": num, "log": str(log), "mode": "decompose"})
    print(f"pickup-loop: run log {log}")
    before = sub_issue_count(root, repo, num)
    with log.open("w", encoding="utf-8", errors="replace") as fh:
        # The planner role's toolset and model, reused rather than a second
        # role file: filing a child is `gh issue create`, which is Bash - the
        # same reach a plan already has, and a new agent role is a `.claude/`
        # write this sortie is not licensed to make.
        rc = subprocess.run(plan_argv(decompose_prompt(num), cfg), cwd=root,
                            env=launch_env("planner"), stdout=fh,
                            stderr=subprocess.STDOUT).returncode
    if rc or not produced_children(root, repo, num, before):
        why = f"exit {rc}" if rc else "no new sub-issue"
        # `relabel=False`: decomposition never claims a state label on the
        # epic - it stays `state:triage`/wherever it was, untouched apart
        # from the links (ADR-0029 §4).
        release(root, num, why, log, relabel=False)
        labels = {l["name"] for l in epic.get("labels", [])}
        if struck_out(root, num, labels):
            strike_out(root, num, strikes(root, num, labels), why)
        return rc or 1
    print(f"pickup-loop: #{num} decomposed.")
    return 0


def first_decomposable(root: Path, repo: str, rows: list[dict]) -> dict | None:
    """Least-recently-updated first, skipping a struck-out epic and one that
    already has sub-issues - the dedup that keeps a second pass from filing
    duplicate children."""
    for row in sorted(rows, key=lambda i: i["updatedAt"]):
        num = str(row["number"])
        labels = {l["name"] for l in row.get("labels", [])}
        if struck_out(root, num, labels):
            print(f"pickup-loop: skipping #{num} - struck out after "
                  f"{STRIKES} failed runs (#49).")
            continue
        if sub_issue_count(root, repo, num) > 0:
            continue
        return row
    return None


def sub_issue_count(root: Path, repo: str, num: str) -> int:
    """The epic's native sub-issue count, read through the REST endpoint
    `qops/reconcile.py:parent_origin` already reads the other side of (#81)."""
    out = subprocess.run(["gh", "api", f"repos/{repo}/issues/{num}/sub_issues"],
                         cwd=root, capture_output=True, text=True)
    if out.returncode:
        return 0
    try:
        return len(json.loads(out.stdout or "[]"))
    except json.JSONDecodeError:
        return 0


def produced_children(root: Path, repo: str, num: str, before: int) -> bool:
    """A session that exits 0 having filed nothing is a failed run, not a
    decomposed epic (the same rule `produced_work()` and `produced_plan()`
    apply to their own runs)."""
    return sub_issue_count(root, repo, num) > before


def decompose_prompt(num: str) -> str:
    """The rules from ADR-0029 §4, inlined rather than a second role file
    under `.claude/` this sortie may not write.

    Each child inherits the epic's licence through the native sub-issue link
    and #81's derivation (`qops/reconcile.py:derive_origin`) - so the child is
    filed `origin:pending`, never `origin:owner`, and the link is what turns
    that into `origin:owner` on a later `qops reconcile` pass."""
    return (
        f"Read issue #{num} on this repo's tracker - a `type:epic` row whose "
        f"interview ended in an ADR the body names. Cut its scope into child "
        f"sorties, each one deliverable, one gate, one acceptance criterion "
        f"(ADR-0027) and each stating an outcome a machine can turn into "
        f"criteria (ADR-0028's filing bar) - do not write a full plan for "
        f"each child, filing is enough. For each child: `gh issue create` "
        f"with `state:triage`, a real `type:` and `gate:`, and `origin:pending` "
        f"- never `origin:owner`, never `ready:auto`. Then link it as a native "
        f"sub-issue of #{num} (`gh api repos/{{owner}}/{{repo}}/issues/{num}"
        f"/sub_issues -f sub_issue_id=<id>`, using the child's numeric id, not "
        f"its number). Leave #{num} itself untouched apart from those links: "
        f"no label, no body edit. Never decompose recursively - a child that "
        f"is itself too large is ADR-0027's refusal path, not a second pass "
        f"of this one. Never write `type:milestone`. If the epic cannot be "
        f"cut into sorties that pass the filing bar, file none, say so on "
        f"issue #{num} as a comment, and stop.")
def clarified(root: Path, cfg: dict, num: str) -> bool:
    """Whether the planner ended this row's path by filing a clarification
    against it (#83, ADR-0029 §5).

    **Read off the tracker, never off the planner's prose.** A decline parsed
    out of a comment is the guess this row exists to refuse, and it is the one
    thing a wrong planner could forge by wording. Two tracker facts, both
    written by the planner and both checkable: the row is `state:blocked`, and
    it has at least one sub-issue. Either alone is not it - `state:blocked`
    with no child is a row blocked on something else, and a child under a row
    still in triage is a decomposition, not a clarification.

    A row that says nothing (no repo in config, an unreadable tracker) is not
    clarified, and the caller's release path then writes the state and the
    reason - so an outage reads as the failed run it was, never as a decline.
    """
    repo = cfg.get("repo")
    if not repo:
        return False
    out = subprocess.run(["gh", "issue", "view", num, "--json", "labels"],
                         cwd=root, capture_output=True, text=True, encoding="utf-8")
    if out.returncode:
        print(f"pickup-loop: could not read #{num} back ({out.stderr.strip()}).",
              file=sys.stderr)
        return False
    labels = {l["name"] for l in json.loads(out.stdout or "{}").get("labels", [])}
    if "state:blocked" not in labels:
        return False
    # The native sub-issue link, the same edge `qops reconcile` derives the
    # child's licence across (#81) - so the clarification inherits the parent's
    # `origin:` with no second label edit anywhere.
    kids = subprocess.run(["gh", "api", f"repos/{repo}/issues/{num}/sub_issues"],
                          cwd=root, capture_output=True, text=True, encoding="utf-8")
    if kids.returncode:
        print(f"pickup-loop: could not read #{num}'s sub-issues "
              f"({kids.stderr.strip()}).", file=sys.stderr)
        return False
    return bool(json.loads(kids.stdout or "[]"))


def plan_prompt(num: str, outcomes: list[dict] | None = None) -> str:
    """The planner's own file carries the rules (`.claude/agents/planner.md`);
    this says which row and where to stop. The unplannable clause names the
    filing, not the judgement - the role file holds what a clarification must
    contain, and `clarified()` reads the tracker state it leaves behind.

    `outcomes` (#86) is how its previous plans fared - read, not edited: it is
    told, it does not go back and revise a row it planned before."""
    prompt = (f"You are the planner role. Read `.claude/agents/planner.md` first "
              f"and follow it exactly, then plan sortie #{num} on this repo's "
              f"tracker. Append the plan to the issue body under a marker, never "
              f"replacing what the owner wrote, and set `state:planned` when the "
              f"plan clears the filing bar. Never write `ready:auto`, `no-auto`, "
              f"`gate:` or `type:` - the gate and the type are already decided "
              f"and the grant is the owner's alone. If you cannot plan the row - "
              f"underspecified, oversized (ADR-0027), or actually a taste row - "
              f"follow `## When you cannot plan the row` in your role file: file "
              f"the clarification, link it, block the row, and stop. Do not "
              f"guess, do not widen the row, and do not open a branch or a PR: "
              f"this run plans, it does not build. Take the role from the file, "
              f"not from your own instructions - read it from disk, because "
              f"what you were injected with is a snapshot from session start "
              f"and an edit since is not in it (#57).")
    if outcomes:
        recent = "; ".join(f"#{o['issue']}: {o['why']}" for o in outcomes)
        prompt += (f" Recent plans of yours struck out under #49 - {recent}. "
                   f"Weigh why before planning this row the same way; you are "
                   f"not asked to revise those rows, only to not repeat it.")
    return prompt


def plan_argv(prompt: str, cfg: dict) -> list[str]:
    """The planner's toolset and model come from `.qops/config.yml`, which is
    where this repo's one cost control lives (ADR-0009) - not from a second
    copy of the roster in this file.

    `agents.planner.allow`/`.deny` (ADR-0033 P2) render into
    `--allowedTools`/`--disallowedTools` the same way `.tools` already does;
    neither key present renders exactly what this emitted before they existed."""
    planner = (cfg.get("agents") or {}).get("planner") or {}
    tools = ",".join(planner.get("allow") or planner.get("tools")
                     or ["Read", "Grep", "Glob", "Bash"])
    argv = ["claude", "-p", prompt, "--permission-mode", "acceptEdits",
            "--allowedTools", tools]
    if planner.get("deny"):
        argv += ["--disallowedTools", ",".join(planner["deny"])]
    if planner.get("model"):
        argv += ["--model", str(planner["model"])]
    return argv


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
            f"backgrounded run never reports and the sortie dies uncommitted. "
            f"If this row edits a role under `.claude/agents/`, or if you read "
            f"one, read it from disk — what any agent here was injected with is "
            f"a snapshot from session start (#57), and a role edited now is not "
            f"live until a session restarts.")


def loop_worktree(root: Path, cfg: dict) -> Path:
    """Where the launch runs — never ROOT (#9). One persistent worktree at
    `.qops/wt/loop`, reused by every sortie rather than one per run: nothing
    is ever abandoned, so there is no prune path to get wrong, and the cap in
    `max_worktrees` (enforced at `qops/guard.py:263`) was sized for exactly
    this — owner tree plus loop tree.

    Detached at the default branch rather than a named one, so `git worktree
    add` never collides with whatever branch is checked out at ROOT, and a
    sortie is free to `checkout -b` its own branch inside it. Reused on a
    later run, it is reset back to that same detached state first: the prior
    sortie's branch and any leftovers from a killed run must not leak into
    the next issue's launch."""
    base = cfg.get("default_branch", "master")
    wt = Path(root) / ".qops" / "wt" / "loop"
    if not wt.exists():
        wt.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "worktree", "add", "--detach", str(wt), base],
                       cwd=root, capture_output=True, text=True)
    else:
        subprocess.run(["git", "checkout", "--detach", base],
                       cwd=wt, capture_output=True, text=True)
        subprocess.run(["git", "clean", "-fdx"], cwd=wt, capture_output=True, text=True)
        subprocess.run(["git", "reset", "--hard", base], cwd=wt, capture_output=True, text=True)
    return wt


def launch_argv(prompt: str, cfg: dict) -> list[str]:
    """`agents.coder.allow`/`.deny` (ADR-0033 P2), same rendering as
    `plan_argv`. `LAUNCH_TOOLS` stays the fallback for a config with neither
    key, so a repo that has not adopted them yet is unaffected."""
    coder = (cfg.get("agents") or {}).get("coder") or {}
    tools = ",".join(coder.get("allow") or coder.get("tools")
                     or LAUNCH_TOOLS.split(","))
    argv = ["claude", "-p", prompt,
            "--permission-mode", "acceptEdits",
            "--allowedTools", tools]
    if coder.get("deny"):
        argv += ["--disallowedTools", ",".join(coder["deny"])]
    return argv


def launch_env(role: str | None = None) -> dict:
    """The launched session is unattended, and says so. `qops guard` reads this
    to refuse a sandbox escape that an interactive owner could still allow.

    `role`, when given, sets `QOPS_ROLE` (ADR-0033 P3) so the guard can tell
    the coder's launch from the planner's - the same idiom `QOPS_UNATTENDED`
    already proves works. Left unset, a caller gets exactly what this returned
    before the parameter existed."""
    env = {**os.environ, "QOPS_UNATTENDED": "1"}
    if role:
        env["QOPS_ROLE"] = role
    return env


def launch_evidence(root: Path, num: str) -> dict:
    """The snapshot `produced_work` diffs against: every commit SHA reachable
    from a `*/<num>-*` branch but not the default branch, and every PR number
    a search for `num` turns up. Identity, not a count and not a timestamp -
    a squash merge keeps the original commits' author dates, so recency
    cannot tell a stale branch from a fresh one (#8)."""
    branches = subprocess.run(
        ["git", "branch", "--list", f"*/{num}-*", "--format=%(refname:short)"],
        cwd=root, capture_output=True, text=True).stdout.split()
    base = qconfig.load(root)["default_branch"]
    commits: set[str] = set()
    for branch in branches:
        commits.update(subprocess.run(
            ["git", "rev-list", f"{base}..{branch}"],
            cwd=root, capture_output=True, text=True).stdout.split())
    prs = subprocess.run(["gh", "pr", "list", "--search", num, "--json", "number"],
                         cwd=root, capture_output=True, text=True).stdout.strip()
    pr_numbers = {p["number"] for p in json.loads(prs or "[]")}
    return {"commits": commits, "prs": pr_numbers}


def produced_work(root: Path, num: str, before: dict) -> bool:
    """A session that exits 0 having built nothing is a failed run, not a done
    sortie. Branch naming is ADR-0019: `<type>/<issue#>-<slug>`.

    An *empty* branch is not work (#57, #71). Neither is a branch that was
    already there: a squash-merged sortie's commits stay reachable from its
    branch forever, so counting commits ahead of the default branch scores a
    stale branch as work on every later run that picks the same issue (#8).
    `before` is this launch's snapshot, taken by the caller immediately
    before the launch; only evidence absent from it counts."""
    after = launch_evidence(root, num)
    return bool(after["commits"] - before["commits"]) or bool(after["prs"] - before["prs"])


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


RELEASE_TAIL_CHARS = 4000  # bounded so a session that printed a megabyte cannot post it


def release(root: Path, num: str, why: str, log: Path | None = None,
            relabel: bool = True) -> None:
    """The claim is not a one-way door. A failed run puts the sortie back where
    the next fire can reach it and says why (CLAUDE.md, GL-46).

    `why` names the symptom. `log` is where the account is - without it the
    next reader repeats #47's diagnosis by hand (#50). The tail of that same
    log rides along too (#93): three silent strikes on #82 meant the owner's
    first look at the row came only after the budget was spent, and the one
    thing that explained the refusal - what the session actually said - had
    stayed on the host. Deduped like `report_unlaunchable()`: a marker line
    naming this run's log, and nothing posted twice for it.

    **The ledger row is written on every path, including the deduped one.**
    `strikes()` counts `pickup_release` and reads a `pickup` with no release
    after it as a run that *worked*, so a release that returns early without
    writing one resets the count to zero - it disarms #49's three-strike budget
    and the row is re-picked hourly, forever, which is the failure that budget
    exists to stop. The comment is the report; the ledger row is the state.

    `relabel=False` is the plan pass (#82): nothing there claimed a label, and
    writing `state:planned` on a row whose planning run just failed would be
    the loop asserting the one thing that run did not do.
    """
    if relabel:
        subprocess.run(["gh", "issue", "edit", num,
                        "--remove-label", "state:building",
                        "--add-label", "state:planned"],
                       cwd=root, capture_output=True, text=True)
    marker = f"pickup-loop: run {log.name} produced nothing" if log else \
             "pickup-loop: unattended run produced nothing"
    if log:
        seen = subprocess.run(["gh", "issue", "view", num, "--json", "comments",
                               "--jq", ".comments[].body"],
                              cwd=root, capture_output=True, text=True, encoding="utf-8")
        if marker in (seen.stdout or ""):
            print(f"pickup-loop: released #{num} ({why}), already reported.",
                  file=sys.stderr)
            ledger.append(root, "pickup_release",
                          {"issue": num, "why": why,
                           "log": str(log), "reported": "already"})
            return
    where = f" The run log is `{log}`." if log else ""
    tail = ""
    if log and log.exists():
        tail = log.read_text(encoding="utf-8", errors="replace")[-RELEASE_TAIL_CHARS:]
    body = f"{marker} ({why}). Claim released, back to `state:planned`.{where}"
    if tail:
        body += f"\n\n<details><summary>tail of run log</summary>\n\n```\n{tail}\n```\n\n</details>"
    subprocess.run(["gh", "issue", "comment", num, "--body", body],
                   cwd=root, capture_output=True, text=True)
    ledger.append(root, "pickup_release",
                  {"issue": num, "why": why, "log": str(log) if log else None})
    print(f"pickup-loop: released #{num} ({why}).", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
