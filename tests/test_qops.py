"""qops substrate — the assertions that make the rules real.

CLAUDE.md's own convention: an instruction in a prompt is a preference, not a
control (GL-53). Every rule qops states in a workflow, a hook or a prompt has an
assertion here.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from qops import config as qconfig  # noqa: E402
from qops import guard, install, ledger, metrics, brief as briefmod  # noqa: E402


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------

def test_config_carries_every_project_specific():
    cfg = qconfig.load(REPO)
    for key in (
        "project", "python", "protected_branches", "max_worktrees",
        "tripwires", "claude_md_max_lines", "ci", "agents", "labels",
    ):
        assert key in cfg, f"{key} missing from .qops/config.yml"
    assert cfg["claude_md_max_lines"] == 150
    assert "master" in cfg["protected_branches"]


# --------------------------------------------------------------------------
# skills — ADR-0018. ADR-0013 made the count a mitigation a human was asked to
# re-read; nobody did and 11 accepted became 19 installed. Here it is a check.
# --------------------------------------------------------------------------

def test_declared_skill_set_matches_what_is_installed():
    assert install.skill_drift(REPO, qconfig.load(REPO)) == []


def test_skill_drift_catches_an_undeclared_skill_and_a_refless_pin(tmp_path):
    (tmp_path / ".claude" / "skills").mkdir(parents=True)
    for name in ("interview", "grill-me"):
        (tmp_path / ".claude" / "skills" / name).mkdir()
    (tmp_path / "skills-lock.json").write_text(json.dumps({"skills": {
        "run-models": {"source": "acme/skills"},               # no ref
    }}), encoding="utf-8")
    cfg = {"skills": {"native": ["interview", "triage"],
                      "external": ["run-models"]}}
    problems = "\n".join(install.skill_drift(tmp_path, cfg))
    assert "grill-me" in problems and "not declared" in problems
    assert "triage" in problems and "missing" in problems
    assert "run-models" in problems and "no upstream ref" in problems


def test_gh_api_writes_are_never_allowlisted():
    """Sign-off item 10. `gh api` bare is a GET and is allowlisted; a write to
    repo settings is an owner decision, already taken. The allow rule is only
    safe while the deny rules take the method flags back."""
    perms = json.loads((REPO / ".claude" / "settings.json")
                       .read_text(encoding="utf-8"))["permissions"]
    denied = set(perms.get("deny", []))
    for flag in ("-X", "--method", "-f", "--field", "-F", "--input"):
        assert f"Bash(gh api {flag}:*)" in denied, f"gh api {flag} is not denied"
    assert not any(a.startswith("Bash(gh api -X") for a in perms["allow"])


@pytest.mark.parametrize("agent", ["planner", "interactor"])
def test_owner_facing_asks_are_capped_at_one_page(agent):
    """Sign-off item 9: enforced in the agent definitions, not as a wish."""
    text = (REPO / ".claude" / "agents" / f"{agent}.md").read_text(encoding="utf-8")
    assert "One page" in text
    assert "four options" in text and "one recommendation" in text


def test_triage_stays_owner_only_and_spec_to_issue_does_not():
    """ADR-0019 decided the two by name. A decision with no assertion is a
    preference (GL-53), so the frontmatter is asserted, not trusted."""
    skills = REPO / ".claude" / "skills"
    triage = (skills / "triage" / "SKILL.md").read_text(encoding="utf-8")
    spec = (skills / "spec-to-issue" / "SKILL.md").read_text(encoding="utf-8")
    assert "disable-model-invocation: true" in triage
    assert "disable-model-invocation" not in spec


# --------------------------------------------------------------------------
# guard — the hard blocks. ADR-0001: PreToolUse exit 2 blocks for real.
# --------------------------------------------------------------------------

CTX = {"branch": "master", "worktrees": 1}
FEATURE = {"branch": "gl-63-thing", "worktrees": 1}


@pytest.mark.parametrize("command", [
    "git commit -m 'x'",
    "git commit --amend --no-edit",
    "git push origin master",
    "git push",
])
def test_guard_blocks_writes_to_master(command):
    assert guard.check("Bash", {"command": command}, CTX, qconfig.load(REPO))


@pytest.mark.parametrize("command", [
    "git push --force origin gl-63",
    "git push -f origin gl-63",
    "git push --force-with-lease origin gl-63",
    "git reset --hard HEAD~1",
])
def test_guard_blocks_destructive_git(command):
    assert guard.check("Bash", {"command": command}, FEATURE, qconfig.load(REPO))


def test_guard_blocks_worktree_sprawl():
    cfg = qconfig.load(REPO)
    over = dict(FEATURE, worktrees=cfg["max_worktrees"])
    assert guard.check("Bash", {"command": "git worktree add ../wt"}, over, cfg)
    under = dict(FEATURE, worktrees=0)
    assert guard.check("Bash", {"command": "git worktree add ../wt"}, under, cfg) is None


@pytest.mark.parametrize("command", [
    "git commit -m 'x'",          # on a feature branch, fine
    "git push origin gl-63-thing",
    "python -m pytest -q",
    "git status --short",
    "git reset HEAD~1",           # soft reset is not the blocked one
])
def test_guard_allows_ordinary_work(command):
    assert guard.check("Bash", {"command": command}, FEATURE, qconfig.load(REPO)) is None


# A synthetic tripwire set. The substrate has to be exercised without any
# project's constraints in it; this repo's own are in test_qops_project.py.
SYNTHETIC = {
    "protected_branches": ["master"],
    "max_worktrees": 2,
    "scan_exclude": ["docs/"],
    "tripwires": [
        {"name": "scoped", "pattern": r"FORBIDDEN_CALL\(", "paths": ["src/"],
         "why": "A tripwire with paths applies only there."},
        {"name": "global", "pattern": r"FORBIDDEN-LITERAL",
         "why": "A tripwire with no paths applies everywhere."},
    ],
}
EMPTY = {"protected_branches": ["master"], "max_worktrees": 2,
         "scan_exclude": [], "tripwires": []}


@pytest.mark.parametrize("tool,inp", [
    ("Bash", {"command": "echo FORBIDDEN-LITERAL"}),
    ("Write", {"file_path": "src/x.py", "content": "FORBIDDEN_CALL()"}),
    ("Edit", {"file_path": "src/x.py", "new_string": "FORBIDDEN_CALL()"}),
])
def test_guard_blocks_a_tripwire(tool, inp):
    assert guard.check(tool, inp, FEATURE, SYNTHETIC)


def test_a_scoped_tripwire_does_not_apply_outside_its_paths():
    inp = {"file_path": "elsewhere/x.py", "content": "FORBIDDEN_CALL()"}
    assert guard.check("Write", inp, FEATURE, SYNTHETIC) is None


def test_guard_scan_finds_a_planted_string_and_skips_the_excluded_tree(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "src" / "x.py").write_text("FORBIDDEN_CALL()\n", encoding="utf-8")
    (tmp_path / "docs" / "why.md").write_text("FORBIDDEN-LITERAL\n", encoding="utf-8")
    hits = guard.scan(tmp_path, SYNTHETIC)
    assert [h["file"] for h in hits] == ["src/x.py"]


def test_guard_scan_exits_0_against_an_empty_tripwire_list(tmp_path, capsys):
    """The substrate repo declares none. That path had never been exercised,
    and a crashing guard job would fail every build in a new repo from its
    first push (PRD P8.1)."""
    (tmp_path / "x.py").write_text("anything at all\n", encoding="utf-8")
    assert guard.scan(tmp_path, EMPTY) == []
    assert guard.main(["scan"], tmp_path, EMPTY) == 0
    assert "no tripwires" in capsys.readouterr().out


# --- #168: the guard reads argv, not the prose argv carries ----------------

@pytest.mark.parametrize("command", [
    "git push origin master",
    "git push origin :master",                  # refspec delete
    "git push --delete origin master",          # flag delete
    "git push origin HEAD:master",              # renamed source
    "git push --quiet origin master",           # a flag before the remote
    "git push origin refs/heads/master",        # fully qualified
    "git push --all origin",                    # every branch, master included
    "git push",                                 # implicit, while master is out
])
def test_guard_reads_every_push_target(command):
    """Each of these writes a protected branch. The old parse read the last
    whitespace-separated token and missed all but the first (#168)."""
    assert guard.check("Bash", {"command": command}, CTX, SYNTHETIC), command


@pytest.mark.parametrize("command", [
    "git push origin feature",
    "git push origin HEAD:feature",
    "git push -u origin feature",
    "git push --quiet origin feature",
    "git push origin :feature",                 # deleting a feature branch
])
def test_guard_allows_a_push_to_an_unprotected_branch_from_master(command):
    assert guard.check("Bash", {"command": command}, CTX, SYNTHETIC) is None, command


def test_guard_lets_a_command_document_a_git_rule():
    """The substrate has to be able to state its own git rules through a tool
    that takes prose. `_FORCE` matched the whole command string, so it could
    not (#168) — and the workaround, --body-file, is a path the guard cannot
    see into, which is worse."""
    prose = "the rule is: git push --force is blocked, and git reset --hard too"
    for cmd in (f'gh issue comment 1 --body "{prose}"',
                f"gh pr create --title 'x' --body '{prose}'",
                f'git commit -m "{prose}"'):
        assert guard.check("Bash", {"command": cmd}, FEATURE, SYNTHETIC) is None, cmd


@pytest.mark.parametrize("command", [
    'bash -c "git push --force origin feature"',   # -c carries a command
    "git -c core.pager=cat push --force origin feature",
])
def test_the_prose_exemption_does_not_reach_a_command(command):
    assert guard.check("Bash", {"command": command}, FEATURE, SYNTHETIC), command


def test_guard_allows_branching_before_writing_on_master():
    """`git checkout -b x && git commit` does not write to master, and the
    refusal it used to draw even named the wrong verb."""
    cmd = "git checkout -b fix/1-x && git commit -m 'x'"
    assert guard.check("Bash", {"command": cmd}, CTX, SYNTHETIC) is None


# --------------------------------------------------------------------------
# ledger + resume
# --------------------------------------------------------------------------

def test_ledger_appends_one_json_object_per_line(tmp_path):
    ledger.append(tmp_path, "session_start", {"branch": "master"})
    ledger.append(tmp_path, "stop", {"reads": 3})
    lines = (tmp_path / ".qops" / "ledger.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2
    rec = json.loads(lines[0])
    assert rec["event"] == "session_start" and rec["branch"] == "master" and rec["ts"]


def test_resume_is_written_from_the_ledger(tmp_path):
    ledger.append(tmp_path, "session_start", {"branch": "gl-63", "cwd": str(tmp_path)})
    ledger.append(tmp_path, "note", {"text": "picked up GL-63"})
    text = ledger.write_resume(tmp_path)
    assert "GL-63" in text or "gl-63" in text
    assert (tmp_path / ".qops" / "resume.md").exists()


# --------------------------------------------------------------------------
# brief — the two contracts
# --------------------------------------------------------------------------

def test_brief_never_exceeds_400_tokens():
    text = briefmod.render(REPO, qconfig.load(REPO))
    assert briefmod.tokens(text) <= 400, f"brief is {briefmod.tokens(text)} tokens"


def test_brief_is_ascii():
    """It is written to a Windows console by a hook; a dash became U+FFFD."""
    text = briefmod.render(REPO, qconfig.load(REPO))
    text.encode("ascii")


def test_brief_reports_dotted_paths_intact():
    """`git status --porcelain`'s first line starts with a space; stripping it
    took the first character of the path with it."""
    state = briefmod.collect(REPO, qconfig.load(REPO))
    assert not any(p.startswith("qops/config.yml") for p in state["dirty"])


def test_brief_leads_with_a_dirty_tree_violation():
    state = {"branch": "master", "dirty": ["src/x.py", "notes/y.txt"],
             "worktrees": 1, "issue": None, "resume": "", "ahead": 0}
    text = briefmod.render_from(state, qconfig.load(REPO))
    first = [ln for ln in text.splitlines() if ln.strip()][0]
    assert "dirty" in first.lower(), f"first line was: {first}"


def test_brief_is_quiet_when_the_tree_is_clean():
    state = {"branch": "gl-63", "dirty": [], "worktrees": 1, "issue": None,
             "resume": "", "ahead": 0}
    text = briefmod.render_from(state, qconfig.load(REPO))
    assert "dirty" not in text.lower().splitlines()[0]


# --------------------------------------------------------------------------
# brief — the routing verdict (ADR-0017)
#
# The rule decides how much of the owner's time an issue may spend. In a skill
# body it is a preference; here it is read unasked, every session.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("labels,expected", [
    (["type:epic", "gate:taste"], "mission"),
    (["type:code", "gate:machine"], "no owner contact"),
    (["type:code", "gate:taste"], "artefact"),
    (["type:code", "gate:none"], "unrouted"),
    (["type:code"], "unrouted"),
])
def test_routing_verdict_per_gate(labels, expected):
    assert expected in briefmod.routing(labels).lower()


def test_routing_says_proceed_only_with_ready_auto():
    """`gate:machine` alone means no contact before review. `ready:auto` is the
    stronger claim — an unattended pickup — and needs both labels."""
    assert "unattended" not in briefmod.routing(["gate:machine"]).lower()
    assert "unattended" in briefmod.routing(["gate:machine", "ready:auto"]).lower()


def test_routing_never_promises_autonomy_without_a_gate():
    """gate:none blocks ready:auto (finding B7). A mislabelled issue must not
    read as a licence to run unattended."""
    for labels in (["ready:auto"], ["ready:auto", "gate:none"]):
        assert "unattended" not in briefmod.routing(labels).lower()


@pytest.mark.parametrize("branch,expected", [
    ("feat/117-brief-routing-verdict", 117),
    ("fix/110-ci-duplicate-runs", 110),
    ("docs/112-phase7-proposal", 112),
    ("no-issue/quick-look", None),
    ("master", None),
    ("gl45-telegram-drops", None),
])
def test_active_issue_comes_from_the_branch(branch, expected):
    """The ledger only ever carried an `issue` on `qops close`, so the brief's
    active sortie was the last CLOSED one — and a routing verdict for a closed
    sortie is worse than none. The branch is the live fact (ADR-0019)."""
    assert briefmod.issue_from_branch(branch) == expected


def test_brief_prints_the_verdict_for_the_active_issue():
    state = {"branch": "feat/117-x", "dirty": [], "worktrees": 1, "issue": 117,
             "labels": ["type:code", "gate:machine"], "resume": "", "ahead": 0}
    text = briefmod.render_from(state, qconfig.load(REPO))
    assert "no owner contact" in text.lower()


def test_brief_degrades_silently_when_labels_are_unavailable():
    """`gh` may be absent, offline or slow. A brief that fails is worse than a
    brief with no verdict — it is hot path and it runs before anything else."""
    state = {"branch": "feat/117-x", "dirty": [], "worktrees": 1, "issue": 117,
             "labels": [], "resume": "", "ahead": 0}
    text = briefmod.render_from(state, qconfig.load(REPO))
    assert "unrouted" not in text.lower()
    assert "sortie #117" in text


# --------------------------------------------------------------------------
# metrics — S1 must reproduce the Phase -1 method exactly
# --------------------------------------------------------------------------

def _transcript(tmp_path, records):
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in records))
    return p


def _msg(role, blocks, sidechain=False):
    return {"type": role, "isSidechain": sidechain,
            "message": {"role": role, "content": blocks}}


def _read(lines=10):
    return {"type": "tool_use", "name": "Read", "input": {"file_path": "x.py"}}


def _bash(cmd):
    return {"type": "tool_use", "name": "Bash", "input": {"command": cmd}}


def test_s1_counts_reads_before_the_first_productive_call(tmp_path):
    t = _transcript(tmp_path, [
        _msg("assistant", [_read(), _read()]),
        _msg("assistant", [{"type": "tool_use", "name": "Edit", "input": {}}]),
        _msg("assistant", [_read()]),          # after productive: not counted
    ])
    assert metrics.s1_for_transcript(t)["reads"] == 2


def test_s1_excludes_subagent_traffic(tmp_path):
    t = _transcript(tmp_path, [
        _msg("assistant", [_read()], sidechain=True),
        _msg("assistant", [_read()]),
        _msg("assistant", [{"type": "tool_use", "name": "Write", "input": {}}]),
    ])
    assert metrics.s1_for_transcript(t)["reads"] == 1


def test_s1_does_not_count_bash_reads(tmp_path):
    t = _transcript(tmp_path, [
        _msg("assistant", [_bash("cat CLAUDE.md"), _bash("sed -n '1,50p' x")]),
        _msg("assistant", [{"type": "tool_use", "name": "Edit", "input": {}}]),
    ])
    assert metrics.s1_for_transcript(t)["reads"] == 0


@pytest.mark.parametrize("cmd,productive", [
    ("git commit -m x", True),
    ("python -m pytest -q", True),
    ("npm test", True),
    ("git status", False),
])
def test_s1_productive_call_definition(tmp_path, cmd, productive):
    t = _transcript(tmp_path, [
        _msg("assistant", [_bash(cmd)]),
        _msg("assistant", [_read()]),
    ])
    # a read after the first productive call is not counted; if the bash call is
    # not productive, the read is the only thing before nothing -> no productive
    got = metrics.s1_for_transcript(t)
    assert got["productive"] is productive


def test_s1_flags_reads_over_200_lines(tmp_path):
    big = {"type": "tool_result", "content": "\n".join(str(i) for i in range(250))}
    t = _transcript(tmp_path, [
        _msg("assistant", [_read()]),
        _msg("user", [big]),
        _msg("assistant", [{"type": "tool_use", "name": "Edit", "input": {}}]),
    ])
    assert metrics.s1_for_transcript(t)["big_read"] is True


def _timed_transcript(dir_path, name, ts, records):
    p = dir_path / name
    lines = [json.dumps({"type": "user", "timestamp": ts,
                          "isSidechain": False, "message": {"role": "user", "content": []}})]
    lines += [json.dumps(r) for r in records]
    p.write_text("\n".join(lines))
    return p


def test_s1_floors_transcript_on_first_user_assistant_record_and_windows_by_date(tmp_path, monkeypatch):
    home = tmp_path / "home"
    proj_dir = home / ".claude" / "projects" / "C--fake-project"
    proj_dir.mkdir(parents=True)
    monkeypatch.setattr(metrics.os.path, "expanduser", lambda p: str(home))
    root = tmp_path / "project"
    root.mkdir()

    in_window = [_msg("assistant", [_read(), _read()]),
                 _msg("assistant", [{"type": "tool_use", "name": "Edit", "input": {}}])]
    before = [_msg("assistant", [_read()]),
              _msg("assistant", [{"type": "tool_use", "name": "Edit", "input": {}}])]
    after = [_msg("assistant", [_read()]),
             _msg("assistant", [{"type": "tool_use", "name": "Edit", "input": {}}])]

    _timed_transcript(proj_dir, "in.jsonl", "2026-07-20T10:00:00.000Z", in_window)
    _timed_transcript(proj_dir, "before.jsonl", "2026-06-01T10:00:00.000Z", before)
    _timed_transcript(proj_dir, "after.jsonl", "2026-09-01T10:00:00.000Z", after)

    result = metrics.s1(root, since="2026-07-01", until="2026-08-01")
    assert result["sessions"] == 1
    assert result["median_reads"] == 2
    assert "C--fake-project" in result["by_dir"]
    assert result["by_dir"]["C--fake-project"]["sessions"] == 1


def test_s2_counts_kickoff_class_docs():
    n = metrics.s2(REPO, since="2026-07-14")
    assert isinstance(n, int) and n >= 0


# --------------------------------------------------------------------------
# metrics — S11/S12/S13, usage not ROI (issue #115)
# --------------------------------------------------------------------------

def test_owner_minutes_pairs_session_start_with_next_stop():
    events = [
        {"ts": "2026-08-01T10:00:00Z", "event": "session_start", "branch": "feat/1-x"},
        {"ts": "2026-08-01T10:05:00Z", "event": "stop", "branch": "feat/1-x"},
        {"ts": "2026-08-01T11:00:00Z", "event": "session_start", "branch": "feat/1-x"},
        {"ts": "2026-08-01T11:02:00Z", "event": "session_end", "branch": "feat/1-x"},
    ]
    got = metrics.owner_minutes(events)
    assert got == {"total_minutes": 7.0, "sessions": 2}


def test_owner_minutes_respects_since():
    events = [
        {"ts": "2026-07-01T10:00:00Z", "event": "session_start", "branch": "feat/1-x"},
        {"ts": "2026-07-01T10:05:00Z", "event": "stop", "branch": "feat/1-x"},
    ]
    got = metrics.owner_minutes(events, since="2026-08-01")
    assert got == {"total_minutes": 0.0, "sessions": 0}


def test_full_flow_share_requires_convention_gate_and_deleted_branch():
    prs = [
        {"headRefName": "feat/1-x", "statusCheckRollup": [{"conclusion": "SUCCESS"}]},
        {"headRefName": "feat/2-y", "statusCheckRollup": [{"conclusion": "SUCCESS"}]},
        {"headRefName": "no-issue/z", "statusCheckRollup": [{"conclusion": "SUCCESS"}]},
        {"headRefName": "feat/3-w", "statusCheckRollup": [{"conclusion": "FAILURE"}]},
    ]
    gone = {"feat/1-x": True, "feat/2-y": False, "no-issue/z": True, "feat/3-w": True}
    got = metrics.full_flow_share(prs, lambda b: gone[b])
    assert got == {"total": 4, "full_flow": 1, "pct": 25}


def test_full_flow_share_empty_prs():
    assert metrics.full_flow_share([], lambda b: True) == {
        "total": 0, "full_flow": 0, "pct": None}


def test_owner_interruptions_counts_extra_session_starts_per_branch():
    events = [
        {"ts": "2026-08-01T10:00:00Z", "event": "session_start", "branch": "feat/1-x"},
        {"ts": "2026-08-01T12:00:00Z", "event": "session_start", "branch": "feat/1-x"},
        {"ts": "2026-08-02T10:00:00Z", "event": "session_start", "branch": "feat/2-y"},
    ]
    got = metrics.owner_interruptions(events)
    assert got == {"sorties": 2, "interruptions": 1, "per_sortie": 0.5}


def test_owner_interruptions_no_sessions():
    assert metrics.owner_interruptions([]) == {
        "sorties": 0, "interruptions": 0, "per_sortie": None}


def test_main_wires_since_through_to_s1(tmp_path, monkeypatch):
    seen = {}

    def fake_s1(root, since="2026-07-14", until=None):
        seen["since"], seen["until"] = since, until
        return {"since": since, "until": until}

    monkeypatch.setattr(metrics, "s1", fake_s1)
    monkeypatch.setattr(metrics, "s2", lambda root, since="2026-07-14": 0)
    monkeypatch.setattr(metrics, "s4", lambda root: {"available": False})
    monkeypatch.setattr(metrics, "s9", lambda root: {"available": False})
    monkeypatch.setattr(metrics, "s10", lambda root, cfg: {})
    monkeypatch.setattr(metrics, "s11", lambda root, since=None: {"available": False})
    monkeypatch.setattr(metrics, "s12", lambda root, since=None: {"available": False})
    monkeypatch.setattr(metrics, "s13", lambda root, since=None: {})

    rc = metrics.main(["--since", "2026-08-01", "--until", "2026-08-10", "--json"],
                       tmp_path, {})
    assert rc == 0
    assert seen == {"since": "2026-08-01", "until": "2026-08-10"}


def test_main_rejects_unrecognised_flag(tmp_path, capsys):
    rc = metrics.main(["--bogus"], tmp_path, {})
    assert rc != 0
    assert "--bogus" in capsys.readouterr().err


# --------------------------------------------------------------------------
# install / doctor — rendered workflows, and drift is detectable
# --------------------------------------------------------------------------

def test_install_renders_the_six_workflows(tmp_path):
    written = install.render_all(tmp_path, qconfig.load(REPO))
    names = {Path(p).name for p in written}
    assert names == {"test.yml", "gate.yml", "guard.yml", "digest.yml",
                     "groom.yml", "automerge.yml"}
    import re
    for p in written:
        # `${{ secrets.X }}` is GitHub's own syntax and stays; qops placeholders
        # are `{{word}}` and must all be gone.
        left = re.search(r"\{\{\w+\}\}", Path(p).read_text())
        assert left is None, f"unrendered placeholder {left.group(0)} in {p}"


def test_doctor_is_green_on_a_fresh_install(tmp_path):
    install.render_all(tmp_path, qconfig.load(REPO))
    assert install.drift(tmp_path, qconfig.load(REPO)) == []


def test_doctor_detects_drift(tmp_path):
    install.render_all(tmp_path, qconfig.load(REPO))
    wf = tmp_path / ".github" / "workflows" / "groom.yml"
    wf.write_text(wf.read_text() + "\n# hand-edited\n")
    assert "groom.yml" in " ".join(install.drift(tmp_path, qconfig.load(REPO)))


def test_the_repo_itself_is_installed_and_undrifted():
    assert install.drift(REPO, qconfig.load(REPO)) == []


# --------------------------------------------------------------------------
# automerge — ADR-0020's conditions, so loosening one fails a test
#
# The workflow decides, unattended, that no human will read a diff before it
# reaches master. Each condition below is the reason that is safe.
# --------------------------------------------------------------------------

def _automerge_text() -> str:
    return install.render_one("automerge.yml", qconfig.load(REPO))


@pytest.mark.parametrize("condition", [
    "gate:machine",                     # the gate that authorises it
    "no-auto",                          # the standing per-issue veto
    "draft == false",                   # a draft is not a claim of done
    "head.repo.full_name == github.repository",   # never a fork
])
def test_automerge_keeps_every_adr_0020_condition(condition):
    assert condition in _automerge_text()


def test_automerge_reads_the_gate_from_the_issue_not_the_pr():
    """The first cut read `github.event.pull_request.labels` and could never
    fire: nothing labels a PR, and issues are the source of truth. The issue
    number comes from the branch (ADR-0019), so `no-issue/` never qualifies."""
    text = _automerge_text()
    assert "gh issue view" in text
    assert "pull_request.labels.*.name" not in text   # the expression form
    assert "^[a-z]+/([0-9]+)-" in text


def test_automerge_never_interpolates_the_branch_into_a_shell():
    """A branch name is attacker-controlled on a public repo. It reaches the
    shell as an environment variable or not at all."""
    text = _automerge_text()
    assert "${{ github.event.pull_request.head.ref }}" not in text.split("env:")[0]
    assert "$REF" in text


def test_automerge_squashes_and_deletes():
    assert "--squash" in _automerge_text()
    assert "--delete-branch" in _automerge_text()


def test_automerge_waits_for_the_gate_rather_than_merging_now():
    """`--auto` hands the merge to branch protection's required checks. Merging
    directly would be the one thing ADR-0020 does not authorise."""
    assert "--auto" in _automerge_text()


# --------------------------------------------------------------------------
# automerge — the release-on-success half (#128)
#
# #122 released the claim on failure and on nothing else, so #116 shipped and
# its issue stayed OPEN at `state:building` with `ready:auto` on it: not a
# re-pick loop, a silent one-row leak that only grows, and `metrics.S9` counted
# a finished sortie as in-flight. The mechanism is here rather than in the
# launched agent's PR body — an instruction in a prompt is a preference, not a
# control (GL-53).
# --------------------------------------------------------------------------

def test_a_merged_pr_advances_its_issue_off_the_claim():
    text = _automerge_text()
    advance = text.split("\n  advance:")[1]
    assert "github.event.pull_request.merged == true" in advance
    assert "--add-label state:done" in advance
    assert "--remove-label ready:auto" in advance
    assert "--remove-label state:building" in advance


def test_advance_labels_but_never_closes_the_issue():
    """A merged PR means the code landed, not that the sortie is judged. On
    `gate:taste` work the owner's read is the only judgement there is."""
    assert "gh issue close" not in _automerge_text()


def test_advance_does_not_depend_on_the_agent_writing_closes():
    """The branch already carries the issue number (ADR-0019) and the workflow
    already parses it. #116's PR carried no `Closes` line and shipped anyway."""
    advance = _automerge_text().split("\n  advance:")[1]
    assert "^[a-z]+/([0-9]+)-" in advance
    assert "$REF" in advance
    assert "${{ github.event.pull_request.head.ref }}" not in advance.split("env:")[0]


def test_automerge_hears_the_merge_without_re_merging_a_closed_pr():
    text = _automerge_text()
    assert "closed" in text.split("types: [")[1].split("]")[0]
    assert "github.event.action != 'closed'" in text.split("\n  advance:")[0]


# --------------------------------------------------------------------------
# the two rules that are otherwise only stated in a workflow
# --------------------------------------------------------------------------

def test_claude_md_is_within_the_hot_path_cap():
    cfg = qconfig.load(REPO)
    n = len((REPO / "CLAUDE.md").read_text(encoding="utf-8").splitlines())
    assert n <= cfg["claude_md_max_lines"], f"CLAUDE.md is {n} lines"


def test_every_doc_path_cited_from_code_resolves():
    missing = install.broken_doc_links(REPO)
    assert missing == [], f"broken doc citations: {missing}"


# --------------------------------------------------------------------------
# the CLI is actually wired
# --------------------------------------------------------------------------

@pytest.mark.parametrize("verb", ["brief", "ledger", "resume", "guard", "close",
                                  "install", "doctor", "metrics"])
def test_every_verb_is_dispatchable(verb):
    out = subprocess.run([sys.executable, "-m", "qops", verb, "--help"],
                         cwd=REPO, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr


# --------------------------------------------------------------------------
# subagent definitions — the roster, and the two §3.4 levers, asserted
# --------------------------------------------------------------------------

AGENT_DIR = REPO / ".claude" / "agents"


def _frontmatter(path: Path) -> dict:
    import yaml
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path.name} has no frontmatter"
    return yaml.safe_load(text.split("---", 2)[1])


def test_the_roster_is_exactly_the_config_s():
    names = {p.stem for p in AGENT_DIR.glob("*.md")}
    assert names == set(qconfig.load(REPO)["agents"])


@pytest.mark.parametrize("role", ["planner", "coder", "reviewer", "scribe",
                                  "triager", "interactor"])
def test_each_agent_matches_its_config_entry(role):
    spec = qconfig.load(REPO)["agents"][role]
    fm = _frontmatter(AGENT_DIR / f"{role}.md")
    assert fm["model"] == spec["model"]
    assert fm["effort"] == spec["effort"]
    assert [t.strip() for t in fm["tools"].split(",")] == spec["tools"]


@pytest.mark.parametrize("role", ["planner", "coder", "reviewer", "scribe",
                                  "triager", "interactor"])
def test_no_agent_nags_about_verification(role):
    """§3.4: scope-fencing language replaces verification-nagging. The named
    exception is the reviewer, which exists because of a 2026-08-01 incident."""
    body = (AGENT_DIR / f"{role}.md").read_text(encoding="utf-8").lower()
    for phrase in ("double-check", "double check", "verify your own work",
                   "make sure you did"):
        assert phrase not in body, f"{role} contains {phrase!r}"
    assert "scope fence" in body


def test_read_only_agents_cannot_write():
    for role in ("planner", "reviewer", "triager", "interactor"):
        tools = qconfig.load(REPO)["agents"][role]["tools"]
        assert not ({"Write", "Edit", "MultiEdit", "NotebookEdit"} & set(tools)), role


# --------------------------------------------------------------------------
# pickup-loop — the unattended write grant (#122). The 2026-08-16 acceptance
# run read for 62 seconds and could not write a line: the launch carried no
# permission mode, so every branch and every edit waited on a human who was
# not there. The grant is per-launch and scoped; the guard stays the control.
# --------------------------------------------------------------------------

sys.path.insert(0, str(REPO / "scripts"))
import qops_pickup  # noqa: E402


def test_launch_carries_a_write_grant():
    argv = qops_pickup.launch_argv("work #116")
    assert "--permission-mode" in argv and argv[argv.index("--permission-mode") + 1] == "acceptEdits"
    assert "--allowedTools" in argv


def test_the_grant_is_the_coder_toolset_and_no_wider():
    """#123 asks what each role may run. Until it answers, the launch borrows
    the coder's answer rather than inventing a second one."""
    granted = qops_pickup.launch_argv("x")[qops_pickup.launch_argv("x").index("--allowedTools") + 1]
    assert set(granted.split(",")) == set(qconfig.load(REPO)["agents"]["coder"]["tools"])


def test_launch_never_passes_a_blanket_bypass():
    argv = qops_pickup.launch_argv("x")
    for flag in qops_pickup.BLANKET_BYPASS:
        assert flag not in argv
    assert not any(a.startswith("--dangerously") for a in argv)


def test_launch_marks_the_session_unattended():
    assert qops_pickup.launch_env()["QOPS_UNATTENDED"] == "1"


def test_guard_refuses_a_sandbox_escape_when_unattended():
    cfg = qconfig.load(REPO)
    payload = {"command": "git checkout -b feat/116-x", "dangerouslyDisableSandbox": True}
    ctx = {"branch": "master", "worktrees": 1, "unattended": True}
    assert "unattended" in (guard.check("Bash", payload, ctx, cfg) or "")
    ctx["unattended"] = False
    assert guard.check("Bash", payload, ctx, cfg) is None


def test_a_failed_run_releases_the_claim(monkeypatch, tmp_path):
    """The claim was a one-way door: a failed run left `state:building` and no
    later fire could reach the issue again (GL-46 — a swallowed failure must
    leave a state change behind)."""
    calls = []
    monkeypatch.setattr(qops_pickup.subprocess, "run",
                        lambda cmd, **kw: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""))
    monkeypatch.setattr(qops_pickup.ledger, "append", lambda *a, **k: None)
    qops_pickup.release(tmp_path, "116", "exit 1")
    edit = next(c for c in calls if c[:3] == ["gh", "issue", "edit"])
    assert "state:building" in edit and "state:planned" in edit
    comment = next(c for c in calls if c[:3] == ["gh", "issue", "comment"])
    assert "exit 1" in comment[-1]


def test_the_launch_prompt_names_the_branch_prefixes(monkeypatch):
    """#116 branched `code/116-...` — it read `type:code` off the issue and used
    a label where ADR-0019 wants a commit type. Prompt-only on purpose: a merge
    rejected over a prefix nit would be worse than the drift."""
    prompt = qops_pickup.launch_prompt("116")
    for prefix in ("feat", "fix", "docs", "chore"):
        assert prefix in prompt
    assert "`<type>/116-<slug>`" in prompt


def test_the_launch_prompt_does_not_ask_the_pr_to_close_the_issue():
    """`Closes #n` would close it on merge. Closing is the owner's; the loop
    advances the label (see the `advance` job)."""
    prompt = qops_pickup.launch_prompt("116")
    assert "Refs #116" in prompt
    assert "Closes #116" not in prompt


def test_no_branch_and_no_pr_is_a_failed_run(monkeypatch):
    """The 62-second run exited 0. Exit code alone would have kept the claim."""
    monkeypatch.setattr(qops_pickup.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", ""))
    assert qops_pickup.produced_work(REPO, "999999") is False


def _fake_git(branches: str, ahead: str, prs: str = "[]"):
    """A subprocess double for produced_work's three shell-outs."""
    def run(cmd, **kw):
        if cmd[:2] == ["git", "branch"]:
            out = branches
        elif cmd[:2] == ["git", "rev-list"]:
            out = ahead
        else:
            out = prs
        return subprocess.CompletedProcess(cmd, 0, out, "")
    return run


def test_an_empty_branch_is_not_work(monkeypatch):
    """2026-08-18: both sorties wrote their change, backgrounded the full test
    suite and ended the turn waiting on a notification a `-p` run never gets.
    The branch existed and pointed at master's tip, so this returned True, the
    claim was never released and the issue said nothing was wrong."""
    monkeypatch.setattr(qops_pickup.subprocess, "run",
                        _fake_git("fix/71-modifier-class-schema\n", "0"))
    assert qops_pickup.produced_work(REPO, "71") is False


def test_a_branch_with_a_commit_is_work(monkeypatch):
    monkeypatch.setattr(qops_pickup.subprocess, "run",
                        _fake_git("fix/71-modifier-class-schema\n", "1"))
    assert qops_pickup.produced_work(REPO, "71") is True


def test_an_empty_branch_with_a_pr_is_still_work(monkeypatch):
    """The commit may live only on the remote. A PR is evidence either way."""
    monkeypatch.setattr(qops_pickup.subprocess, "run",
                        _fake_git("fix/71-x\n", "0", prs='[{"number": 161}]'))
    assert qops_pickup.produced_work(REPO, "71") is True


def test_the_launch_prompt_forbids_waiting_on_a_backgrounded_command():
    """The instruction half. `produced_work` is the assertion half - an
    instruction in a prompt is a preference, not a control (GL-53)."""
    prompt = qops_pickup.launch_prompt("116")
    assert "background" in prompt
    assert "only the tests you touched" in prompt


# --------------------------------------------------------------------------
# reconcile — #150. `advance` fires on the `closed` pull-request event, and
# GitHub raises no such event for a merge its own GITHUB_TOKEN caused, so the
# unattended path advanced nothing. This reads state instead of an event.
# --------------------------------------------------------------------------

from qops import reconcile as reconcilemod  # noqa: E402


class FakeGh:
    """A gh double. Holds issues by number and applies label edits, so a second
    reconcile run sees the first run's effect — which is what idempotency
    means here."""

    def __init__(self, prs, issues, fail_on=None):
        self.prs, self.issues, self.fail_on = prs, issues, fail_on
        self.calls = []

    def __call__(self, args):
        self.calls.append(args)
        if self.fail_on and args[:2] == self.fail_on:
            raise RuntimeError("gh boom")
        if args[0] == "pr":
            return json.dumps(self.prs)
        num = args[2]
        if args[1] == "view":
            return json.dumps(self.issues[num])
        if args[1] == "edit":
            names = {l["name"] for l in self.issues[num]["labels"]}
            for i, a in enumerate(args):
                if a == "--add-label":
                    names.add(args[i + 1])
                if a == "--remove-label":
                    names.discard(args[i + 1])
            self.issues[num]["labels"] = [{"name": n} for n in sorted(names)]
        return ""


def _building(num="59"):
    return {num: {"state": "OPEN", "labels": [{"name": "state:building"},
                                              {"name": "ready:auto"},
                                              {"name": "gate:machine"}]}}


def test_reconcile_advances_a_merged_sortie_whose_row_is_still_in_flight():
    gh = FakeGh([{"number": 148, "headRefName": "fix/59-orphan-gap"}], _building())
    report = reconcilemod.reconcile("o/r", run=gh)
    assert report["advanced"] == [("59", "148")]
    names = {l["name"] for l in gh.issues["59"]["labels"]}
    assert "state:done" in names
    assert "ready:auto" not in names and "state:building" not in names


def test_reconcile_is_idempotent():
    """It runs against rows `advance` already handled correctly — a human-token
    merge still fires `advance` (PR #146). Twice must be once."""
    gh = FakeGh([{"number": 148, "headRefName": "fix/59-orphan-gap"}], _building())
    reconcilemod.reconcile("o/r", run=gh)
    edits = len([c for c in gh.calls if c[:2] == ["issue", "edit"]])
    second = reconcilemod.reconcile("o/r", run=gh)
    assert second["advanced"] == []
    assert second["skipped"] == [("59", "already state:done")]
    assert len([c for c in gh.calls if c[:2] == ["issue", "edit"]]) == edits


def test_reconcile_labels_and_never_closes():
    """ADR-0020's limit, same as `advance`: a merge means the code landed, not
    that the sortie is judged. Closing stays the owner's."""
    gh = FakeGh([{"number": 148, "headRefName": "fix/59-orphan-gap"}], _building())
    reconcilemod.reconcile("o/r", run=gh)
    assert not [c for c in gh.calls if c[:2] == ["issue", "close"]]


def test_reconcile_skips_a_branch_that_names_no_issue():
    gh = FakeGh([{"number": 146, "headRefName": "no-issue/triage-sweep"}], {})
    report = reconcilemod.reconcile("o/r", run=gh)
    assert report["skipped"] == [("146", "branch names no issue")]
    assert not [c for c in gh.calls if c[0] == "issue"]


def test_reconcile_skips_an_issue_the_owner_already_closed():
    issues = {"59": {"state": "CLOSED", "labels": [{"name": "state:building"}]}}
    gh = FakeGh([{"number": 148, "headRefName": "fix/59-orphan-gap"}], issues)
    report = reconcilemod.reconcile("o/r", run=gh)
    assert report["skipped"] == [("59", "issue already closed")]


def test_a_failed_row_leaves_a_reason_behind_and_fails_the_run(tmp_path):
    """CLAUDE.md: a swallowed per-item exception writes a status plus a reason
    onto the row, and the run still fails once after the loop."""
    gh = FakeGh([{"number": 148, "headRefName": "fix/59-orphan-gap"}], _building(),
                fail_on=["issue", "view"])
    report = reconcilemod.reconcile("o/r", run=gh)
    assert [i for i, _ in report["failed"]] == ["59"]
    comments = [c for c in gh.calls if c[:2] == ["issue", "comment"]]
    assert comments and "could not advance" in comments[0][-1]

    def fake(repo, limit=50, run=None):
        return {"advanced": [], "skipped": [], "failed": [("59", "gh boom")]}

    saved, reconcilemod.reconcile = reconcilemod.reconcile, fake
    try:
        assert reconcilemod.main([], tmp_path, {"repo": "o/r"}) == 1
    finally:
        reconcilemod.reconcile = saved


def test_reconcile_reads_the_issue_from_the_branch_not_from_closes():
    """#116 proved `Closes #n` is a preference, not a control (GL-53)."""
    assert reconcilemod.issue_number("fix/59-orphan-gap") == "59"
    assert reconcilemod.issue_number("no-issue/sweep") is None
    assert reconcilemod.issue_number("") is None


def test_the_reconciler_runs_on_the_digest_cadence_not_a_third_one():
    wf = (REPO / ".github" / "workflows" / "digest.yml").read_text(encoding="utf-8")
    assert "qops reconcile" in wf
    assert "needs: reconcile" in wf
    assert wf.count("cron:") == 1


# --------------------------------------------------------------------------
# metrics --state — #153. Nine rows read
# "Windows Subsystem for Linux has no installed distributions." because
# `bash -lc` on the ADR-0009 cron host is the WSL launcher and the exit code
# was never checked. A table that looks measured is worse than an empty one.
# --------------------------------------------------------------------------

def test_every_state_row_is_a_number_on_this_host():
    text, failures = metrics.state_report(REPO, qconfig.load(REPO))
    assert failures == []
    rows = [l for l in text.splitlines() if l.startswith("| ") and "---" not in l]
    values = [l.split("|")[2].strip() for l in rows[1:]]
    assert len(values) == len(metrics._STATE_ROWS)
    for v in values:
        # `n/a` is legal for one row only: a shallow CI checkout has no local
        # default-branch ref. Everything else must be a number.
        assert v.isdigit() or v == "n/a", f"state report row is not a number: {v!r}"
    assert values.count("n/a") <= 1


def test_a_failed_probe_is_marked_and_exits_non_zero(tmp_path, monkeypatch):
    def boom(root, cfg):
        raise RuntimeError("no such thing")

    monkeypatch.setattr(metrics, "_STATE_ROWS", [("Broken", "false", boom)])
    (tmp_path / ".qops").mkdir()
    monkeypatch.setattr(metrics, "_git", lambda root, *a: ["deadbee"])
    text, failures = metrics.state_report(tmp_path, qconfig.load(REPO))
    assert "| Broken | FAILED |" in text
    assert failures and "no such thing" in failures[0]
    assert metrics.main(["--state"], tmp_path, qconfig.load(REPO)) == 1


# --------------------------------------------------------------------------
# doctor's label invariants — #147. Each is the machine version of something a
# human got wrong in the week of 2026-08-17.
# --------------------------------------------------------------------------

def test_every_label_named_in_the_config_is_in_the_taxonomy():
    """`ci.status_issue_label: qops:status` was declared nowhere, so the
    importer never created it and digest.yml failed at 06:00 UTC for weeks."""
    assert install.undeclared_labels(qconfig.load(REPO)) == []


def test_an_undeclared_label_is_caught():
    cfg = json.loads(json.dumps(qconfig.load(REPO), default=str))
    cfg["ci"]["status_issue_label"] = "qops:nope"
    assert any("qops:nope" in p for p in install.undeclared_labels(cfg))


def test_an_open_issue_carries_exactly_one_type_state_and_gate():
    cfg = qconfig.load(REPO)
    issues = [
        {"number": 1, "labels": [{"name": "type:code"}, {"name": "state:planned"},
                                 {"name": "gate:machine"}]},
        {"number": 2, "labels": [{"name": "type:code"}, {"name": "state:planned"},
                                 {"name": "state:building"},
                                 {"name": "gate:machine"}]},
        {"number": 3, "labels": [{"name": "type:code"}, {"name": "state:triage"},
                                 {"name": "gate:none"}]},
    ]
    problems = install.issue_invariants(issues, cfg)
    assert not [p for p in problems if p.startswith("#1")]
    assert any("#2" in p and "2 `state:`" in p for p in problems)
    assert any("#3" in p and "gate:none" in p for p in problems)


def test_ready_auto_outside_state_planned_is_reported():
    """Finding 1: pickup-loop's eligible() requires state:planned, so the flag
    is inert and invisible anywhere else — it reads as a filled queue."""
    cfg = qconfig.load(REPO)
    issues = [{"number": 136, "labels": [{"name": "type:code"},
                                         {"name": "state:triage"},
                                         {"name": "gate:machine"},
                                         {"name": "ready:auto"}]}]
    assert any("#136" in p and "ready:auto" in p
               for p in install.issue_invariants(issues, cfg))


def test_doctor_does_not_require_the_network(capsys):
    """A doctor that cannot run offline is a worse instrument than one that
    says why it is quiet (#147)."""
    assert install.open_issues({}) is None
    assert "skipping the label invariant" in capsys.readouterr().out


# --------------------------------------------------------------------------
# portability — Phase 8's actual property, asserted rather than measured once
#
# The 2026-08-17 audit answered "does anything project-specific live outside
# .qops/config.yml" by grepping, correctly, on one day. A measurement holds
# until the next commit; a test holds. Four leaks were found by three separate
# passes over the same question, which is the argument for making it a check.
# --------------------------------------------------------------------------

# What the qops repo takes with it (PRD §Scope-in). `.qops/config.yml` is the
# one file allowed to name the project, so it is not here.
SUBSTRATE_PATHS = ["qops", "scripts/qops_import.py", "scripts/qops_pickup.py",
                   "tests/test_qops.py", ".claude/agents", "docs/agents"]


def _substrate_files():
    for rel in SUBSTRATE_PATHS:
        p = REPO / rel
        if p.is_dir():
            yield from (f for f in p.rglob("*")
                        if f.is_file() and f.suffix in (".py", ".md", ".tmpl",
                                                        ".yml", ".json"))
        elif p.exists():
            yield p


def test_no_project_specific_string_outside_the_config():
    cfg = qconfig.load(REPO)
    # The list is the config's, and the project's own name has to be ON it —
    # asserted per-project, because in the substrate repo `project: qops` names
    # the substrate and every file is allowed to say it.
    forbidden = {t["name"].lower() for t in cfg.get("tripwires", [])}
    forbidden |= {w.lower() for w in cfg.get("portability_forbidden", [])}
    leaks = []
    for f in _substrate_files():
        text = f.read_text(encoding="utf-8", errors="ignore").lower()
        for word in sorted(forbidden):
            if word in text:
                leaks.append(f"{f.relative_to(REPO)}: {word}")
    assert leaks == [], "project-specific strings in substrate source:\n" + \
                        "\n".join(leaks)


def _literals(path):
    """Every string literal in a module that is not a docstring, plus whether
    anything is called with `shell=True`.

    Comments and docstrings are excluded on purpose: `metrics.py` explains at
    length why it no longer shells through `bash`, and an assertion that cannot
    tell the explanation from the defect is one nobody can leave in place.
    """
    import ast
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)) and ast.get_docstring(node) is not None:
            docs.add(id(node.body[0].value))
    strings = [n.value for n in ast.walk(tree)
               if isinstance(n, ast.Constant) and isinstance(n.value, str)
               and id(n) not in docs]
    shell = any(isinstance(n, ast.keyword) and n.arg == "shell"
                and getattr(n.value, "value", None) is True for n in ast.walk(tree))
    return strings, shell


@pytest.mark.parametrize("needle", ["bash", "sh -c", "/bin/", "python3"])
def test_no_substrate_module_assumes_posix(needle):
    """ADR-0009: the cron host is a Windows desktop. `metrics.state_report`
    shelled its nine rows through `bash -lc`, where `bash` is the WSL launcher,
    and captured its refusal as data — nine garbage numbers in a table that
    looked fine (PRD P8.1, fourth leak). `python:` is in config precisely so
    nothing else has to guess at an interpreter."""
    hits = []
    for f in _substrate_files():
        # the test file itself has to name the needles in order to look for them
        if f.suffix != ".py" or f.parent.name == "tests":
            continue
        strings, shell = _literals(f)
        if any(needle in s for s in strings) or shell:
            hits.append(str(f.relative_to(REPO)))
    assert hits == [], f"{needle!r} in {hits}"


def test_every_label_the_config_names_is_in_its_own_taxonomy():
    """`ci.status_issue_label: qops:status` lived only under `ci:`, so the
    importer never created it and the daily digest failed at 06:00 UTC for
    weeks (#136). Cheap, and it is the assertion that would have caught it."""
    cfg = qconfig.load(REPO)
    taxonomy = cfg["labels"]
    declared = set(taxonomy.get("flags", []))
    for ns in ("type", "state", "mission", "gate"):
        declared |= {f"{ns}:{v}" for v in taxonomy.get(ns, [])}

    def label_like(node):
        if isinstance(node, str):
            if ":" in node and not any(c in node for c in " /\t"):
                yield node
        elif isinstance(node, dict):
            for k, v in node.items():
                if k != "labels":
                    yield from label_like(v)
        elif isinstance(node, list):
            for v in node:
                yield from label_like(v)

    undeclared = sorted(set(label_like(cfg)) - declared)
    assert undeclared == [], f"named in the config, absent from labels: {undeclared}"


def test_the_status_issue_is_exempt_from_the_issue_invariants():
    """digest.yml opens the pinned status issue with exactly one label, and
    `issue_invariants` rejected it — so `doctor` reported three problems it
    could never clear, and a gate that can never be green stops being read
    (#167). Machine-authored bookkeeping is not a sortie."""
    cfg = qconfig.load(REPO)
    label = cfg["ci"]["status_issue_label"]
    assert install.issue_invariants(
        [{"number": 1, "labels": [{"name": label}]}], cfg) == []
    # an ordinary issue carrying one label is still three problems
    assert len(install.issue_invariants(
        [{"number": 2, "labels": [{"name": "type:code"}]}], cfg)) == 2


def test_ready_auto_must_name_a_test(capsys):
    """Triage R8. The full suite runs longer than one Bash call may, and a
    `claude -p` process exits with its turn, so a sortie whose evidence of
    doneness IS the full suite cannot finish (attempt 2, #57/#71). The rule
    existed only as launch-prompt prose, which by GL-53 is a preference."""
    cfg = qconfig.load(REPO)
    labels = [{"name": "type:code"}, {"name": "state:planned"},
              {"name": "gate:machine"}, {"name": "ready:auto"}]
    vague = install.issue_invariants(
        [{"number": 1, "labels": labels, "body": "make the thing work"}], cfg)
    assert any("names no test" in p for p in vague)
    named = install.issue_invariants(
        [{"number": 2, "labels": labels,
          "body": "Acceptance: tests/test_qops.py passes."}], cfg)
    assert named == []
    # A caller with no body cannot answer the question, and silence is not a pass
    # it can grant either — the rule simply does not fire.
    assert install.issue_invariants([{"number": 3, "labels": labels}], cfg) == []


def test_the_brief_says_which_tracker_it_read():
    """Two trackers from Phase 8 on. A session reading the wrong one is the
    dominant new failure mode, so the repo is named every time, not on demand
    (PRD §Risks, non-negotiable)."""
    cfg = qconfig.load(REPO)
    state = {"branch": "gl-63", "dirty": [], "worktrees": 1, "issue": None,
             "resume": "", "ahead": 0}
    assert cfg["repo"] in briefmod.render_from(state, cfg)
    assert "no `repo:`" in briefmod.render_from(state, dict(cfg, repo=None))


# --- #168, second pass: a subcommand is not a substring --------------------

@pytest.mark.parametrize("command", [
    "git stash push -m wip -- tests/x.py && git checkout master",
    "git stash push tests/a.py tests/b.py",
    "git log --oneline master",
    "git diff master",
    "git branch -d master-notes",
])
def test_a_git_subcommand_is_not_a_substring(command):
    """`git stash push` is not `git push`, and `git log master` names no push
    target. The string form could not tell, and refused all five (#168)."""
    assert guard.check("Bash", {"command": command}, CTX, SYNTHETIC) is None, command


def test_args_stop_at_the_shell_separator():
    """`git commit && git checkout master` must not read `master` as an
    argument to anything before the `&&`."""
    toks = guard.argv_tokens("git stash push a.py && git checkout master")
    assert guard.git_commands(toks) == [("stash", ["push", "a.py"]),
                                        ("checkout", ["master"])]


def test_git_options_are_skipped_to_find_the_subcommand():
    toks = guard.argv_tokens("git -c core.pager=cat --no-pager push origin master")
    assert guard.git_commands(toks) == [("push", ["origin", "master"])]
    assert guard.check("Bash", {"command": "git -c a=b push origin master"},
                       FEATURE, SYNTHETIC)


def test_the_importer_taxonomy_parser_matches_the_yaml():
    """`qops_import.load_taxonomy()` re-reads the config with regexes rather
    than a YAML parser, and it is what `--labels` creates. If it silently drops
    a namespace, a fresh repo comes up missing exactly those labels and the
    picker's query returns empty while exiting 0."""
    sys.path.insert(0, str(REPO / "scripts"))
    import qops_import                                   # noqa: E402

    cfg = qconfig.load(REPO)
    taxonomy = cfg["labels"]
    expected = set(taxonomy.get("flags", []))
    for ns in ("type", "state", "mission", "gate"):
        expected |= {f"{ns}:{v}" for v in taxonomy.get(ns, [])}
    labels, milestones = qops_import.load_taxonomy()
    assert labels == expected
    assert milestones == set(cfg["milestones"])


def test_the_brief_only_points_at_files_that_exist(tmp_path):
    """A fixed list of filenames is a dangling pointer in the first repo that
    does not have one of them, and it sits in the hot path of every session."""
    cfg = qconfig.load(REPO)
    assert briefmod._pointers(tmp_path) == []
    (tmp_path / "CLAUDE.md").write_text("x", encoding="utf-8")
    assert briefmod._pointers(tmp_path) == ["constraints: CLAUDE.md"]
    state = {"branch": "gl-63", "dirty": [], "worktrees": 1, "issue": None,
             "resume": "", "ahead": 0, "pointers": []}
    text = briefmod.render_from(state, cfg)
    assert "CONTEXT.md" not in text and cfg["repo"] in text
