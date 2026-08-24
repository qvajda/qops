"""The local guard. PreToolUse exit 2 blocks a call outright (ADR-0001).

Not a security control - an agent can run with hooks disabled. It is the local
half of a pair whose other half is server-side branch protection (PRD §5 B8).
The tripwire list lives in .qops/config.yml and is read here AND by guard.yml,
so there is one definition with two enforcement points.

**Everything below reads argv, never the command string** (ADR-0021, #168). The
string form could not tell `git push` from `git stash push`, could not see
`push` behind `git -c x=y`, read only the last token as the push target, and
matched a git rule quoted inside `--body`. Six checks each did their own
scanning and got it wrong differently. Parse once, decide six times.
"""

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

from . import config, ledger

# git subcommands that write to the current branch
_WRITES = {"commit", "push", "merge", "rebase"}

# git's own options, before the subcommand. These take a value.
_GIT_VALUE_OPTS = {"-c", "-C", "--exec-path", "--git-dir", "--work-tree",
                   "--namespace"}

# What separates one command from the next inside a single Bash call.
_SEPARATORS = {"&&", "||", ";", "|", "&"}

_TEXT_FIELDS = ("content", "new_string", "command", "file_text")

# Flags whose value is prose the caller wrote, not something the shell will run.
# A comment quoting a git rule documents it; it does not break it. The tripwire
# scan has had that exemption since it was written and the git checks did not,
# so the substrate could not state its own git rules through any tool that takes
# prose on the command line (#168).
#
# Long forms only, plus `-m`. Every short form is ambiguous, and dropping the
# token after one hides a ref: to gh, `-b` is the body; to git checkout it is
# the new branch. `-c` carries a whole command and `-d` a ref to delete.
_PROSE_FLAGS = {"-m", "--message", "--body", "--title", "--notes",
                "--description", "--reason"}

_FORCE_FLAGS = ("--force", "--force-with-lease", "--force-if-includes")

# Flags whose value is another command. `bash -c "..."` hides its payload from
# a token scan the same way `--body "..."` hid prose from a string scan; the
# difference is that this one runs. Expanded, not dropped.
_COMMAND_FLAGS = {"-c", "-lc", "-ic", "--command", "/c", "/C"}

# `git push` flags that consume the token after them.
_PUSH_VALUE_FLAGS = {"-o", "--push-option", "--receive-pack", "--exec", "--repo"}


def argv_tokens(cmd: str) -> list[str]:
    """Command tokens, with the values of prose-carrying flags dropped and the
    values of command-carrying flags expanded (#168).

    Unbalanced quotes fall back to a naive split rather than to allowing the
    call: the guard may read less, it never reads nothing.
    """
    try:
        toks = shlex.split(cmd)
    except ValueError:
        return cmd.split()
    out, skip = [], False
    for t in toks:
        if skip:
            skip = False
        elif t in _PROSE_FLAGS:
            skip = True
        elif "=" in t and t.split("=", 1)[0] in _PROSE_FLAGS:
            continue
        else:
            out.append(t)
    for i, t in enumerate(out[1:], 1):
        # `-c` is ambiguous: `bash -c "git push"` carries a command, `git -c
        # key=val` carries a config setting. A payload with no whitespace in it
        # is not a command, so only the former is expanded.
        if out[i - 1] in _COMMAND_FLAGS and any(c.isspace() for c in t):
            out += argv_tokens(t)
    return out


def git_commands(toks: list[str]) -> list[tuple[str, list[str], str | None]]:
    """Every `git <subcommand>` in these tokens, as (subcommand, its own args,
    the value of its own `-C` flag or None).

    The subcommand is the first non-option token after `git`, so `git -c k=v
    push` is a push and `git stash push` is not one. Args stop at the next shell
    separator, so `git commit && git checkout master` does not read `master` as
    an argument to `commit`. A repeated `-C` chains like git's own does
    (`git -C a -C b` means `./a/b`) - #122.
    """
    found = []
    for i, t in enumerate(toks):
        if t != "git":
            continue
        j = i + 1
        cpath = None
        while j < len(toks) and toks[j].startswith("-"):
            if toks[j] == "-C" and j + 1 < len(toks):
                cpath = toks[j + 1] if cpath is None else str(Path(cpath) / toks[j + 1])
            j += 2 if toks[j] in _GIT_VALUE_OPTS else 1
        if j >= len(toks) or toks[j] in _SEPARATORS:
            continue
        args = []
        for a in toks[j + 1:]:
            if a in _SEPARATORS:
                break
            args.append(a)
        found.append((toks[j], args, cpath))
    return found


def checkout_target(verb: str, args: list[str]) -> str | None:
    """The branch a `checkout`/`switch` lands on, read plainly off its args.

    The first token that is not a flag and not a `--` path separator is the
    branch, whether it followed `-b`/`-c`/`-B`/`-C` or stood alone. `switch -`
    (the previous branch) and other relative forms resolve to nothing here -
    skipping them leaves the next comparison honest instead of recording a
    guess (#130).
    """
    if verb not in ("checkout", "switch"):
        return None
    for a in args:
        if a == "--":
            return None
        if a == "-" or a.startswith("-"):
            continue
        return a
    return None


def self_checkout(toks: list[str]) -> str | None:
    """The branch this session's own command would land HEAD on, if any -
    the last `checkout`/`switch` among possibly several chained commands,
    skipping any judged by another root's HEAD (#130)."""
    target = None
    for verb, args, cpath in git_commands(toks):
        if cpath:
            continue
        found = checkout_target(verb, args)
        if found:
            target = found
    return target


def head_moved_refusal(toks: list[str], ctx: dict) -> str | None:
    """HEAD can move under a session with no act of its own - another
    actor's checkout, a rebase in another tool, a script (#130). Before a
    branch-creating or branch-switching command, refuse if HEAD no longer
    matches the branch this session itself last recorded in the ledger.

    A session's own earlier checkout is recorded right here in `hook()`, so
    it is not a divergence - only a move this session did not make is. No
    prior record for this session (`session_branch` absent) allows through:
    a first command has nothing to have diverged from.

    A recorded checkout is written before the command runs - an intent, not
    an observation. If it failed, HEAD never left the branch this session
    recorded just before it (`session_prior_branch`); that is a failed
    checkout, not an external move, and is not refused (#167).
    """
    session_branch = ctx.get("session_branch")
    branch = ctx.get("branch") or ""
    if not session_branch or session_branch == branch:
        return None
    if branch == ctx.get("session_prior_branch"):
        return None
    if not any(verb in ("checkout", "switch") for verb, _, _ in git_commands(toks)):
        return None
    return (f"HEAD moved under this session: last recorded on `{session_branch}`, "
            f"now on `{branch}`. Something else moved the tree - re-read it "
            f"(git status) before branching.")


def issue_filings(toks: list[str]) -> list[list[str]]:
    """The args of every `gh issue create` in these tokens.

    Same parse as `git_commands`, same reason (ADR-0021): decide from a parse,
    not from a regex over the string. `gh issue edit`, `gh issue list` and a
    `--body` that merely mentions the label are not filings.
    """
    found = []
    for i, t in enumerate(toks):
        if t != "gh" or toks[i + 1:i + 3] != ["issue", "create"]:
            continue
        args = []
        for a in toks[i + 3:]:
            if a in _SEPARATORS:
                break
            args.append(a)
        found.append(args)
    return found


def issue_edits(toks: list[str]) -> list[list[str]]:
    """The args of every `gh issue edit` in these tokens - same parse as
    `issue_filings`, same reason (ADR-0021)."""
    found = []
    for i, t in enumerate(toks):
        if t != "gh" or toks[i + 1:i + 3] != ["issue", "edit"]:
            continue
        args = []
        for a in toks[i + 3:]:
            if a in _SEPARATORS:
                break
            args.append(a)
        found.append(args)
    return found


def label_values(args: list[str]) -> set[str]:
    """Every label a `gh` call passes, across repeated and comma-joined flags."""
    out = set()
    for i, a in enumerate(args):
        value = None
        if a in ("--label", "-l") and i + 1 < len(args):
            value = args[i + 1]
        elif a.startswith("--label="):
            value = a.split("=", 1)[1]
        if value:
            out |= {v.strip() for v in value.split(",") if v.strip()}
    return out


def origin_refusal(toks: list[str], ctx: dict) -> str | None:
    """A filing states the `origin:` its own session can honestly claim.

    ADR-0023 makes `origin:` the input to the `ready:auto` grant: on an
    `origin:owner` row the filing itself is the grant. So an unattended run
    that could write `origin:owner` could grant itself autonomy, and the
    prompt saying not to is a preference, not a control (CLAUDE.md). This is
    the control. Absence is refused too - it is the easy way around a check on
    the value, and it would leave `doctor` inferring after the fact, which is
    exactly what the ADR forbids.

    ADR-0029: `origin:pending` is the honest claim for an unattended filing
    that expects a parent link to derive it later — `qops reconcile` does the
    deriving, from the link, never from this claim.
    """
    honest = {"origin:agent", "origin:pending"} if ctx.get("unattended") \
        else {"origin:owner"}
    for args in issue_filings(toks):
        claimed = {l for l in label_values(args) if l.startswith("origin:")}
        if len(claimed) == 1 and claimed <= honest:
            continue
        choices = " or ".join(f"`{h}`" for h in sorted(honest))
        if not claimed:
            return (f"`gh issue create` states no `origin:` label. This session "
                    f"can claim {choices} — pass `--label` (ADR-0023/0029).")
        return (f"this filing claims {', '.join(sorted(claimed))}; this session "
                f"can only claim {choices} (ADR-0023/0029). `origin:` is set by "
                f"which path filed the row, not chosen.")
    return None


def role_refusal(toks: list[str], ctx: dict) -> str | None:
    """ADR-0033's per-role command cells that a tool list cannot express (P3).
    `ctx["role"]` is absent for an attended owner's session, and this returns
    None immediately then - the table's rules are the unattended launch's,
    never the owner's own keyboard.

    Only the cells ADR-0033 names as built here: the scribe never pushes and
    never opens a branch (§4, §5); the planner never commits, branches or adds
    a worktree (§4); the coder reports by comment, never by editing the row's
    labels itself (§6). Every other role/verdict in the table still rests on
    the tool list and P1, unchanged.
    """
    role = ctx.get("role")
    if role not in ("scribe", "planner", "coder"):
        return None
    for verb, args, _ in git_commands(toks):
        if role == "scribe":
            if verb == "push":
                return ("the scribe does not push - a scribe that may push is "
                        "a scribe that ships (ADR-0033 §5).")
            if verb == "checkout" and "-b" in args:
                return ("the scribe does not open a branch - it records onto "
                        "the sortie already in flight (ADR-0033 §4).")
        elif role == "planner":
            if verb == "commit":
                return ("the planner does not commit - the row is the "
                        "artefact, not a branch (ADR-0033 §4).")
            if verb == "checkout" and "-b" in args:
                return ("the planner does not open a branch - a branch from "
                        "the planner is a sortie nobody licensed (ADR-0033 §4).")
            if verb == "worktree" and args[:1] == ["add"]:
                return "the planner does not add a worktree (ADR-0033 §4)."
    if role == "coder":
        for args in issue_edits(toks):
            if any(a in ("--add-label", "--remove-label") for a in args):
                return ("the coder reports what it built by comment; "
                        "advancing the row's labels is the loop's act, off "
                        "facts (ADR-0033 §6).")
    return None


def forces(args: list[str]) -> bool:
    """A `git push` forced, in any of its spellings."""
    return any(a == "-f" or a.startswith(_FORCE_FLAGS) for a in args)


def push_targets(args: list[str], branch: str) -> list[str]:
    """Every branch this `git push` would write, as a bare name.

    The old parse read the *last* whitespace-separated token and, when that
    started with `-`, fell back to the checked-out branch. Four routes past it,
    all of which reach a protected branch (#168): a refspec delete, a flag
    delete, a renamed source, and any flag sitting before the remote.

    `*` means every branch - `--all` / `--mirror`. No refspec at all means the
    checked-out branch, which is what git itself pushes.
    """
    positional, skip, everything = [], False, False
    for t in args:
        if skip:
            skip = False
        elif t in _PUSH_VALUE_FLAGS:
            skip = True
        elif t in ("--all", "--mirror"):
            everything = True
        elif t.startswith("-"):
            continue
        else:
            positional.append(t)
    if everything:
        return ["*"]
    refspecs = positional[1:]          # positional[0] is the remote
    if not refspecs:
        return [branch]
    dests = []
    for spec in refspecs:
        dest = spec.split(":")[-1].lstrip("+")
        dests.append(dest.split("/")[-1] if dest.startswith("refs/") else dest)
    return dests


def _in_scope(path_hint: str, scope) -> bool:
    """A tripwire with `paths:` applies only there. path_hint None = no path
    context (a Bash command), where every tripwire applies."""
    if not scope or path_hint is None:
        return True
    norm = path_hint.replace("\\", "/")
    return any(norm.startswith(s.rstrip("/")) or norm.endswith(s) for s in scope)


def _tripwire(text: str, path_hint, cfg: dict):
    for tw in cfg.get("tripwires", []):
        if not _in_scope(path_hint, tw.get("paths")):
            continue
        if re.search(tw["pattern"], text):
            return f"tripwire {tw['name']}: {tw['why']}"
    return None


def git_refusal(toks: list[str], ctx: dict, cfg: dict) -> str | None:
    """The six git checks, over one parse. None allows.

    A `git -C <path>` command is judged by that path's own root, if `hook()`
    resolved one into `ctx["other_roots"]` - a `-C` under no qops root falls
    back to this root's rules, unchanged (#122).
    """
    branch = ctx.get("branch") or ""
    protected = cfg.get("protected_branches", [])
    other_roots = ctx.get("other_roots") or {}
    commands = git_commands(toks)
    # A command that makes its own branch before it writes is not writing to
    # the protected one. `git checkout -b x && git commit` used to be refused,
    # and the refusal named the wrong verb while doing it.
    branches_first = any(verb in ("checkout", "switch")
                         and any(a in ("-b", "-c", "-B", "-C") for a in args)
                         for verb, args, _ in commands)
    for verb, args, cpath in commands:
        other = other_roots.get(cpath) if cpath else None
        own_branch = other["branch"] if other else branch
        own_protected = other["protected"] if other else protected
        if verb == "push":
            if forces(args):
                return ("force-push is blocked. Rebase and push normally, or "
                        "ask the owner.")
            # a push naming another branch is fine even while master is out
            for target in push_targets(args, own_branch):
                if target == "*" and own_protected:
                    return (f"push --all/--mirror is blocked while "
                            f"{own_protected[0]} is protected"
                            f"{f' in {cpath}' if other else ''}. Open a PR.")
                if target in own_protected:
                    return (f"push to {target} is blocked"
                            f"{f' in {cpath}' if other else ''}. Open a PR.")
        elif verb == "reset" and "--hard" in args:
            return ("git reset --hard discards uncommitted work. Use git stash "
                    "or a soft reset.")
        elif verb == "worktree" and args[:1] == ["add"] \
                and ctx.get("worktrees", 0) >= cfg["max_worktrees"]:
            return (f"worktree sprawl: {ctx['worktrees']} already live, cap is "
                    f"{cfg['max_worktrees']}. Remove one first "
                    f"(git worktree remove).")
        elif verb in _WRITES and own_branch in own_protected and not branches_first:
            return (f"'{verb}' on {own_branch} is blocked"
                    f"{f' in {cpath}' if other else ''} - {own_branch} is "
                    f"protected. Branch first.")
    return None


def check(tool_name: str, tool_input: dict, ctx: dict, cfg: dict) -> str | None:
    """Return a refusal reason, or None to allow. Pure - ctx carries git state.

    Judged by what the payload carries, not by `tool_name` (#123): a shell
    tool under any name still carries a `command` string, so a matcher that
    reaches this function needs no second, hand-maintained list of names to
    stay in sync with the one on the hook's `matcher`.
    """
    cmd = tool_input.get("command")
    if cmd is not None:
        # #122: a denied unattended session retried with the sandbox off. An
        # owner at a keyboard can still make that call; a pickup-loop launch
        # (which sets QOPS_UNATTENDED) cannot, because nobody is reading.
        if tool_input.get("dangerouslyDisableSandbox") and ctx.get("unattended"):
            return ("dangerouslyDisableSandbox is refused in an unattended run. "
                    "Report the blocked command on the issue instead.")

        toks = argv_tokens(cmd)
        refusal = (head_moved_refusal(toks, ctx) or git_refusal(toks, ctx, cfg)
                   or origin_refusal(toks, ctx) or role_refusal(toks, ctx))
        if refusal:
            return refusal

        # A commit message that quotes a tripwire is describing the constraint,
        # not breaking it - same exemption the constraint docs get below.
        if not re.match(r"\s*git\s+(commit|log|show|notes)\b", cmd):
            hit = _tripwire(cmd, None, cfg)
            if hit:
                return hit
        return None

    if any(f in tool_input for f in _TEXT_FIELDS) or "edits" in tool_input:
        path_hint = tool_input.get("file_path", "")
        norm = path_hint.replace("\\", "/")
        excluded = tuple(cfg.get("scan_exclude", []))
        # The files that state the constraints have to be able to name them.
        if excluded and any(norm.endswith(e) or f"/{e}" in norm for e in excluded):
            return None
        # ADR-0033 §3, P3: the one cell with no mechanism until this row - the
        # scribe writes prose and state, never code. `docs/` and the ledger,
        # nowhere else; an unscoped scribe is a second coder with no test
        # discipline.
        if ctx.get("role") == "scribe" and not (
                norm.startswith("docs/") or norm.endswith(".qops/ledger.jsonl")):
            return ("the scribe writes only under docs/ and the ledger "
                    "(ADR-0033 §3).")
        for field in _TEXT_FIELDS:
            value = tool_input.get(field)
            if isinstance(value, str):
                hit = _tripwire(value, path_hint, cfg)
                if hit:
                    return hit
        for edit in tool_input.get("edits", []) or []:
            hit = _tripwire(str(edit.get("new_string", "")), path_hint, cfg)
            if hit:
                return hit
    return None


def git_context(root: Path) -> dict:
    def run(*args):
        try:
            return subprocess.run(["git", *args], cwd=root, capture_output=True,
                                  text=True, timeout=10).stdout.strip()
        except Exception:
            return ""
    worktrees = len([l for l in run("worktree", "list").splitlines() if l.strip()])
    return {"branch": run("rev-parse", "--abbrev-ref", "HEAD"),
            "worktrees": max(worktrees - 1, 0),
            "unattended": os.environ.get("QOPS_UNATTENDED") == "1",
            # ADR-0033 P3: the launcher sets QOPS_ROLE, the same idiom
            # QOPS_UNATTENDED already proves works. Absent for an owner at a
            # keyboard, who sets nothing and must be unaffected by role_refusal.
            "role": os.environ.get("QOPS_ROLE")}


# --- the CI half -----------------------------------------------------------

def scan(root: Path, cfg: dict) -> list[dict]:
    """Grep the tracked tree for tripwires. What guard.yml runs."""
    root = Path(root)
    hits = []
    files = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True,
                           text=True).stdout.split()
    if not files:
        files = [str(p.relative_to(root)) for p in root.rglob("*")
                 if p.is_file() and ".git" not in p.parts]
    excluded = tuple(cfg.get("scan_exclude", []))
    for tw in cfg.get("tripwires", []):
        rx = re.compile(tw["pattern"])
        for rel in files:
            norm = rel.replace("\\", "/")
            if excluded and norm.startswith(excluded):
                continue          # files that name the tripwires on purpose
            if not _in_scope(norm, tw.get("paths")):
                continue
            p = root / rel
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for n, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    hits.append({"file": norm, "line": n, "pattern": tw["pattern"],
                                 "name": tw["name"], "why": tw["why"]})
    return hits


# --- entry points ----------------------------------------------------------

def other_git_roots(cmd: str, root: Path) -> dict:
    """Every `git -C <path>` in `cmd`, resolved to that path's own branch and
    protected list - keyed by the literal `-C` value so `git_refusal` can look
    a command's `cpath` straight up.

    A `-C` path under no qops root is left out: it is judged by this root's
    rules unchanged, which is `argv_tokens`' own rule (read less, never
    nothing) applied to roots instead of tokens. The subprocess call and file
    read live here, not in `check()` - that is the one function the
    parametrized refusal tests drive directly (#122).
    """
    other = {}
    for _, _, cpath in git_commands(argv_tokens(cmd)):
        if not cpath or cpath in other:
            continue
        candidate = Path(cpath)
        if not candidate.is_absolute():
            candidate = root / candidate
        found = config.find_root(candidate)
        if not (found / ".qops" / "config.yml").exists():
            continue
        other[cpath] = {"branch": git_context(found)["branch"],
                         "protected": config.load(found).get("protected_branches", [])}
    return other


def hook(root: Path, cfg: dict) -> int:
    """PreToolUse. Reads the payload on stdin; exit 2 blocks the call."""
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    session_id = payload.get("session_id")
    ctx = git_context(root)
    if session_id:
        ctx["session_branch"] = ledger.last_session_branch(root, session_id)
        ctx["session_prior_branch"] = ledger.prior_session_branch(root, session_id)
    cmd = tool_input.get("command", "")
    if "command" in tool_input:
        ctx["other_roots"] = other_git_roots(cmd, root)
    reason = check(tool_name, tool_input, ctx, cfg)
    if reason:
        print(f"qops guard: {reason}", file=sys.stderr)
        return 2
    if session_id and cmd:
        target = self_checkout(argv_tokens(cmd))
        if target:
            ledger.append(root, "checkout", {"session_id": session_id, "branch": target})
    return 0


def main(argv: list[str], root: Path, cfg: dict) -> int:
    if argv and argv[0] == "scan":
        hits = scan(root, cfg)
        for h in hits:
            print(f"{h['file']}:{h['line']}: {h['name']} - {h['why']}")
        if hits:
            print(f"\n{len(hits)} tripwire hit(s).", file=sys.stderr)
            return 1
        print("guard: no tripwires.")
        return 0
    return hook(root, cfg)
