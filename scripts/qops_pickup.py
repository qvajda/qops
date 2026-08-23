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
import re
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
        cwd=root, capture_output=True, text=True)
    if out.returncode:
        print(out.stderr.strip(), file=sys.stderr)
        return None
    return json.loads(out.stdout or "[]")


def candidates(root: Path) -> list[dict] | None:
    """The rows the loop may *build*: `install.eligible()` over the backlog."""
    rows = backlog(root)
    return None if rows is None else [i for i in rows if eligible(i)]


def plannable(issue: dict) -> bool:
    """The rows the loop may *plan* (#82, ADR-0029 §1).

    `state:triage` and nothing else: planning is the act that leaves triage, so
    a row anywhere else has already had it. The filing bar is the gate — a row
    whose body states no outcome cannot be planned into criteria, and guessing
    at one is how a plan invents work the owner never licensed (ADR-0028).

    `type:epic` is refused here rather than planned badly: an epic is where
    direction only the owner holds gets set, so it gets an interview and then
    #84's decomposition, never a plan instead of one (ADR-0029 §4).

    `no-auto` and `blocked` veto planning for the same reason they veto
    building — the flag says the owner is handling this one.
    """
    labels = {l["name"] for l in issue.get("labels", [])}
    if "state:triage" not in labels or "type:epic" in labels:
        return False
    if labels & BLOCKING_FLAGS:
        return False
    return install.states_an_outcome(issue.get("body") or "")


# ADR-0029 §4: the interview stays the owner's; what happens under it does not.
# The trigger for decomposition must be a fact on the row, not an assumption
# that a `type:epic` row was interviewed just because it exists. The interview
# skill's own rule is that it "ends in something written down" - an ADR, for a
# Mission-routed row - so a reference to an ADR that actually exists on disk is
# that fact, checked mechanically rather than inferred from a label an
# unattended session could apply to itself.
ADR_REF = re.compile(r"docs/adr/\d+-[\w-]+\.md")


def interviewed(root: Path, issue: dict) -> bool:
    """The epic's body names an ADR file that exists in this repo."""
    for m in ADR_REF.finditer(issue.get("body") or ""):
        if (Path(root) / m.group(0)).exists():
            return True
    return False


def decomposable(root: Path, issue: dict) -> bool:
    """The rows the loop may *decompose* (#84, ADR-0029 §4).

    `type:epic` and interviewed, same veto flags as `plannable()` - `no-auto`
    and `blocked` still mean the owner is handling this one.
    """
    labels = {l["name"] for l in issue.get("labels", [])}
    if "type:epic" not in labels or labels & BLOCKING_FLAGS:
        return False
    return interviewed(root, issue)


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


def strikes(root: Path, num: str, labels: frozenset[str] = frozenset(),
            now: str | None = None) -> int:
    """Consecutive failed runs on this row, most recent last.

    Consecutive, not cumulative: a `pickup` with no `pickup_release` after it
    is a run that worked, and it resets the count. The off-by-one here fails
    open - it keeps burning sessions - so the interleaved case is the one the
    tests lean on. A `pickup_skip` (#48) is not a strike: nothing was spent and
    no session ever attempted the row.

    `no-auto` absent means the owner cleared a prior strike-out (#99): the
    count then starts after the last `pickup_struck_out` event rather than
    from the top of the ledger, so the row gets a fresh budget instead of
    reading as struck out for the rest of `STRIKE_WINDOW_DAYS`. While
    `no-auto` is on the row, nothing has been cleared and the full history
    still counts.
    """
    cutoff = (datetime.fromisoformat(now) if now else datetime.now(timezone.utc)
              ) - timedelta(days=STRIKE_WINDOW_DAYS)
    records = [rec for rec in ledger.read(root) if str(rec.get("issue")) == str(num)]
    if "no-auto" not in labels:
        last_strike_out = max(
            (i for i, rec in enumerate(records) if rec.get("event") == "pickup_struck_out"),
            default=None)
        if last_strike_out is not None:
            records = records[last_strike_out + 1:]
    count, open_attempt = 0, False
    for rec in records:
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


def struck_out(root: Path, num: str, labels: frozenset[str] = frozenset(),
               now: str | None = None) -> bool:
    return strikes(root, num, labels, now) >= STRIKES


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
    rows = backlog(root)
    if rows is None:
        print("pickup-loop: could not read the backlog - nothing was picked and "
              "the queue state is UNKNOWN, which is not the same as empty.",
              file=sys.stderr)
        return 1
    picks = [i for i in rows if eligible(i)]
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
        return _plan(argv, root, cfg, rows)
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
        rc = subprocess.run(launch_argv(launch_prompt(num)), cwd=launch_cwd,
                            env=launch_env(), stdout=fh,
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
    row = first_plannable(root, [i for i in rows if plannable(i)])
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
                            env=launch_env(), stdout=fh,
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
                            env=launch_env(), stdout=fh,
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
              f"this run plans, it does not build.")
    if outcomes:
        recent = "; ".join(f"#{o['issue']}: {o['why']}" for o in outcomes)
        prompt += (f" Recent plans of yours struck out under #49 - {recent}. "
                   f"Weigh why before planning this row the same way; you are "
                   f"not asked to revise those rows, only to not repeat it.")
    return prompt


def plan_argv(prompt: str, cfg: dict) -> list[str]:
    """The planner's toolset and model come from `.qops/config.yml`, which is
    where this repo's one cost control lives (ADR-0009) - not from a second
    copy of the roster in this file."""
    planner = (cfg.get("agents") or {}).get("planner") or {}
    tools = ",".join(planner.get("tools") or ["Read", "Grep", "Glob", "Bash"])
    argv = ["claude", "-p", prompt, "--permission-mode", "acceptEdits",
            "--allowedTools", tools]
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
            f"backgrounded run never reports and the sortie dies uncommitted.")


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


def launch_argv(prompt: str) -> list[str]:
    return ["claude", "-p", prompt,
            "--permission-mode", "acceptEdits",
            "--allowedTools", LAUNCH_TOOLS]


def launch_env() -> dict:
    """The launched session is unattended, and says so. `qops guard` reads this
    to refuse a sandbox escape that an interactive owner could still allow."""
    return {**os.environ, "QOPS_UNATTENDED": "1"}


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
