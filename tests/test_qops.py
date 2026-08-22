"""qops substrate — the assertions that make the rules real.

CLAUDE.md's own convention: an instruction in a prompt is a preference, not a
control (GL-53). Every rule qops states in a workflow, a hook or a prompt has an
assertion here.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from qops import config as qconfig  # noqa: E402
from qops import review as reviewmod  # noqa: E402
from qops import guard, init as initmod, install, ledger, metrics, brief as briefmod  # noqa: E402


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

def test_install_renders_the_seven_workflows(tmp_path):
    written = install.render_all(tmp_path, qconfig.load(REPO))
    names = {Path(p).name for p in written}
    assert names == {"test.yml", "gate.yml", "guard.yml", "digest.yml",
                     "groom.yml", "automerge.yml", "reviewer.yml"}
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
# a rendered workflow has to run in a repo shaped UNLIKE the one that rendered
# it (ADR-0024)
#
# #1, #21 and the same hole still open in digest.yml's reconcile job were one
# defect three times: each install step was calibrated against a single
# consumer's shape, and each only surfaced when a repo of a different shape
# rendered it. `tripwires` and `doc-links` are required checks, so in a
# consuming repo this class does not degrade — it blocks every PR.
# --------------------------------------------------------------------------

# Every workflow with a job that runs Python. groom.yml and automerge.yml have
# none: they are `gh` and `wc -l`, and adding a Python job to either is what
# this list is here to notice.
PYTHON_JOB_WORKFLOWS = ("test.yml", "gate.yml", "guard.yml", "digest.yml")


@pytest.mark.parametrize("name", PYTHON_JOB_WORKFLOWS)
def test_every_job_that_runs_python_uses_the_one_install_block(name):
    """One block, no second way to install anything. Three copies is how the
    three bugs happened, and a fourth copy is how the fourth one would."""
    text = install.render_one(name, qconfig.load(REPO))
    jobs = text.count("actions/setup-python")
    assert jobs >= 1, f"{name} is in PYTHON_JOB_WORKFLOWS and sets up no Python"
    block = install.INSTALL_DEPS.replace("\n", "\n" + install._RUN_INDENT)
    assert text.count(block) == jobs, \
        f"{name}: {jobs} Python job(s), {text.count(block)} carrying the block"
    assert text.count("pip install") == jobs * install.INSTALL_DEPS.count("pip install"), \
        f"{name} installs something outside INSTALL_DEPS"


# shape -> (the files that declare its dependencies, the install it must reach)
REPO_SHAPES = {
    "requirements": ({"requirements.txt": "-e .\n",
                      "requirements-dev.txt": "pytest\n"},
                     "install -r requirements.txt"),
    "pyproject": ({"pyproject.toml": "[project]\nname = 'x'\n"},
                  "install -e ."),
    "neither": ({}, "install pyyaml"),
}


@pytest.mark.parametrize("shape", sorted(REPO_SHAPES))
def test_the_install_block_reaches_qops_in_every_repo_shape(tmp_path, shape):
    """The property nothing asserted before ADR-0024.

    Executed, not pattern-matched: #1 was a branch that read correctly and
    never fired. `python` and `pip` are shell functions that log their argv, so
    nothing is installed and no network is touched — what is under test is
    which branch the block takes in a tree of each shape.
    """
    sh = shutil.which("bash")
    if sh is None:                  # the Windows cron host; ADR-0009
        pytest.skip("no bash here — guard.yml's ubuntu runner is the gate")
    files, expected = REPO_SHAPES[shape]
    repo = tmp_path / shape
    (repo / "qops").mkdir(parents=True)     # the vendored shape's package
    for fname, body in files.items():
        (repo / fname).write_text(body, encoding="utf-8")

    script = ('python() { echo "python $*" >> pip.log; }\n'
              'pip() { echo "pip $*" >> pip.log; }\n' + install.INSTALL_DEPS)
    run = subprocess.run([sh, "-c", script], cwd=repo, capture_output=True,
                         text=True)
    assert run.returncode == 0, run.stderr
    log = (repo / "pip.log").read_text(encoding="utf-8")
    assert expected in log, \
        f"a {shape}-shaped repo never reaches `{expected}`:\n{log}"


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


def test_the_reconciler_runs_where_a_bot_merge_can_reach_it():
    """A `GITHUB_TOKEN` merge starts no workflow run, so `advance` never fires
    on the auto-merge path — diagnosed and mitigated in #150 by making
    `reconcile` a state-reading backstop rather than an event listener. The
    backstop then ran only on `digest_cron`, so a shipped row stayed
    `state:building` for up to 24h and `metrics` S9 counted it as in flight the
    whole time (#66).

    The assertion is on the *trigger*, not on the step: a job that no event
    starts is exactly what this row is about, and a step-level check would pass
    for the job that was already dead.
    """
    text = _automerge_text()
    assert "python -m qops reconcile" in text
    job = text.split("\n  reconcile:", 1)
    assert len(job) == 2, "automerge.yml has no `reconcile` job"
    # Up to the next top-level job key, or the end of file if it is the last.
    body = re.split(r"\n  \w+:\n", job[1], maxsplit=1)[0]
    # It must not inherit `enable`'s gates: those exclude `closed`, and a
    # reconcile that only runs on the events `enable` accepts still never sees
    # the state a merge left behind.
    assert "action != 'closed'" not in body
    assert "issues: write" in body
    # A fork's token cannot write issues, and its PR is a taste decision.
    assert "head.repo.full_name == github.repository" in body


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


def test_automerge_squashes():
    """Branch deletion is the repo's `delete_branch_on_merge` setting since
    qops#3 - it was a flag on the call that fix removed."""
    assert "mergeMethod: SQUASH" in _automerge_text()


def test_automerge_queues_and_never_merges_now():
    """ADR-0020 authorises handing the merge to the required checks. It does
    not authorise merging.

    `gh pr merge --auto` cannot tell the two apart: with no required checks a
    PR is mergeable the instant it opens, and it merges on the spot. That is
    what happened to this repo's second PR - merged ten seconds before its own
    gate finished (qops#3). `enablePullRequestAutoMerge` fails instead, and the
    step treats that failure as a stop.

    Note what the old assertion did: it looked for `--auto` in the rendered
    text, and it kept passing after the call was removed, because the word
    survived in the comment explaining why. An assertion that a string appears
    somewhere is not an assertion about behaviour.
    """
    text = _automerge_text()
    step = text.split("hand the merge to the required checks", 1)[1]
    # Comments, not code. The step explains at length why `gh pr merge --auto`
    # was wrong, and an assertion that cannot tell the explanation from the
    # thing it explains is the assertion this test replaced.
    run = " ".join(l for l in step.splitlines()
                   if not l.strip().startswith("#"))
    assert "enablePullRequestAutoMerge" in run
    assert "gh pr merge" not in run, "the job merges instead of queueing"
    assert "exit 1" in run, "a failure to queue must fail the job, not pass it"
    assert "branch protection" in run, "the refusal must name its cause"


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


def test_advance_closes_only_behind_a_gate_machine_check():
    """ADR-0025: a merge means the code landed, and on `gate:taste` work that
    is not the sortie being judged — the owner's read is the only judgement
    there is, so the close there stays theirs. On `gate:machine` there is no
    judgement left to give, so `advance` closes it, gated on the label."""
    text = _automerge_text()
    assert "gh issue close" in text
    assert "*gate:machine*" in text
    assert "*no-auto*" in text


def test_advance_does_not_depend_on_the_agent_writing_closes():
    """The branch already carries the issue number (ADR-0019) and the workflow
    already parses it. #116's PR carried no `Closes` line and shipped anyway."""
    advance = _automerge_text().split("\n  advance:")[1]
    assert "^[a-z]+/([0-9]+)-" in advance
    assert "$REF" in advance
    assert "${{ github.event.pull_request.head.ref }}" not in advance.split("env:")[0]


def test_declining_to_automerge_lands_state_review_regardless_of_prior_state():
    """#5. `gh issue edit` fails as a whole when `--remove-label` names a label
    the issue does not carry, and the old code named `state:building`
    unconditionally. A `gate:taste` PR whose issue is `state:planned` (or any
    other `state:`) must still land on `state:review`, exactly the way
    `advance` already removes every `state:` label rather than one assumed
    one."""
    text = _automerge_text()
    enable = text.split("\n  advance:")[0]
    assert "--add-label state:review" in enable
    for state in ("triage", "planned", "building", "gate", "blocked"):
        assert f"--remove-label state:{state}" in enable
    assert "--remove-label state:building || true" not in text
    assert "--remove-label state:blocked \\\n                || true" not in enable, \
        "a failed label edit must not be swallowed silently"


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


def _ledger_with(tmp_path, *records) -> Path:
    root = tmp_path / "root"
    (root / ".qops").mkdir(parents=True)
    (root / ".qops" / "ledger.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return root


def _ago(hours: float) -> str:
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc)
            - timedelta(hours=hours)).isoformat(timespec="seconds")


def test_a_picker_that_dies_before_it_prints_leaves_a_record(tmp_path):
    """The task returned 1 at 09:00, 10:00, 11:00 and 12:00 on 2026-08-21 and
    nothing anywhere said so (#76). Every silence the picker had already fixed
    — `candidates()` returning None, #48, #49, #50 — assumes the process got
    far enough to print. A run that dies at import leaves the loop exactly as
    dead as a disabled task, and says exactly as much.

    So the evidence is a heartbeat, read as state: a completed run records one,
    and the absence of a recent one is the report. It covers the failures no
    in-process handler can — a bad interpreter, a missing root, an import that
    raises — because it does not depend on the run surviving to speak.
    """
    cfg = qconfig.load(REPO)
    hours = cfg["pickup_max_silence_hours"]

    fresh = _ledger_with(tmp_path / "a", {"ts": _ago(hours / 2),
                                          "event": "pickup_ran"})
    assert briefmod.picker_silence(fresh, cfg) is None

    stale = _ledger_with(tmp_path / "b", {"ts": _ago(hours * 2),
                                          "event": "pickup_ran"})
    said = briefmod.picker_silence(stale, cfg)
    assert said and "pickup-loop" in said

    # Never enabled is not a failure. A loop that has never run is silent on
    # purpose (it ships disabled), and nagging about it would train the reader
    # to skip the line — #167's failure, and the one thing this must not do.
    never = _ledger_with(tmp_path / "c", {"ts": _ago(99), "event": "session_start"})
    assert briefmod.picker_silence(never, cfg) is None

    # One report, however many runs failed: it is a state read, so four dead
    # runs and one dead run say the same thing once.
    many = _ledger_with(tmp_path / "d", {"ts": _ago(hours * 3),
                                         "event": "pickup_ran"},
                        {"ts": _ago(hours * 2), "event": "session_start"},
                        {"ts": _ago(hours), "event": "session_start"})
    said_many = briefmod.picker_silence(many, cfg)
    assert said_many and "\n" not in said_many

    # And it reaches the brief, which is where a session finds out.
    state = briefmod.collect(stale, cfg)
    assert "pickup-loop" in briefmod.render_from(state, cfg)


def test_a_completed_picker_run_records_its_heartbeat():
    """The other half: nothing clears the report but a run that finished, and
    no human edit is involved in clearing it."""
    src = (REPO / "scripts" / "qops_pickup.py").read_text(encoding="utf-8")
    assert 'ledger.append(root, "pickup_ran"' in src


def test_the_picker_loads_the_substrate_from_the_root_it_names(tmp_path):
    """Running `python <root>/scripts/qops_pickup.py` puts *the script's
    directory* on `sys.path[0]`, not the repo root, so `import qops` reached
    past the repo into site-packages. Every unattended run this week executed
    the repo's `scripts/` against a stale installed library, and it only
    surfaced when #71 moved a name (#74). CI cannot see it: `INSTALL_DEPS`
    installs editable, so package and repo are one tree there.

    A subprocess, because the defect exists only in how the process starts.
    """
    shadow = tmp_path / "shadow"
    (shadow / "qops").mkdir(parents=True)
    (shadow / "qops" / "__init__.py").write_text("", encoding="utf-8")
    # A stale substrate is not a missing one: the module imports, the names
    # are gone. That is exactly the shape the old ModuleNotFoundError guard
    # could not catch.
    (shadow / "qops" / "install.py").write_text("", encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(shadow)}
    out = subprocess.run([sys.executable, str(REPO / "scripts" / "qops_pickup.py"),
                          "--root", str(REPO)], capture_output=True, text=True,
                         encoding="utf-8", env=env, cwd=str(tmp_path), timeout=120)
    # Not the exit code: reading the tracker needs `gh` and a token, which a
    # CI `test` job has neither of, and that would make this assert the
    # environment rather than the import (#65). The root line is printed after
    # the imports resolve and before anything reaches the network, so it is
    # exactly the evidence this row is about.
    assert "ImportError" not in out.stderr, out.stderr
    assert "pickup-loop: root" in out.stdout, out.stderr


def test_the_suite_does_not_read_the_runners_env():
    """The fixture is in force, asserted from inside a test rather than by
    reading `conftest.py` — the failure this closes is a test believing the
    environment is clean when it is not (#65)."""
    from conftest import RUNNER_ENV
    assert not [n for n in RUNNER_ENV if n in os.environ]
    # And the whole set is covered: every name the suite branches on.
    src = (REPO / "tests" / "test_qops.py").read_text(encoding="utf-8")
    for name in ("GITHUB_BASE_REF", "GITHUB_HEAD_REF", "QOPS_STRICT",
                 "QOPS_UNATTENDED"):
        if f'"{name}"' in src:
            assert name in RUNNER_ENV, name


def test_launch_marks_the_session_unattended():
    assert qops_pickup.launch_env()["QOPS_UNATTENDED"] == "1"


def test_guard_refuses_a_sandbox_escape_when_unattended():
    cfg = qconfig.load(REPO)
    payload = {"command": "git checkout -b feat/116-x", "dangerouslyDisableSandbox": True}
    ctx = {"branch": "master", "worktrees": 1, "unattended": True}
    assert "unattended" in (guard.check("Bash", payload, ctx, cfg) or "")
    ctx["unattended"] = False
    assert guard.check("Bash", payload, ctx, cfg) is None


# --------------------------------------------------------------------------
# ADR-0023 / #26 — `origin:` is set at filing, by which path filed it, never
# inferred afterwards. It is the input to the grant: on `origin:owner` the
# filing IS the grant, so an unattended session that could write `origin:owner`
# could grant itself autonomy. The guard is where that is refused, because an
# instruction in a prompt is a preference, not a control (CLAUDE.md).
# --------------------------------------------------------------------------

def _filing(labels: str) -> dict:
    return {"command": f'gh issue create --title "x" --body "y" --label {labels}'}


def test_the_origin_label_is_the_one_the_session_can_honestly_claim():
    cfg = qconfig.load(REPO)
    owner = {"branch": "master", "worktrees": 1, "unattended": False}
    agent = {**owner, "unattended": True}

    assert guard.check("Bash", _filing("type:code,state:triage,gate:machine,"
                                       "origin:owner"), owner, cfg) is None
    assert guard.check("Bash", _filing("type:code,state:triage,gate:machine,"
                                       "origin:agent"), agent, cfg) is None

    # The hole this exists for: an unattended run filing a row as the owner's.
    refusal = guard.check("Bash", _filing("type:code,origin:owner"), agent, cfg)
    assert refusal and "origin:agent" in refusal

    # And the reverse, which is not an authority hole but is still a lie: it
    # would send an owner-filed row down the batch-approval path.
    refusal = guard.check("Bash", _filing("type:code,origin:agent"), owner, cfg)
    assert refusal and "origin:owner" in refusal


def test_a_filing_with_no_origin_is_refused():
    """Absence is the easy way around a check on the value. Every filing states
    one, so nothing reaches the tracker for `doctor` to infer about later."""
    cfg = qconfig.load(REPO)
    ctx = {"branch": "master", "worktrees": 1, "unattended": False}
    refusal = guard.check("Bash", _filing("type:code,state:triage"), ctx, cfg)
    assert refusal and "origin:" in refusal
    # Repeated --label flags are the same filing as one comma-joined list.
    ok = {"command": 'gh issue create --title "x" --label type:code '
                     '--label origin:owner'}
    assert guard.check("Bash", ok, ctx, cfg) is None


def test_the_guard_leaves_everything_that_is_not_a_filing_alone():
    """`gh issue edit`, `gh issue list`, a PR body quoting the label. The guard
    reads argv and decides from the parse (ADR-0021), so a mention is not a
    filing."""
    cfg = qconfig.load(REPO)
    ctx = {"branch": "master", "worktrees": 1, "unattended": True}
    for cmd in ('gh issue edit 26 --add-label state:planned',
                'gh issue list --label origin:owner',
                'gh pr create --title "x" --body "sets origin:owner at filing"'):
        assert guard.check("Bash", {"command": cmd}, ctx, cfg) is None


def test_every_open_row_carries_an_origin():
    """`validate.require_on_open` is where the presence check lives, the same
    shape `gate:` already uses — the guard holds the filing, this holds the
    tracker."""
    cfg = qconfig.load(REPO)
    assert "origin" in cfg["validate"]["require_on_open"]
    assert cfg["labels"]["origin"] == ["owner", "agent", "pending"]
    issues = [{"number": 1, "labels": [{"name": "type:code"},
                                       {"name": "state:triage"},
                                       {"name": "gate:machine"}]}]
    assert any("#1" in p and "origin" in p
               for p in install.issue_invariants(issues, cfg))


# --------------------------------------------------------------------------
# ADR-0029 — `origin:` means whose licence covers the row, not who typed it.
# `origin:pending` is the honest claim for an unattended filing that expects a
# parent link; `qops reconcile` derives it from that link, never from prose.
# --------------------------------------------------------------------------

def test_an_unattended_filing_may_claim_origin_pending():
    cfg = qconfig.load(REPO)
    agent = {"branch": "master", "worktrees": 1, "unattended": True}
    assert guard.check("Bash", _filing("type:code,origin:pending"),
                        agent, cfg) is None
    # Still refused: origin:owner cannot be self-granted, link or no link.
    refusal = guard.check("Bash", _filing("type:code,origin:owner"), agent, cfg)
    assert refusal and "origin:pending" in refusal


def test_origin_is_derived_from_the_link_and_never_claimed():
    """`derive_origin` reads a native sub-issue `parent` link — a tracker
    fact — never the body, the author, or the row's own claim."""
    parents = {"10": {"labels": [{"name": "origin:owner"}]},
               "11": {"labels": [{"name": "origin:agent"}]}}

    def run(args):
        if args[:2] == ["issue", "list"]:
            return json.dumps([{"number": 10}, {"number": 11}, {"number": 12}])
        if args[0] == "api":
            num = args[1].rsplit("/", 2)[1]
            if num in parents:
                return json.dumps(parents[num])
            raise RuntimeError("404 - no parent link")
        if args[:2] == ["issue", "edit"]:
            return ""
        raise AssertionError(f"unexpected call: {args}")

    report = reconcilemod.derive_origin("o/r", run=run)
    assert ("10", "owner") in report["derived"]
    assert ("11", "agent") in report["derived"]
    # #12 links to nothing: stays origin:pending, not inferred.
    assert any(i == "12" for i, _ in report["skipped"])


def test_a_pending_origin_derives_to_the_agent_parents_licence_not_owner():
    """Inheritance carries the licence the parent actually has (ADR-0029) —
    an origin:agent parent does not upgrade the child to origin:owner."""
    def run(args):
        if args[:2] == ["issue", "list"]:
            return json.dumps([{"number": 20}])
        if args[0] == "api":
            return json.dumps({"labels": [{"name": "origin:agent"}]})
        return ""

    report = reconcilemod.derive_origin("o/r", run=run)
    assert report["derived"] == [("20", "agent")]


def test_a_row_whose_blockers_all_closed_leaves_blocked():
    """#110: `Blocked by #83` on the body, #83 closed - one reconcile run
    moves the row `state:blocked` -> `state:triage`."""
    def run(args):
        if args[:2] == ["issue", "list"]:
            return json.dumps([{"number": 85}])
        if args[:2] == ["issue", "view"] and args[2] == "85":
            return json.dumps({"body": "Blocked by #83", "comments": []})
        if args[:2] == ["issue", "view"] and args[2] == "83":
            return json.dumps({"state": "CLOSED"})
        if args[:2] == ["issue", "edit"]:
            return ""
        raise AssertionError(f"unexpected call: {args}")

    report = reconcilemod.unblock_stale("o/r", run=run)
    assert report["unblocked"] == [("85", ["83"])]


def test_a_row_naming_an_open_blocker_stays_blocked_with_a_reason():
    def run(args):
        if args[:2] == ["issue", "list"]:
            return json.dumps([{"number": 106}])
        if args[:2] == ["issue", "view"] and args[2] == "106":
            return json.dumps({"body": "Blocked by #105", "comments": []})
        if args[:2] == ["issue", "view"] and args[2] == "105":
            return json.dumps({"state": "OPEN"})
        raise AssertionError(f"unexpected call: {args}")

    report = reconcilemod.unblock_stale("o/r", run=run)
    assert report["unblocked"] == []
    assert report["skipped"] == [("106", "blocked by open #105")]


def test_a_blocker_named_two_ways_where_only_one_closed_stays_blocked():
    """Negative: a row naming two blockers where only one closed must not be
    treated as fully unblocked."""
    def run(args):
        if args[:2] == ["issue", "list"]:
            return json.dumps([{"number": 1}])
        if args[:2] == ["issue", "view"] and args[2] == "1":
            return json.dumps({"body": "Blocked by #2, #3", "comments": []})
        if args[:2] == ["issue", "view"] and args[2] == "2":
            return json.dumps({"state": "CLOSED"})
        if args[:2] == ["issue", "view"] and args[2] == "3":
            return json.dumps({"state": "OPEN"})
        raise AssertionError(f"unexpected call: {args}")

    report = reconcilemod.unblock_stale("o/r", run=run)
    assert report["unblocked"] == []
    assert report["skipped"] == [("1", "blocked by open #3")]


def test_the_edge_is_honoured_from_a_comment_not_only_the_body():
    def run(args):
        if args[:2] == ["issue", "list"]:
            return json.dumps([{"number": 86}])
        if args[:2] == ["issue", "view"] and args[2] == "86":
            return json.dumps({"body": "no edge here",
                                "comments": [{"body": "Blocked by #82"}]})
        if args[:2] == ["issue", "view"] and args[2] == "82":
            return json.dumps({"state": "CLOSED"})
        if args[:2] == ["issue", "edit"]:
            return ""
        raise AssertionError(f"unexpected call: {args}")

    report = reconcilemod.unblock_stale("o/r", run=run)
    assert report["unblocked"] == [("86", ["82"])]


def test_a_row_with_no_blocked_by_line_anywhere_stays_blocked():
    def run(args):
        if args[:2] == ["issue", "list"]:
            return json.dumps([{"number": 9}])
        if args[:2] == ["issue", "view"] and args[2] == "9":
            return json.dumps({"body": "waiting on #83 to land",
                                "comments": []})
        raise AssertionError(f"unexpected call: {args}")

    report = reconcilemod.unblock_stale("o/r", run=run)
    assert report["unblocked"] == []
    assert report["skipped"] == [("9", "no Blocked by line")]


def test_origin_pending_is_never_auto_eligible():
    """`eligible()`'s second route requires `origin:owner`; `origin:pending`
    is neither that nor `ready:auto`, so a freshly filed child is briefly
    un-pickable, which ADR-0029 calls correct."""
    issue = {"number": 1, "body": "tests/test_x.py",
             "labels": [{"name": "state:planned"}, {"name": "gate:machine"},
                        {"name": "origin:pending"}]}
    assert install.eligible(issue) is False


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
    before = {"commits": set(), "prs": set()}
    assert qops_pickup.produced_work(REPO, "999999", before) is False


def _fake_git(branches: str, commits: str, prs: str = "[]"):
    """A subprocess double for launch_evidence's three shell-outs. `commits` is
    `git rev-list`'s stdout: one SHA per line, empty for a branch with none."""
    def run(cmd, **kw):
        if cmd[:2] == ["git", "branch"]:
            out = branches
        elif cmd[:2] == ["git", "rev-list"]:
            out = commits
        else:
            out = prs
        return subprocess.CompletedProcess(cmd, 0, out, "")
    return run


EMPTY_EVIDENCE = {"commits": set(), "prs": set()}


def test_an_empty_branch_is_not_work(monkeypatch):
    """2026-08-18: both sorties wrote their change, backgrounded the full test
    suite and ended the turn waiting on a notification a `-p` run never gets.
    The branch existed and pointed at master's tip, so this returned True, the
    claim was never released and the issue said nothing was wrong."""
    monkeypatch.setattr(qops_pickup.subprocess, "run",
                        _fake_git("fix/71-modifier-class-schema\n", ""))
    assert qops_pickup.produced_work(REPO, "71", EMPTY_EVIDENCE) is False


def test_a_branch_with_a_commit_is_work(monkeypatch):
    monkeypatch.setattr(qops_pickup.subprocess, "run",
                        _fake_git("fix/71-modifier-class-schema\n", "abc123\n"))
    assert qops_pickup.produced_work(REPO, "71", EMPTY_EVIDENCE) is True


def test_an_empty_branch_with_a_pr_is_still_work(monkeypatch):
    """The commit may live only on the remote. A PR is evidence either way."""
    monkeypatch.setattr(qops_pickup.subprocess, "run",
                        _fake_git("fix/71-x\n", "", prs='[{"number": 161}]'))
    assert qops_pickup.produced_work(REPO, "71", EMPTY_EVIDENCE) is True


def test_a_stale_merged_branch_is_not_this_run_s_work(monkeypatch):
    """#8: a squash-merged sortie's commits stay reachable from its branch
    forever, so a run that re-picks the same issue and writes nothing must not
    score the old branch as this run's work."""
    monkeypatch.setattr(qops_pickup.subprocess, "run",
                        _fake_git("fix/71-x\n", "abc123\n"))
    before = {"commits": {"abc123"}, "prs": set()}
    assert qops_pickup.produced_work(REPO, "71", before) is False


def test_a_stale_merged_branch_plus_a_new_commit_is_work(monkeypatch):
    monkeypatch.setattr(qops_pickup.subprocess, "run",
                        _fake_git("fix/71-x\n", "abc123\ndef456\n"))
    before = {"commits": {"abc123"}, "prs": set()}
    assert qops_pickup.produced_work(REPO, "71", before) is True


def test_a_pr_that_predates_the_claim_is_not_this_run_s_work(monkeypatch):
    """The PR fallback has the same shape as the branch case: a merged PR from
    a previous attempt must not count again."""
    monkeypatch.setattr(qops_pickup.subprocess, "run",
                        _fake_git("", "", prs='[{"number": 161}]'))
    before = {"commits": set(), "prs": {161}}
    assert qops_pickup.produced_work(REPO, "71", before) is False


def test_a_pr_opened_by_this_run_is_work(monkeypatch):
    monkeypatch.setattr(qops_pickup.subprocess, "run",
                        _fake_git("", "", prs='[{"number": 161}]'))
    before = {"commits": set(), "prs": set()}
    assert qops_pickup.produced_work(REPO, "71", before) is True


def test_the_launch_prompt_forbids_waiting_on_a_backgrounded_command():
    """The instruction half. `produced_work` is the assertion half - an
    instruction in a prompt is a preference, not a control (GL-53)."""
    prompt = qops_pickup.launch_prompt("116")
    assert "background" in prompt
    assert "only the tests you touched" in prompt


# --- the task names its root, and an unreadable queue is not an empty one ---
#
# The registered task's WorkingDirectory was empty (#12, and the source repo's
# #176), so once the picker stopped rooting off `__file__` it would have
# resolved its root from wherever the scheduler started it. `find_root()`
# returns cwd when it finds nothing, and the task was disabled, so the breakage
# would have stayed invisible until someone enabled it.

def _root(tmp_path, name="repo"):
    root = tmp_path / name
    (root / ".qops").mkdir(parents=True)
    (root / ".qops" / "config.yml").write_text("project: x\n", encoding="utf-8")
    return root


def test_the_task_takes_its_root_from_the_argument(tmp_path):
    root = _root(tmp_path)
    assert qops_pickup.repo_root(["--root", str(root), "--launch"]) == root.resolve()


def test_a_root_that_is_not_a_qops_root_is_refused_not_used(tmp_path, monkeypatch):
    """With two roots on one host the silent outcomes are the wrong repo's
    backlog or no repo at all, and the picker exits 0 on both."""
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as e:
        qops_pickup.repo_root([])
    assert "not a qops root" in str(e.value)
    with pytest.raises(SystemExit) as e:
        qops_pickup.repo_root(["--root", str(tmp_path)])
    assert "--root" in str(e.value)


def test_root_says_where_the_root_it_refused_came_from(tmp_path, monkeypatch):
    """Which of the two failures it is decides the repair: a task with no
    --root, or a --root pointing at the wrong path."""
    monkeypatch.chdir(tmp_path)
    derived = str(pytest.raises(SystemExit, qops_pickup.repo_root, []).value)
    assert "working directory" in derived


def test_a_failed_backlog_query_is_not_an_idle_queue(tmp_path, monkeypatch):
    """Both exit 0 through the picker unless the failure says so. A repo whose
    labels were never created returns empty and looks identical."""
    monkeypatch.setattr(qops_pickup.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess([], 1, "", "gh: not logged in"))
    assert qops_pickup.candidates(tmp_path) is None
    monkeypatch.setattr(qops_pickup.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess([], 0, "[]", ""))
    assert qops_pickup.candidates(tmp_path) == []


# --------------------------------------------------------------------------
# #82 / ADR-0029 §1 — the loop plans when it has nothing to build. Until this,
# `state:triage -> state:planned` was the one act in the chain only an owner
# session performed, so a backlog of 18 rows and an idle loop were the same
# picture. Building is never starved by planning: the plan pass runs only where
# the run would previously have stopped.
# --------------------------------------------------------------------------

def _row(number, state="state:triage", body="## Acceptance\n- it merges.\n",
         extra=(), updated="2026-08-01T00:00:00Z"):
    labels = [{"name": state}, {"name": "type:code"}, {"name": "gate:machine"},
              {"name": "origin:owner"}, *({"name": n} for n in extra)]
    return {"number": number, "title": f"row {number}", "labels": labels,
            "body": body, "updatedAt": updated}


def test_the_loop_plans_when_it_has_nothing_to_build(tmp_path, monkeypatch):
    """One `state:triage` row that passes the filing bar, nothing to build:
    the run plans that row and picks nothing else. A row available to build
    wins — planning never starves building."""
    root = _root(tmp_path)
    planned, launched = [], []
    monkeypatch.setattr(qops_pickup, "plan_argv",
                        lambda prompt, cfg: planned.append(prompt) or ["true"])
    monkeypatch.setattr(qops_pickup, "launch_argv",
                        lambda prompt: launched.append(prompt) or ["true"])
    monkeypatch.setattr(qops_pickup, "produced_plan", lambda *a, **k: True)
    monkeypatch.setattr(qops_pickup, "launch_evidence", lambda *a, **k: {})
    monkeypatch.setattr(qops_pickup, "produced_work", lambda *a, **k: True)
    monkeypatch.setattr(qops_pickup, "_review", lambda root: 0)
    monkeypatch.setattr(qops_pickup.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", ""))

    triage = _row(5)
    monkeypatch.setattr(qops_pickup, "backlog", lambda r: [triage])
    assert qops_pickup.main(["--root", str(root), "--launch"]) == 0
    assert len(planned) == 1 and "#5" in planned[0], planned
    assert launched == []

    # A row to build is picked and the plan pass does not run at all.
    planned.clear()
    buildable = _row(6, state="state:planned", extra=("ready:auto",),
                     body="## Acceptance\n- `tests/test_qops.py::test_x` passes.\n")
    monkeypatch.setattr(qops_pickup, "backlog", lambda r: [triage, buildable])
    assert qops_pickup.main(["--root", str(root), "--launch"]) == 0
    assert len(launched) == 1 and "#6" in launched[0], launched
    assert planned == []


@pytest.mark.parametrize("why,row", [
    ("no outcome stated", _row(7, body="It would be nice if this were faster.")),
    ("an epic", _row(8, extra=("type:epic",))),
    ("the owner is handling it", _row(9, extra=("no-auto",))),
    ("already planned", _row(10, state="state:planned")),
])
def test_the_planner_is_not_pointed_at_a_row_it_may_not_plan(why, row):
    """The filing bar is the gate (ADR-0028): a row stating no outcome cannot
    be planned into criteria, and guessing at one invents work the owner never
    licensed. An epic gets an interview and #84's decomposition, never a plan
    instead of one (ADR-0029 §4)."""
    assert not qops_pickup.plannable(row), why


def test_a_failed_plan_does_not_label_the_row_planned(tmp_path, monkeypatch):
    """The build path's release writes `state:planned`. On a planning run that
    failed, that would be the loop asserting the one thing the run did not do —
    and the row would go to the build queue with no plan on it."""
    root = _root(tmp_path)
    calls = []
    monkeypatch.setattr(qops_pickup.subprocess, "run",
                        lambda cmd, **kw: calls.append(cmd) or
                        subprocess.CompletedProcess(cmd, 0, "", ""))
    monkeypatch.setattr(qops_pickup, "plan_argv", lambda p, c: ["true"])
    monkeypatch.setattr(qops_pickup, "produced_plan", lambda *a, **k: False)
    monkeypatch.setattr(qops_pickup, "_review", lambda root: 0)
    monkeypatch.setattr(qops_pickup, "backlog", lambda r: [_row(5)])

    assert qops_pickup.main(["--root", str(root), "--launch"]) == 1
    edits = [c for c in calls if c[:3] == ["gh", "issue", "edit"]]
    assert not any("state:planned" in c for c in edits), edits
    # It still spends the row's strike budget, so a row nothing can plan stops
    # being planned hourly (#49).
    assert qops_pickup.strikes(root, "5") == 1


def test_the_planner_launch_reads_its_toolset_from_the_config(tmp_path):
    """The roster is `.qops/config.yml`, not a second copy in this file. The
    planner gets no Edit and no Write: it writes a plan onto the row through
    `gh`, and a planner that can edit the tree is a coder."""
    cfg = qconfig.load(REPO)
    argv = qops_pickup.plan_argv("plan #5", cfg)
    tools = argv[argv.index("--allowedTools") + 1].split(",")
    assert tools == cfg["agents"]["planner"]["tools"], tools
    assert "Edit" not in tools and "Write" not in tools
    for flag in qops_pickup.BLANKET_BYPASS:
        assert flag not in argv


def test_an_unreadable_queue_fails_the_run_and_an_empty_one_does_not(tmp_path, monkeypatch):
    root = _root(tmp_path)
    # `backlog()` is the seam since #82: one query, two filters over it.
    monkeypatch.setattr(qops_pickup, "backlog", lambda r: None)
    assert qops_pickup.main(["--root", str(root)]) == 1
    monkeypatch.setattr(qops_pickup, "backlog", lambda r: [])
    assert qops_pickup.main(["--root", str(root)]) == 0


def test_every_run_names_the_root_and_the_tracker_it_read(tmp_path, monkeypatch, capsys):
    """The log line that separates a healthy idle queue from a picker pointed
    at the wrong root. Without it both print `nothing eligible`."""
    root = _root(tmp_path)
    (root / ".qops" / "config.yml").write_text("project: x\nrepo: o/n\n",
                                               encoding="utf-8")
    monkeypatch.setattr(qops_pickup, "candidates", lambda r: [])
    qops_pickup.main(["--root", str(root)])
    out = capsys.readouterr().out
    assert str(root.resolve()) in out and "o/n" in out


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
        if args[1] == "close":
            self.issues[num]["state"] = "CLOSED"
        return ""


def _building(num="59"):
    return {num: {"state": "OPEN", "labels": [{"name": "state:building"},
                                              {"name": "ready:auto"},
                                              {"name": "gate:machine"}]}}


def test_reconcile_advances_a_merged_sortie_whose_row_is_still_in_flight():
    """`_building()` is `gate:machine`, so the row is both advanced and closed
    (ADR-0025) — the label transition is asserted regardless."""
    gh = FakeGh([{"number": 148, "headRefName": "fix/59-orphan-gap"}], _building())
    report = reconcilemod.reconcile("o/r", run=gh)
    assert report["closed"] == [("59", "148")]
    names = {l["name"] for l in gh.issues["59"]["labels"]}
    assert "state:done" in names
    assert "ready:auto" not in names and "state:building" not in names


def test_reconcile_is_idempotent():
    """It runs against rows `advance` already handled correctly — a human-token
    merge still fires `advance` (PR #146). Twice must be once. `_building()` is
    `gate:machine`, so the first run both advances and closes it; the second
    run finds it already closed and does nothing further."""
    gh = FakeGh([{"number": 148, "headRefName": "fix/59-orphan-gap"}], _building())
    reconcilemod.reconcile("o/r", run=gh)
    edits = len([c for c in gh.calls
                 if c[:2] in (["issue", "edit"], ["issue", "close"])])
    second = reconcilemod.reconcile("o/r", run=gh)
    assert second["advanced"] == [] and second["closed"] == []
    assert second["skipped"] == [("59", "issue already closed")]
    assert len([c for c in gh.calls
                if c[:2] in (["issue", "edit"], ["issue", "close"])]) == edits


def test_reconcile_closes_a_gate_machine_row_the_gate_already_judged():
    """ADR-0025: a `gate:machine` merge leaves nothing left to judge, so
    closing it is not a recurring owner action."""
    gh = FakeGh([{"number": 148, "headRefName": "fix/59-orphan-gap"}], _building())
    report = reconcilemod.reconcile("o/r", run=gh)
    assert report["advanced"] == [] and report["closed"] == [("59", "148")]
    closes = [c for c in gh.calls if c[:2] == ["issue", "close"]]
    assert closes and closes[0][2] == "59"


def test_reconcile_never_closes_a_gate_taste_row():
    """ADR-0020's limit, unchanged for `gate:taste`: a merge means the code
    landed, not that the sortie is judged. Closing that one stays the owner's."""
    issues = {"59": {"state": "OPEN", "labels": [{"name": "state:building"},
                                                 {"name": "ready:auto"},
                                                 {"name": "gate:taste"}]}}
    gh = FakeGh([{"number": 148, "headRefName": "fix/59-orphan-gap"}], issues)
    report = reconcilemod.reconcile("o/r", run=gh)
    assert report["advanced"] == [("59", "148")] and report["closed"] == []
    assert not [c for c in gh.calls if c[:2] == ["issue", "close"]]


def test_reconcile_heals_a_row_advance_already_labelled_but_never_closed():
    """The backstop half: a row that already carries `state:done` and
    `gate:machine` but is still open (e.g. #21/#23 — a hand merge advance also
    caught, from before ADR-0025) gets closed on the next reconcile run, not
    left to an owner to notice."""
    issues = {"59": {"state": "OPEN", "labels": [{"name": "state:done"},
                                                 {"name": "gate:machine"}]}}
    gh = FakeGh([{"number": 148, "headRefName": "fix/59-orphan-gap"}], issues)
    report = reconcilemod.reconcile("o/r", run=gh)
    assert report["closed"] == [("59", "148")]


def test_reconcile_no_auto_vetoes_the_close_same_as_the_merge():
    issues = {"59": {"state": "OPEN", "labels": [{"name": "state:done"},
                                                 {"name": "gate:machine"},
                                                 {"name": "no-auto"}]}}
    gh = FakeGh([{"number": 148, "headRefName": "fix/59-orphan-gap"}], issues)
    report = reconcilemod.reconcile("o/r", run=gh)
    assert report["closed"] == [] and report["skipped"] == [("59", "no-auto")]
    assert not [c for c in gh.calls if c[:2] in (["issue", "close"], ["issue", "edit"])]


def test_reconcile_no_auto_vetoes_re_advancing_a_row_too():
    """#12: a merged PR against this issue's branch number relabelled
    `state:done` over a deliberate owner correction back to `state:planned`,
    because the PR only closed part of the issue's scope. `no-auto` now stops
    reconcile from touching the row at all, not just from closing it."""
    issues = {"59": {"state": "OPEN", "labels": [{"name": "state:planned"},
                                                 {"name": "gate:taste"},
                                                 {"name": "no-auto"}]}}
    gh = FakeGh([{"number": 148, "headRefName": "fix/59-orphan-gap"}], issues)
    report = reconcilemod.reconcile("o/r", run=gh)
    assert report["advanced"] == [] and report["skipped"] == [("59", "no-auto")]
    assert not [c for c in gh.calls if c[:2] == ["issue", "edit"]]
    assert {l["name"] for l in gh.issues["59"]["labels"]} == {"state:planned", "gate:taste", "no-auto"}


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


def test_a_failed_row_leaves_a_reason_behind_and_fails_the_run(tmp_path, monkeypatch):
    """CLAUDE.md: a swallowed per-item exception writes a status plus a reason
    onto the row, and the run still fails once after the loop."""
    gh = FakeGh([{"number": 148, "headRefName": "fix/59-orphan-gap"}], _building(),
                fail_on=["issue", "view"])
    report = reconcilemod.reconcile("o/r", run=gh)
    assert [i for i, _ in report["failed"]] == ["59"]
    comments = [c for c in gh.calls if c[:2] == ["issue", "comment"]]
    assert comments and "could not advance" in comments[0][-1]

    def fake(repo, limit=50, run=None):
        return {"advanced": [], "closed": [], "skipped": [],
                "failed": [("59", "gh boom")]}

    # `main()` runs several sweeps alongside the backstop, and this test's
    # subject is only the backstop's exit code. Naming each of the others and
    # stubbing it by hand is what broke here: #110 added `unblock_stale()` to
    # `main()`, the list still named three, and the unstubbed sweep reached the
    # real `gh` against this fixture's `o/r` — a token-less runner, not a logic
    # error, the same shape #87 first failed in CI with.
    #
    # So stub the *network*, not the sweeps. Every sweep binds `run=gh` at
    # import, so rebinding `reconcilemod.gh` would not reach them — but `gh()`
    # itself looks `subprocess.run` up when it is called. One stub there, and a
    # sweep added to `main()` tomorrow reads an empty tracker and does nothing
    # rather than dialling out.
    monkeypatch.setattr(reconcilemod.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "[]", ""))
    monkeypatch.setattr(reconcilemod, "reconcile", fake)
    assert reconcilemod.main([], tmp_path, {"repo": "o/r"}) == 1


def test_a_failing_origin_sweep_does_not_take_the_backstop_down():
    """`derive_origin()` runs ahead of `reconcile()` in `main()`. Raising there
    would stop merged rows reaching `state:done` — the one job this module
    exists to do — on a transient `gh` error in an unrelated sweep. So it
    reports per item and the run fails once, after both (CLAUDE.md)."""
    def boom(args):
        raise RuntimeError("gh boom")

    report = reconcilemod.derive_origin("o/r", run=boom)
    assert report["derived"] == [] and report["failed"], report
    # A row that fails mid-sweep does not abort the rows after it.
    calls = {"n": 0}

    def one_bad(args):
        if args[:2] == ["issue", "list"]:
            return json.dumps([{"number": 1}, {"number": 2}])
        if args[0] == "api":
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("gh boom")
            return json.dumps({"labels": [{"name": "origin:owner"}]})
        return ""

    report = reconcilemod.derive_origin("o/r", run=one_bad)
    assert [i for i, _ in report["failed"]] == ["1"]
    assert [i for i, _ in report["derived"]] == ["2"]


def test_reconcile_reads_the_issue_from_the_branch_not_from_closes():
    """#116 proved `Closes #n` is a preference, not a control (GL-53)."""
    assert reconcilemod.issue_number("fix/59-orphan-gap") == "59"
    assert reconcilemod.issue_number("no-issue/sweep") is None
    assert reconcilemod.issue_number("") is None


# --------------------------------------------------------------------------
# advance_behind — #102. Native auto-merge only advances a stale branch when
# `allow_update_branch` is on, which this repo's protection does not set (an
# owner setting, not ours to flip). A queued `gate:machine` PR that loses a
# merge race sits `BEHIND` forever, reading exactly like a broken picker.
# --------------------------------------------------------------------------

class FakePrGh:
    """A gh double for the BEHIND sweep: PRs carry mergeStateStatus and
    autoMergeRequest; issues carry labels only."""

    def __init__(self, prs, issues):
        self.prs, self.issues = prs, issues
        self.calls = []

    def __call__(self, args):
        self.calls.append(args)
        if args[0] == "pr" and args[1] == "list":
            return json.dumps(self.prs)
        if args[0] == "pr" and args[1] == "update-branch":
            return ""
        if args[0] == "issue" and args[1] == "view":
            return json.dumps(self.issues[args[2]])
        return ""


def _behind_pr(num=101, status="BEHIND", auto_merge=True):
    return {"number": num, "headRefName": f"fix/{num - 1}-slug",
            "mergeStateStatus": status,
            "autoMergeRequest": {"enabledAt": "now"} if auto_merge else None}


def test_reconcile_updates_a_behind_gate_machine_pr():
    pr = _behind_pr()
    issues = {"100": {"labels": [{"name": "gate:machine"}]}}
    gh = FakePrGh([pr], issues)
    report = reconcilemod.advance_behind("o/r", run=gh)
    assert report["advanced"] == [("100", "101")]
    updates = [c for c in gh.calls if c[:2] == ["pr", "update-branch"]]
    assert len(updates) == 1 and updates[0][2] == "101"


def test_advance_behind_skips_dirty_no_auto_merge_and_no_auto():
    issues = {"100": {"labels": [{"name": "gate:machine"}]}}
    dirty = FakePrGh([_behind_pr(status="DIRTY")], issues)
    assert reconcilemod.advance_behind("o/r", run=dirty)["advanced"] == []
    assert not [c for c in dirty.calls if c[0] == "pr" and c[1] == "update-branch"]

    no_auto_merge = FakePrGh([_behind_pr(auto_merge=False)], issues)
    assert reconcilemod.advance_behind("o/r", run=no_auto_merge)["advanced"] == []
    assert not [c for c in no_auto_merge.calls if c[0] == "pr" and c[1] == "update-branch"]

    labelled = {"100": {"labels": [{"name": "gate:machine"}, {"name": "no-auto"}]}}
    no_auto = FakePrGh([_behind_pr()], labelled)
    assert reconcilemod.advance_behind("o/r", run=no_auto)["advanced"] == []
    assert not [c for c in no_auto.calls if c[0] == "pr" and c[1] == "update-branch"]


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
                                 {"name": "gate:machine"},
                                 {"name": "origin:owner"}]},
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


def test_ready_auto_is_reported_only_where_it_is_stranded():
    """Finding 1 said `ready:auto` outside `state:planned` is inert, and that
    over-reached: `state:building` is the flag doing its job. pickup writes
    `state:building` at launch and only `automerge` clears it, *after* the
    merge — so reporting it made `gate` red, held the PR, and the label was
    never cleared. Every picked-up row bricked itself (#60). The flag is
    stranded only where nothing downstream advances it."""
    cfg = qconfig.load(REPO)

    def problems(state):
        return install.issue_invariants(
            [{"number": 136, "labels": [{"name": "type:code"},
                                        {"name": state},
                                        {"name": "gate:machine"},
                                        {"name": "ready:auto"}]}], cfg)

    for stranded in ("state:triage", "state:blocked"):
        assert any("#136" in p and "ready:auto" in p
                   for p in problems(stranded)), stranded
    for in_flight in ("state:planned", "state:building", "state:gate",
                      "state:review", "state:done"):
        assert not any("ready:auto" in p for p in problems(in_flight)), in_flight


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
    # an ordinary issue carrying one label is still missing every other
    # namespace `validate.require_on_open` names
    assert len(install.issue_invariants(
        [{"number": 2, "labels": [{"name": "type:code"}]}], cfg)) ==         len(cfg["validate"]["require_on_open"]) - 1


def test_ready_auto_must_name_a_test(capsys):
    """Triage R8. The full suite runs longer than one Bash call may, and a
    `claude -p` process exits with its turn, so a sortie whose evidence of
    doneness IS the full suite cannot finish (attempt 2, #57/#71). The rule
    existed only as launch-prompt prose, which by GL-53 is a preference."""
    cfg = qconfig.load(REPO)
    labels = [{"name": "type:code"}, {"name": "state:planned"},
              {"name": "gate:machine"}, {"name": "ready:auto"},
              {"name": "origin:owner"}]
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


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   text=True)


def _r8_repo(tmp_path, pkg_base, pkg_head, test_head):
    """A real temporary git repo: a base commit carrying `pkg_base`, and a
    head commit on `feat/27-fixture` carrying `pkg_head` plus
    `tests/test_thing.py`. `origin/master` is a plain branch ref rather than
    an actual remote — `git merge-base` resolves it the same way."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "trunk")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "pkg.py").write_text(pkg_base, encoding="utf-8")
    _git(root, "add", "pkg.py")
    _git(root, "commit", "-q", "-m", "base")
    _git(root, "branch", "origin/master")
    _git(root, "checkout", "-q", "-b", "feat/27-fixture")
    (root / "pkg.py").write_text(pkg_head, encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_thing.py").write_text(test_head, encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "head")
    return root


_R8_ISSUES = [{"number": 27, "labels": [{"name": "ready:auto"}],
              "body": "Acceptance: tests/test_thing.py::test_thing proves it."}]


def test_r8_rejects_a_test_that_passes_without_the_change(tmp_path):
    """R8's proof half (#27, ADR-0023): a named test that would pass even
    against the unfixed code proves nothing, and today's label-time regex
    (`_NAMES_A_TEST`) cannot tell — it only checks the name appears. This
    drives `r8_proof` against a real git repo, not a mocked subprocess: the
    discrimination is executed, not pattern-matched."""
    root = _r8_repo(tmp_path, pkg_base="def thing():\n    return 1\n",
                    pkg_head="def thing():\n    return 2\n",
                    test_head="def test_thing():\n    assert True\n")
    problems = install.r8_proof(root, _R8_ISSUES, base_ref="master",
                                head_ref="feat/27-fixture")
    assert any("proves nothing" in p for p in problems), problems


def test_r8_accepts_a_test_that_fails_without_the_change(tmp_path):
    """The other half of the same fixture: a test that genuinely exercises
    the change (fails at the merge base, passes at HEAD) is not a problem."""
    root = _r8_repo(tmp_path, pkg_base="def thing():\n    return 1\n",
                    pkg_head="def thing():\n    return 2\n",
                    test_head="import pkg\n\n\ndef test_thing():\n"
                              "    assert pkg.thing() == 2\n")
    problems = install.r8_proof(root, _R8_ISSUES, base_ref="master",
                                head_ref="feat/27-fixture")
    assert problems == []


def test_r8_is_silent_without_a_pr_context(monkeypatch, capsys):
    """No `GITHUB_BASE_REF`/`GITHUB_HEAD_REF` means no PR to prove anything
    about — a laptop `doctor` run must not try, let alone fail (#27)."""
    monkeypatch.delenv("GITHUB_BASE_REF", raising=False)
    monkeypatch.delenv("GITHUB_HEAD_REF", raising=False)
    assert install.r8_proof(REPO, []) == []
    assert "R8" not in capsys.readouterr().out


def _two_bad_rows():
    """Two rows carrying the same problem, no body — so `issue_invariants`
    reports both and neither drags `r8_proof` into running a real pytest."""
    bad = [{"name": "type:code"}, {"name": "state:triage"},
           {"name": "gate:machine"}, {"name": "ready:auto"}]
    return [{"number": 100, "labels": bad}, {"number": 200, "labels": bad}]


def test_doctor_judges_only_the_prs_own_row(monkeypatch, capsys):
    """`gate` is a required check and the invariants swept all 21 open rows, so
    one bad row anywhere failed every open PR at once — PR #58, a gitattributes
    chore, sat red on #33's labels, and an unattended sortie cannot make the
    tracker edit that would unblock it (#63). `r8_proof` already reads the row
    the branch names; the label invariants now make the same move."""
    cfg = qconfig.load(REPO)
    monkeypatch.setattr(install, "open_issues", lambda _cfg: _two_bad_rows())

    monkeypatch.setenv("GITHUB_BASE_REF", "master")
    monkeypatch.setenv("GITHUB_HEAD_REF", "fix/100-a-branch-naming-its-row")
    problems = install.doctor(REPO, cfg)
    assert any("#100" in p for p in problems)
    assert not any("#200" in p for p in problems)
    assert "row #100" in capsys.readouterr().out

    monkeypatch.delenv("GITHUB_BASE_REF")
    monkeypatch.delenv("GITHUB_HEAD_REF")
    problems = install.doctor(REPO, cfg)
    assert any("#100" in p for p in problems)
    assert any("#200" in p for p in problems)


def test_the_daily_job_still_sweeps_the_whole_tracker():
    """Scoping `gate` to one row only moves the sweep off the merge path — it
    must still land somewhere a bad row is visible and blocks nothing (#63)."""
    rendered = install.render_one("digest.yml", qconfig.load(REPO))
    assert "python -m qops doctor" in rendered


def test_r8_only_runs_targets_pytest_can_resolve():
    """`_NAMES_A_TEST` is deliberately loose and matches a bare `test_x` in
    running prose. pytest reads a bare token as a *path*, exits 4 with `file or
    directory not found` before running anything, and R8 read that as the
    change failing its own proof — #27's PR failed on a sentence in its own
    plan. Prose references are dropped; a node id is kept."""
    body = ("It replaces `test_ready_auto_must_name_a_test` with a real proof.\n"
            "Test: `tests/test_qops.py::test_r8_accepts_a_test_that_fails_"
            "without_the_change`, and `tests/test_qops.py` overall.\n")
    targets = install._test_targets(body)
    assert "test_ready_auto_must_name_a_test" not in targets
    assert all("/" in t or "\\" in t for t in targets), targets
    assert "tests/test_qops.py::test_r8_accepts_a_test_that_fails_without_the_change" \
        in targets


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
    # Every namespace the config declares, not a list repeated here — repeating
    # it means the parser and its test drop the same one together, silently.
    # `origin:` (#26) is what proved that: adding it to the config alone left a
    # fresh repo unable to file at all, since `gh issue create --label` fails on
    # a label the repo does not have.
    expected = set(taxonomy.get("flags", []))
    for ns, values in taxonomy.items():
        if ns != "flags":
            expected |= {f"{ns}:{v}" for v in values}
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


def test_the_digest_job_is_gated_and_the_reconciler_is_not():
    """Extraction criterion 7: owner CI attention must not double. With two
    repos on one cadence it did.

    A project turns its digest off by config; the reconciler stays on the cron
    regardless, because it is the load-bearing half — `advance` cannot fire on
    a merge its own GITHUB_TOKEN caused, so the scheduled reconcile is the only
    thing that repairs the row on the path that matters.

    `success() &&` is asserted because adding an `if:` drops the implicit
    `success()` that `needs:` would otherwise imply, and a digest rendered from
    rows a failed reconcile did not repair is worse than no digest at all.
    """
    cfg = qconfig.load(REPO)
    text = install.render_one("digest.yml", cfg)
    digest = text.split("\n  digest:", 1)[1]
    assert "if: success() && (" in digest
    assert "github.event_name == 'workflow_dispatch'" in digest

    on_block = text.split("on:", 1)[1].split("jobs:", 1)[0]
    assert "schedule:" in on_block, "the reconciler must keep its cron"

    posting = install.render_one(
        "digest.yml", {**cfg, "ci": {**cfg["ci"], "digest_posts_on_schedule": True}})
    assert "(true ||" in posting
    quiet = install.render_one(
        "digest.yml", {**cfg, "ci": {**cfg["ci"], "digest_posts_on_schedule": False}})
    assert "(false ||" in quiet
    # A config that says nothing wants its digest — the default must not be off.
    silent = install.render_one(
        "digest.yml", {**cfg, "ci": {k: v for k, v in cfg["ci"].items()
                                     if k != "digest_posts_on_schedule"}})
    assert "(true ||" in silent


# --------------------------------------------------------------------------
# versioning — v0.1.1 was tagged against a tree that still declared
# version = "0.1.0" (a `pip show qops` in a consumer never learns which
# substrate it has). The tag is the act ADR-0023 rests on; this is the check
# that a tag cut before the version is bumped would have failed.
# --------------------------------------------------------------------------

def _declared_version() -> str:
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert m, "pyproject.toml has no [project] version"
    return m.group(1)


def test_the_tag_agrees_with_the_declared_version():
    """Asked at the tag, which is the only place it is a real question (#40).

    The first shape asked it on every commit — "the declared version is not
    already tagged" — which is red on every post-release tree until someone
    bumps, on every branch, blocking PRs `gate` was never aimed at. And it was
    vacuous in CI, where `actions/checkout` fetches no tags, so it fired
    exactly where it was wrong and never where it was right.

    Cutting a tag is the act ADR-0023 rests on, and it is when the two must
    agree. Off a tag there is nothing to check, so this is silent.
    """
    exact = subprocess.run(["git", "describe", "--tags", "--exact-match", "HEAD"],
                           cwd=REPO, capture_output=True, text=True)
    tag = exact.stdout.strip()
    if not tag.startswith("v"):
        return                      # not a tagged commit: no question to ask
    assert tag == f"v{_declared_version()}", (
        f"HEAD is tagged {tag} against a tree declaring "
        f"{_declared_version()}; bump pyproject.toml before cutting the tag "
        f"(this is what v0.1.1 skipped)")


def test_the_rendered_test_workflow_can_see_the_tag_it_judges():
    """The half of #40 that made the check vacuous. A workflow that never runs
    on a tag, or runs without fetching tags, cannot ask the question above —
    and a green step that produced no observable change did not happen."""
    rendered = install.render_one("test.yml", qconfig.load(REPO))
    assert "tags:" in rendered, "test.yml never runs on a tag push"
    assert "fetch-tags: true" in rendered, "checkout fetches no tags"


def test_brief_reports_the_same_version_pyproject_declares():
    """#103: an editable install's egg-info goes stale the moment
    pyproject.toml is hand-edited without a reinstall — this repo showed
    `importlib.metadata` reporting 0.1.0 while pyproject.toml already said
    0.2.0. `qops_version(root)` must read the tree it is asked about, not
    whatever was true at the last `pip install -e .`."""
    assert briefmod.qops_version(REPO) == _declared_version()


# --------------------------------------------------------------------------
# ADR-0026 — the gate says one thing, and the other two concerns have their own
# carriers. R3's default inverted on the evidence in docs/2026-08-20-gate-audit.md
# (2 of 14 resolved gate:taste rows had an owner action change the outcome), and
# the inversion is only safe while gate:machine confers no autonomy by itself.
# --------------------------------------------------------------------------

def test_gate_machine_alone_confers_no_autonomy():
    """The load-bearing half of ADR-0026. R3's old default was defended by "a
    wrong `machine` label produces an autonomous sortie" — false, because the
    picker needs three more things the owner alone grants. Relax `eligible()`
    and the ADR's safety argument fails here, in the same commit."""
    issue = lambda *labels: {"labels": [{"name": n} for n in labels]}
    assert not qops_pickup.eligible(issue("gate:machine"))
    assert not qops_pickup.eligible(issue("gate:machine", "state:planned"))
    assert not qops_pickup.eligible(issue("gate:machine", "ready:auto"))
    assert qops_pickup.eligible(issue("gate:machine", "state:planned", "ready:auto"))


def test_no_auto_is_the_authority_veto_in_all_three_mechanisms():
    """Concern 2 of ADR-0026's split: authority is `no-auto`, not `gate:`. It
    only holds while every mechanism a gate:machine row can reach honours the
    flag — the pickup, the merge, and reconcile's close *and* relabel."""
    from qops import reconcile
    issue = {"labels": [{"name": n} for n in
                        ("gate:machine", "state:planned", "ready:auto", "no-auto")]}
    assert not qops_pickup.eligible(issue)
    assert not reconcile._closeable({"gate:machine", "no-auto"})
    body = (REPO / "qops" / "templates" / "automerge.yml.tmpl").read_text(encoding="utf-8")
    assert "no-auto" in body
    assert "no-auto" in (REPO / "qops" / "reconcile.py").read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# #69 — ADR-0023's second route into the queue. On an origin:owner row the
# filing is the grant; it takes effect once the body names a test, with no
# second label edit. Four cases: a bare origin:owner row with a named test, a
# row naming no test, a no-auto row (authority still vetoes), and an
# origin:agent row (the confidence-proposer path is #28's, not this one's).
# --------------------------------------------------------------------------

def _owner_issue(*labels, body=""):
    return {"labels": [{"name": n} for n in labels], "body": body}


def test_an_owner_filed_planned_row_needs_no_second_label_edit():
    named_test = "## Files\n\nExpected to touch: `tests/test_qops.py::test_x`\n"
    no_test = "just a body with no test named"

    assert qops_pickup.eligible(
        _owner_issue("gate:machine", "state:planned", "origin:owner", body=named_test))
    assert not qops_pickup.eligible(
        _owner_issue("gate:machine", "state:planned", "origin:owner", body=no_test))
    assert not qops_pickup.eligible(
        _owner_issue("gate:machine", "state:planned", "origin:owner", "no-auto",
                     body=named_test))
    assert not qops_pickup.eligible(
        _owner_issue("gate:machine", "state:planned", "origin:agent", body=named_test))


def test_gate_taste_is_never_eligible_by_the_owner_filed_route():
    body = "## Files\n\nExpected to touch: `tests/test_qops.py::test_x`\n"
    assert not qops_pickup.eligible(
        _owner_issue("gate:taste", "state:planned", "origin:owner", body=body))


def test_the_triage_rules_send_an_unsure_row_to_the_machine():
    """R3 is prose, and prose drifts back. The old default ("when unsure,
    `gate:taste`") is the exact string that produced the parking lot, and 14 of
    22 open printshop taste rows carry it in effect — their own body says the
    gate was never defined."""
    rules = (REPO / "docs" / "agents" / "triage-labels.md").read_text(encoding="utf-8")
    assert "When unsure, `gate:machine`" in rules
    assert "When unsure, `gate:taste`" not in rules
    for concern in ("Judgement", "Authority", "Verification reach"):
        assert concern in rules


# --------------------------------------------------------------------------
# ADR-0027 — one row is one sortie. The two mechanisms are split by role on
# purpose: the triager refuses and reports, the planner splits. They must not
# drift into one, because splitting writes an issue body and the triager is
# fenced out of issue bodies.
# --------------------------------------------------------------------------

def _role(name: str) -> str:
    """Role bodies are hard-wrapped prose, so a phrase spans lines. Normalise
    whitespace before asserting on one - the assertion is about the rule being
    in the role, not about where the wrap fell."""
    text = (REPO / ".claude" / "agents" / f"{name}.md").read_text(encoding="utf-8")
    return " ".join(text.lower().split())


def test_the_triager_refuses_rather_than_guesses():
    role = _role("triager")
    assert "oversized" in role
    assert "you do not edit issue bodies" in role
    assert "you do not split it" in role


def test_the_planner_splits_what_the_triager_refused():
    role = _role("planner")
    assert "oversized" in role
    assert "the deliverable is the children" in role


# --------------------------------------------------------------------------
# ADR-0028 — the filing bar. With `ready:auto` mechanical on `origin:owner`
# there is no grant-time left, so the row's body is the last thing between the
# owner's direction and an unattended commit. R8 already checks that a row
# names a test; nothing checked that it says what done looks like.
# --------------------------------------------------------------------------

_BAR_LABELS = [{"name": "type:code"}, {"name": "state:planned"},
               {"name": "gate:machine"}, {"name": "origin:owner"}]


def test_doctor_refuses_a_row_with_no_stated_outcome():
    """The pair is executed, not pattern-matched. ADR-0024's lesson: #1 was a
    branch that read correctly and never ran, and a test that greps the rule
    would have passed against it."""
    cfg = qconfig.load(REPO)
    barren = install.issue_invariants(
        [{"number": 1, "labels": _BAR_LABELS,
          "body": "the registration keeps drifting, someone should look"}], cfg)
    assert any("#1" in p and "states no outcome" in p for p in barren)

    stated = install.issue_invariants(
        [{"number": 2, "labels": _BAR_LABELS,
          "body": "It drifts.\n\n## Acceptance\n\n- `qops doctor` exits 1.\n"}], cfg)
    assert stated == []


# --------------------------------------------------------------------------
# #80 / ADR-0028 §4 — the reviewer's verdict. The required checks on master
# are all mechanical, so nothing between a filing and master reads for meaning.
# This is the reader. It is also a language model wired into a required check
# under `enforce_admins: true`, so its fail-open path is what bounds the blast
# radius of an outage: a wrong fail-closed costs one bad merge, a wrong
# fail-open costs a repo nobody can merge to and only the owner can unstick.
# --------------------------------------------------------------------------

# What `master` requires today. Written here rather than queried, because the
# assertion is about this repo's rollout decision and a test that asks GitHub
# would go green the moment someone flips the switch by hand.
_REQUIRED_CONTEXTS = ("test", "gate", "tripwires", "doc-links")

_DIFF = "diff --git a/x.py b/x.py\n+def x(): return 1\n"
_ROW = "## Acceptance\n- `x()` returns 1.\n"
_SHA = "1111111111111111111111111111111111111111"
_OLD = "2222222222222222222222222222222222222222"


def _verdict_comment(sha, text):
    return f"{reviewmod.marker(sha)}\n\n{text}"


def _pr_context(monkeypatch, number="7", sha=_SHA):
    """A test that wants a PR context sets one, explicitly (#65). conftest
    clears these, so nothing here depends on where the suite is run."""
    monkeypatch.setenv("PR_NUMBER", number)
    monkeypatch.setenv("PR_HEAD_SHA", sha)


def test_the_reviewer_reads_a_verdict_and_nothing_else():
    """Strict parse. Anything that is not one of the two verdicts is *not* a
    verdict, and a lenient parser would turn a rambling answer into a merge
    decision."""
    assert reviewmod.verdict("VERDICT: serves\nbecause x") == "serves"
    assert reviewmod.verdict("VERDICT: does-not-serve\nno test") == "does-not-serve"
    for junk in ("", "I think it probably serves the goal", "VERDICT: maybe",
                 "serves", "VERDICT:", None):
        assert reviewmod.verdict(junk) is None, junk


def test_a_verdict_is_keyed_on_the_commit_it_read():
    """The load-bearing half of the split. A verdict on an older commit would
    authorise whatever was pushed after it — a reviewer approving code it never
    read — so a verdict for another SHA is no verdict at all."""
    old = _verdict_comment(_OLD, "VERDICT: serves\nfine")
    assert reviewmod.latest_verdict([old], _SHA) is None
    assert reviewmod.latest_verdict([old], _OLD) == "serves"
    # Newest wins: the host re-reviews after a push, and the later comment is
    # the one that read the current diff.
    both = [_verdict_comment(_SHA, "VERDICT: does-not-serve\nno test"),
            _verdict_comment(_SHA, "VERDICT: serves\ntest added")]
    assert reviewmod.latest_verdict(both, _SHA) == "serves"
    # A comment that merely talks about the reviewer is not a verdict.
    assert reviewmod.latest_verdict(["VERDICT: serves, I reckon"], _SHA) is None


@pytest.mark.parametrize("why,number,sha,cfg,bodies", [
    ("no PR context", None, None, {"repo": "o/r"}, []),
    ("config names no `repo`", "7", _SHA, {}, []),
    ("could not read the PR's comments", "7", _SHA, {"repo": "o/r"}, RuntimeError),
    ("no verdict posted", "7", _SHA, {"repo": "o/r"},
     [_verdict_comment(_OLD, "VERDICT: does-not-serve\nstale")]),
    ("gave up", "7", _SHA, {"repo": "o/r"},
     [_verdict_comment(_SHA, "I think so?")]),
])
def test_the_reviewer_fails_open_without_a_verdict(monkeypatch, capsys, tmp_path,
                                                   why, number, sha, cfg, bodies):
    """Every path that is not a verdict is green AND says why. A silent
    fail-open is indistinguishable from a real pass, which is a required check
    that reads nothing while looking like it read.

    The row this rebuild adds is the fourth: the verdict is produced on the
    cron host, so a sleeping host means no comment — and that has to be a
    fail-open, not a PR that waits forever under `enforce_admins: true`."""
    if number:
        _pr_context(monkeypatch, number, sha)
    if bodies is RuntimeError:
        monkeypatch.setattr(reviewmod, "comments", lambda *a, **k: (
            _ for _ in ()).throw(RuntimeError("gh pr view failed")))
    else:
        monkeypatch.setattr(reviewmod, "comments", lambda *a, **k: bodies)
    assert reviewmod.main([], tmp_path, cfg) == 0
    out = capsys.readouterr().out
    assert "fail-open" in out and why in out, out


def test_the_reviewer_fails_closed_on_a_verdict(monkeypatch, capsys, tmp_path):
    """Only a verdict is a rejection, and it says what it judged."""
    _pr_context(monkeypatch)
    monkeypatch.setattr(reviewmod, "comments", lambda *a, **k: [
        _verdict_comment(_SHA, "VERDICT: does-not-serve\nno test added")])
    assert reviewmod.main([], tmp_path, {"repo": "o/r"}) == 1
    assert "does NOT serve" in capsys.readouterr().out

    monkeypatch.setattr(reviewmod, "comments", lambda *a, **k: [
        _verdict_comment(_SHA, "VERDICT: serves\nok")])
    assert reviewmod.main([], tmp_path, {"repo": "o/r"}) == 0


def _host_pass(monkeypatch, prs, answer, seen=()):
    """The host side wired to fakes: `produce` is the only thing that talks to
    a model, and it never runs in CI."""
    calls = []
    monkeypatch.setattr(reviewmod, "open_prs", lambda *a, **k: prs)
    monkeypatch.setattr(reviewmod, "comments", lambda *a, **k: list(seen))
    monkeypatch.setattr(reviewmod, "pr_diff", lambda *a, **k: _DIFF)
    monkeypatch.setattr(reviewmod, "row_body", lambda *a, **k: _ROW)
    monkeypatch.setattr(reviewmod, "ask", lambda *a, **k: answer)
    monkeypatch.setattr(reviewmod, "_gh",
                        lambda root, *args, **k: calls.append(args) or "")
    return calls


def test_the_host_posts_the_verdict_as_a_comment_carrying_the_head_sha(
        monkeypatch, tmp_path):
    """`gh pr comment`, a plain verb — never a commit status. `gh api -X`
    against repo settings is denied by a taken decision (ADR-0016/0020) and a
    reviewer that POSTed a status would be routing around it."""
    prs = [{"number": 7, "headRefName": "fix/1-x", "headRefOid": _SHA,
            "isDraft": False}]
    calls = _host_pass(monkeypatch, prs, "VERDICT: serves\nit does")
    assert reviewmod.produce(tmp_path, {"repo": "o/r"}) == 0
    assert len(calls) == 1, calls
    assert calls[0][:2] == ("pr", "comment")
    assert "-X" not in calls[0] and "api" not in calls[0]
    body = calls[0][calls[0].index("--body") + 1]
    assert reviewmod.marker(_SHA) in body and "VERDICT: serves" in body


def test_the_host_does_not_review_the_same_commit_twice(monkeypatch, capsys,
                                                        tmp_path):
    """A pass runs on a schedule, so re-posting on every fire would be a
    comment an hour on a PR nobody pushed to."""
    prs = [{"number": 7, "headRefName": "fix/1-x", "headRefOid": _SHA,
            "isDraft": False}]
    calls = _host_pass(monkeypatch, prs, "VERDICT: serves\nit does",
                       seen=[_verdict_comment(_SHA, "VERDICT: serves\nit does")])
    assert reviewmod.produce(tmp_path, {"repo": "o/r"}) == 0
    assert calls == []
    # And it still says what it saw: a pass that prints nothing reads the same
    # whether there was nothing to judge or nothing was reachable.
    assert "1 ready PR(s) on o/r" in capsys.readouterr().out


def test_the_host_reports_a_pr_it_could_not_judge_and_still_fails_the_pass(
        monkeypatch, capsys, tmp_path):
    """A swallowed per-item exception leaves a state change behind and the run
    still fails once, after the loop. An unparseable answer is not posted as a
    verdict: a rambling answer must not become a merge decision."""
    prs = [{"number": 7, "headRefName": "fix/1-x", "headRefOid": _SHA,
            "isDraft": False},
           {"number": 8, "headRefName": "sweep", "headRefOid": _OLD,
            "isDraft": False}]
    calls = _host_pass(monkeypatch, prs, "no verdict here")
    assert reviewmod.produce(tmp_path, {"repo": "o/r"}) == 1
    assert calls == []
    out = capsys.readouterr().out
    assert "carried no verdict" in out and "names no row" in out
    # The state change: one ledger row per failed attempt, per commit.
    events = [e for e in ledger.read(tmp_path)
              if e["event"] == "review_unjudged"]
    assert [(e["pr"], e["n"]) for e in events] == [("7", 1), ("8", 1)], events


def test_a_pr_that_cannot_be_judged_is_not_retried_forever(monkeypatch, capsys,
                                                           tmp_path):
    """The loop this bounds: the pass is hourly and a PR can sit open for days
    waiting on the owner, so a review that keeps failing would be one model
    call an hour, forever, on a failure that will not fix itself. Three tries,
    then the host says so on the PR and goes quiet."""
    pr = {"number": 7, "headRefName": "fix/1-x", "headRefOid": _SHA,
          "isDraft": False}
    asked, posted = [], []
    for _ in range(reviewmod.MAX_ATTEMPTS + 2):
        calls = _host_pass(monkeypatch, [pr], "no verdict here",
                           seen=list(posted))
        monkeypatch.setattr(reviewmod, "ask",
                            lambda *a, **k: asked.append(1) or "no verdict here")
        reviewmod.produce(tmp_path, {"repo": "o/r"})
        for args in calls:                     # what the host said on the PR
            posted.append(args[args.index("--body") + 1])

    assert len(asked) == reviewmod.MAX_ATTEMPTS, asked   # then it stopped
    assert len(posted) == 1 and "No verdict" in posted[0]
    assert reviewmod.marker(_SHA) in posted[0]
    assert "already judged" in capsys.readouterr().out

    # CI reads that comment as a fail-open, and says which kind it is: the host
    # gave up here, rather than being asleep.
    _pr_context(monkeypatch)
    monkeypatch.setattr(reviewmod, "comments", lambda *a, **k: posted)
    assert reviewmod.main([], tmp_path, {"repo": "o/r"}) == 0
    assert "gave up" in capsys.readouterr().out


def test_giving_up_holds_even_if_the_host_could_not_say_so(monkeypatch, capsys,
                                                           tmp_path):
    """The ledger stops the asking, not the comment. A pass that trusted the
    comment alone would run the model every hour forever on a PR where posting
    is what is broken."""
    pr = {"number": 7, "headRefName": "fix/1-x", "headRefOid": _SHA,
          "isDraft": False}
    asked = []
    for _ in range(reviewmod.MAX_ATTEMPTS + 2):
        _host_pass(monkeypatch, [pr], "no verdict here")
        monkeypatch.setattr(reviewmod, "ask",
                            lambda *a, **k: asked.append(1) or "no verdict here")
        monkeypatch.setattr(reviewmod, "_gh", lambda *a, **k: (
            _ for _ in ()).throw(RuntimeError("gh pr comment failed")))
        reviewmod.produce(tmp_path, {"repo": "o/r"})
    assert len(asked) == reviewmod.MAX_ATTEMPTS, asked
    assert "given up on" in capsys.readouterr().out


def test_a_new_commit_gets_a_fresh_count(monkeypatch, tmp_path):
    """Giving up is per commit, never per PR: a push is a new SHA, and the
    reviewer has to read what was pushed."""
    for _ in range(reviewmod.MAX_ATTEMPTS):
        _host_pass(monkeypatch, [{"number": 7, "headRefName": "fix/1-x",
                                  "headRefOid": _SHA, "isDraft": False}],
                   "no verdict here")
        reviewmod.produce(tmp_path, {"repo": "o/r"})
    assert reviewmod.attempts(tmp_path, "7", _SHA) == reviewmod.MAX_ATTEMPTS
    assert reviewmod.attempts(tmp_path, "7", _OLD) == 0


def test_the_reviewer_workflow_calls_no_model_and_needs_no_secret():
    """The whole point of the rebuild: CI cannot reach the subscription, so a
    model call here would be a metered key and a second cost line that grows
    with the loop. CI reads a comment."""
    text = install.render_one("reviewer.yml", qconfig.load(REPO))
    assert "python -m qops review" in text
    assert "ANTHROPIC" not in text and "secrets." in text  # only GITHUB_TOKEN
    assert "secrets.GITHUB_TOKEN" in text
    assert "github.event.pull_request.head.sha" in text  # never `github.sha`
    assert "ANTHROPIC" not in (REPO / "qops" / "review.py").read_text(
        encoding="utf-8")
    assert "reviewer.yml" in install.WORKFLOWS


def test_the_reviewer_is_not_a_required_context_yet():
    """It ships reporting-only: built, watched for a week, then added to the
    required contexts with evidence rather than hope (#80's rollout, which is
    why the row is `no-auto`)."""
    assert "reviewer" not in _REQUIRED_CONTEXTS


def test_the_verdict_pass_rides_the_registered_run(monkeypatch, tmp_path):
    """A scheduled task registration is a hand-made machine fact the repo
    cannot see (#12), so the verdict pass adds none: the run that is already
    registered produces it, and the registered command line is unchanged."""
    ran = []
    monkeypatch.setattr(qops_pickup, "repo_root", lambda argv: tmp_path)
    monkeypatch.setattr(qops_pickup, "_run", lambda argv, root: ran.append("pick") or 0)
    monkeypatch.setattr(qops_pickup, "_review", lambda root: ran.append("review") or 0)
    monkeypatch.setattr(qops_pickup.ledger, "append", lambda *a, **k: None)

    assert qops_pickup.main(["--root", str(tmp_path), "--launch"]) == 0
    assert ran == ["pick", "review"], ran   # after, so a new PR is judged now

    ran.clear()
    assert qops_pickup.main(["--review"]) == 0
    assert ran == ["review"], ran           # alone: it picks nothing

    ran.clear()
    assert qops_pickup.main(["--root", str(tmp_path)]) == 0
    assert ran == ["pick"], ran             # a dry run writes nothing anywhere


def test_the_filing_bar_does_not_judge_a_finished_row():
    """The bar exists so *downstream* can tell what done looks like, and
    nothing is downstream of done. A decision row goes `triage -> done` with no
    build in between, so it never passes a planner that would have written an
    acceptance section — and a `gate:taste` row cannot state a machine
    criterion by its own nature (ADR-0028 §2). #46 resolved that way and turned
    the gate red on the PR that recorded the decision (#89)."""
    cfg = qconfig.load(REPO)

    def problems(state):
        return install.issue_invariants(
            [{"number": 46, "labels": [{"name": "type:decision"},
                                       {"name": state},
                                       {"name": "gate:taste"},
                                       {"name": "origin:owner"}],
              "body": "Four shapes, none taken. Recommendation: shape 1."}], cfg)

    for terminal in ("state:done", "state:cancelled"):
        assert not any("states no outcome" in p
                       for p in problems(terminal)), terminal
    # And the states a row can still be worked in are untouched. Without this
    # the fix quietly guts ADR-0028 §1 rather than narrowing it.
    for workable in ("state:planned", "state:building"):
        assert any("states no outcome" in p
                   for p in problems(workable)), workable


def test_the_filing_bar_does_not_fire_in_triage():
    """A row the owner filed in one line must be allowed to exist. The bar is a
    gate on *leaving* triage, not on filing - ADR-0028 puts the last control on
    the filing, and a control that refuses the filing itself has moved the toil
    rather than removed it (CLAUDE.md:81)."""
    cfg = qconfig.load(REPO)
    triage = [{"name": "type:code"}, {"name": "state:triage"},
              {"name": "gate:machine"}, {"name": "origin:owner"}]
    assert install.issue_invariants(
        [{"number": 3, "labels": triage, "body": "the button is dead"}], cfg) == []


def test_the_filing_bar_reads_an_acceptance_line_not_only_a_heading():
    """Real rows write it three ways. The bar is the machine half - is there a
    stated outcome at all - and judging whether the outcome is a *good* one is
    the reviewer gate's, the same split R8 already makes."""
    cfg = qconfig.load(REPO)
    for body in ("## Acceptance\n- it exits 1\n",
                 "**Acceptance:** `qops doctor` exits 1\n",
                 "Acceptance: tests/test_qops.py passes."):
        assert install.issue_invariants(
            [{"number": 4, "labels": _BAR_LABELS, "body": body}], cfg) == [], body
    # the heading alone, with nothing under it, is not a stated outcome
    assert any("states no outcome" in p for p in install.issue_invariants(
        [{"number": 5, "labels": _BAR_LABELS, "body": "## Acceptance\n\n"}], cfg))


def test_the_filing_bar_cannot_answer_without_a_body():
    """Same convention R8's check already uses: a caller that passed no body has
    not granted a pass, the rule simply does not fire."""
    cfg = qconfig.load(REPO)
    assert install.issue_invariants([{"number": 6, "labels": _BAR_LABELS}], cfg) == []


def test_a_closed_blocker_in_the_body_is_a_contradiction():
    """#82's body kept saying `Blocked by #80` after #80 shipped, three
    unattended sorties read the label as workable and the prose as blocked,
    and refused. Labels are what pickup-loop reads; prose is what an agent
    reads — when they disagree the queue looks full and moves nothing."""
    cfg = qconfig.load(REPO)
    open_a = [{"name": "type:code"}, {"name": "state:planned"},
              {"name": "gate:machine"}, {"name": "origin:owner"}]

    def fixture(a_state, body, extra_open=()):
        a = {"number": 82,
             "labels": [{"name": "type:code"}, {"name": a_state},
                        {"name": "gate:machine"}, {"name": "origin:owner"}],
             "body": body}
        return [a, *extra_open]

    body = "**Blocked by #80** (the reviewer gate) and **#46**\n\nAcceptance: it merges."
    contradiction = "body says `Blocked by #80`"

    # #80 not in the open set: the blocker closed, the prose is stale.
    problems = install.issue_invariants(fixture("state:planned", body), cfg)
    assert any(contradiction in p and "#82" in p for p in problems), problems

    # #80 present in the open set: no contradiction.
    problems = install.issue_invariants(
        fixture("state:planned", body,
                [{"number": 80, "labels": open_a}]), cfg)
    assert not any(contradiction in p for p in problems), problems

    # a state:done row is allowed to describe what once blocked it.
    problems = install.issue_invariants(fixture("state:done", body), cfg)
    assert not any(contradiction in p for p in problems), problems

    # One problem per blocker, not one per mention. #92's own body named the
    # same blocker twice and `doctor` said the same sentence twice.
    twice = body + "\n\nBlocked by #80 still, as of today.\n"
    problems = install.issue_invariants(fixture("state:planned", twice), cfg)
    assert len([p for p in problems if contradiction in p]) == 1, problems


def test_only_a_claimed_blocker_counts_not_a_quoted_one():
    """This check's own row was its first false positive: #92's body *cites*
    #82's prose mid-sentence and *quotes* a run log saying `#82 blocked by
    #80`. A body that discusses a blocker does not have one, so the claim is
    read from the start of a line, through markdown emphasis and nothing else."""
    cfg = qconfig.load(REPO)
    prose = ("A row's blocker lives in its body as prose (`**Blocked by #80**` "
             "in #82's body), and nothing makes it false again.\n"
             "> `#82 blocked by #80. #80 still OPEN`\n\n"
             "Acceptance: `doctor` says so.\n")
    row = [{"number": 92, "body": prose,
            "labels": [{"name": "type:code"}, {"name": "state:planned"},
                       {"name": "gate:machine"}, {"name": "origin:owner"}]}]
    assert not any("Blocked by" in p
                   for p in install.issue_invariants(row, cfg)), row


def test_the_blocker_check_does_not_run_on_a_scoped_doctor():
    """`_rows_in_scope` hands a PR its own row and nothing else (#63), so on
    the merge path *every* blocker a row names would read as closed. That is
    how this check failed its own PR (#94). The daily sweep sees the whole
    tracker, and a stale blocker costs unattended sessions, not a merge."""
    cfg = qconfig.load(REPO)
    row = [{"number": 82,
            "body": "**Blocked by #80**\n\nAcceptance: it merges.\n",
            "labels": [{"name": "type:code"}, {"name": "state:planned"},
                       {"name": "gate:machine"}, {"name": "origin:owner"}]}]
    assert any("Blocked by #80" in p
               for p in install.issue_invariants(row, cfg, tracker_wide=True))
    assert not any("Blocked by #80" in p
                   for p in install.issue_invariants(row, cfg,
                                                     tracker_wide=False))


# --------------------------------------------------------------------------
# #44 — the gate evaluates the invariants instead of skipping them. PR #43's
# own gate log said, one line above the word "clean":
#   doctor: skipping the label invariant - gh exited 4 [...] set GH_TOKEN
# so every rule issue_invariants() holds - including ADR-0028's filing bar,
# which that ADR calls the control replacing three others - only ever ran on
# a laptop. A check that exists where it cannot run is ADR-0024's #1 again.
# --------------------------------------------------------------------------

def test_the_gate_evaluates_the_issue_invariants_rather_than_skipping_them():
    """Read off the *rendered* workflow, not the template: the template is the
    source but the workflow is what runs."""
    rendered = install.render_one("gate.yml", qconfig.load(REPO))
    assert "GH_TOKEN" in rendered, "the doctor step cannot query the tracker"
    assert "issues: read" in rendered, "GH_TOKEN without the scope is still a skip"
    assert "QOPS_STRICT" in rendered, "a failed query would go green in CI"


def test_an_unreadable_backlog_is_a_problem_under_strict_and_a_skip_otherwise(monkeypatch):
    """`open_issues()` returning None is right for a local instrument and wrong
    for a required check: a rate-limited `gh` would turn the gate green over an
    unread backlog. Same distinction `qops_pickup.candidates()` already draws
    between an idle queue and a broken picker."""
    monkeypatch.delenv("QOPS_STRICT", raising=False)
    assert not install.strict()
    monkeypatch.setenv("QOPS_STRICT", "1")
    assert install.strict()

    cfg = qconfig.load(REPO)
    monkeypatch.setattr(install, "open_issues", lambda _cfg: None)
    problems = install.doctor(REPO, cfg)
    assert any("invariants" in p and "not evaluated" in p for p in problems)

    monkeypatch.delenv("QOPS_STRICT")
    assert not [p for p in install.doctor(REPO, cfg)
                if "invariants" in p and "not evaluated" in p]


def test_doctor_says_how_many_rows_the_invariants_read(monkeypatch, capsys):
    """The gate log proved the invariants ran only by the *absence* of a skip
    line, which is status-code reasoning. It now names the number it read, so
    an evaluated backlog and a skipped one do not print the same thing."""
    cfg = qconfig.load(REPO)
    monkeypatch.setattr(install, "open_issues", lambda _cfg: [])
    # The count is the tracker-wide one, so this asks it off a PR. It used to
    # delete the two refs by hand, which was a patch for the disease #65's
    # conftest fixture cures: no test inherits them now. Dropped deliberately —
    # a workaround kept after its cause is fixed reads as a requirement.
    install.doctor(REPO, cfg)
    out = capsys.readouterr().out
    assert "invariants evaluated against 0 open rows" in out
    assert cfg["repo"] in out


# --------------------------------------------------------------------------
# #47 — the triager writes the gate. ADR-0026 made `gate:` decidable from the
# row alone; the role was fenced out of every label because, when it was
# drafted, none was. What must not move is the boundary ADR-0023 drew: the
# grant is the owner's, and ADR-0026's authority column is his too.
# --------------------------------------------------------------------------

def test_the_triager_may_write_the_gate_and_never_the_grant():
    role = _role("triager")
    assert "you write `type:` and `gate:`" in role
    for forbidden, why in (("never `ready:auto`", "the grant is the owner's"),
                           ("never `no-auto`", "authority is the owner's"),
                           ("never `state:`", "state:planned is the planner's")):
        assert forbidden in role, why


def test_the_triager_names_all_three_refusals():
    """A refusal is a correct answer, and the three go to different places. The
    measured run refused 8 of 33 and three of those were rows the re-triage had
    labelled and should not have - refusing beat the ground truth."""
    role = _role("triager")
    for refusal in ("ambiguous", "oversized", "below the filing bar"):
        assert refusal in role
    assert "the planner splits a row you report" in role
    assert "`gate:none` is not one of your answers" in role


def test_the_triager_default_is_the_machine_not_the_owner():
    """R3 inverted (ADR-0026). The role is where a triager actually reads it,
    so the prose that produced the parking lot must not survive here either."""
    role = _role("triager")
    assert "when unsure, `gate:machine`" in role
    assert "when unsure, `gate:taste`" not in role


def test_the_loop_still_never_labels_even_though_the_role_now_may():
    """Two documents disagreeing about what an agent may do is worse than
    either rule. `triage-loop` is the LLM-free Actions job and its deliberate
    limit is unchanged; the role is what gained the authority."""
    loops = " ".join((REPO / "docs" / "reference" / "loops.md")
                     .read_text(encoding="utf-8").lower().split())
    assert "warns and does not label" in loops
    assert "the loop still never labels, the role now may" in loops


# --------------------------------------------------------------------------
# #48 — a row the launch may not write is never claimed. Four unattended runs
# on #47 (2026-08-20 13:00-16:00 UTC) each died on the same tool result: the
# harness refuses writes to files that configure Claude Code itself, and
# `--permission-mode acceptEdits` does not cover them. eligible() cannot see
# that, so the row passed every check, launched, and failed identically.
# The grant is NOT what should widen - an unattended agent that may rewrite
# its own role has no controls left.
# --------------------------------------------------------------------------

_FILES_OK = "## Files\n\nExpected to touch: `qops/install.py`, `tests/test_qops.py`\n"
_FILES_ROLE = ("## Files\n\nExpected to touch: `.claude/agents/triager.md`, "
               "`tests/test_qops.py`\n")
_FILES_PROSE = ("## Files\n\nExpected to touch: `qops/install.py`\n"
                "Must not touch: `.claude/settings.json`, `qops/templates/`\n")


def test_a_row_the_launch_may_not_write_is_never_claimed():
    """The whole point is to spend nothing. A row naming an unwritable path is
    reported and skipped *before* the claim, so it never reaches state:building
    and never burns a session."""
    assert qops_pickup.unwritable(_FILES_ROLE) == [".claude/agents/triager.md"]
    assert qops_pickup.unwritable(_FILES_OK) == []


def test_must_not_touch_is_not_expected_to_touch():
    """The fragile case, and the one that would silently empty the queue: every
    specced row in this repo names `.claude/` under *Must not touch*. Reading
    the wrong half would make every row unlaunchable."""
    assert qops_pickup.unwritable(_FILES_PROSE) == []


def test_a_row_with_no_files_section_is_launchable():
    """Filing a row with no Files section is a filing-bar question (#42), not
    this check's. Silence here must not read as a refusal."""
    assert qops_pickup.unwritable("just a body") == []


def test_the_picker_skips_to_the_next_row_rather_than_stopping(monkeypatch, tmp_path):
    """A skipped row is not an idle queue. The run must go on to pick the next
    eligible sortie, and say which row it passed over and why."""
    rows = [{"number": 47, "title": "role", "updatedAt": "2026-08-20T01:00:00Z",
             "body": _FILES_ROLE, "labels": []},
            {"number": 33, "title": "gitattributes", "updatedAt": "2026-08-20T02:00:00Z",
             "body": _FILES_OK, "labels": []}]
    monkeypatch.setattr(qops_pickup, "candidates", lambda _root: rows)
    monkeypatch.setattr(qops_pickup, "report_unlaunchable", lambda *a, **k: None)
    picked = qops_pickup.first_launchable(tmp_path, rows)
    assert picked["number"] == 33


def test_every_row_unlaunchable_is_not_reported_as_an_idle_queue(monkeypatch, tmp_path, capsys):
    """`nothing eligible` means the backlog was read and nothing qualified.
    A backlog whose every row was skipped is a different state and the log must
    not print the same sentence for both (loops.md's reading table)."""
    rows = [{"number": 47, "title": "role", "updatedAt": "2026-08-20T01:00:00Z",
             "body": _FILES_ROLE, "labels": []}]
    monkeypatch.setattr(qops_pickup, "report_unlaunchable", lambda *a, **k: None)
    assert qops_pickup.first_launchable(tmp_path, rows) is None
    assert "cannot write" in capsys.readouterr().out


# --------------------------------------------------------------------------
# #49 — three strikes stop the pickup. Two correct mechanisms cancelled out:
# the Loop Doctor's finding 1 made the claim the no-progress stop, and #122
# made a failed run release that claim so a row is never stuck. Together a
# deterministically failing row is picked every hour forever - #47 burned four
# sessions an hour apart, and nothing counted.
# --------------------------------------------------------------------------

def _ledger(tmp_path, *events):
    d = tmp_path / ".qops"
    d.mkdir(exist_ok=True)
    with (d / "ledger.jsonl").open("w", encoding="utf-8") as fh:
        for i, (event, num) in enumerate(events):
            fh.write(json.dumps({"ts": f"2026-08-20T{i:02d}:00:00+00:00",
                                 "event": event, "issue": str(num)}) + "\n")
    return tmp_path


def test_three_consecutive_failed_runs_stop_the_pickup(tmp_path):
    two = _ledger(tmp_path, ("pickup", 47), ("pickup_release", 47),
                  ("pickup", 47), ("pickup_release", 47))
    assert qops_pickup.strikes(two, "47") == 2
    assert not qops_pickup.struck_out(two, "47")

    three = _ledger(tmp_path, ("pickup", 47), ("pickup_release", 47),
                    ("pickup", 47), ("pickup_release", 47),
                    ("pickup", 47), ("pickup_release", 47))
    assert qops_pickup.strikes(three, "47") == 3
    assert qops_pickup.struck_out(three, "47")


def test_a_successful_run_resets_the_count(tmp_path):
    """Consecutive, not cumulative. The off-by-one here fails open - it keeps
    burning sessions - so the interleaved case is the one that matters."""
    root = _ledger(tmp_path,
                   ("pickup", 47), ("pickup_release", 47),
                   ("pickup", 47), ("pickup_release", 47),
                   ("pickup", 47),                      # no release: it worked
                   ("pickup", 47), ("pickup_release", 47))
    assert qops_pickup.strikes(root, "47") == 1
    assert not qops_pickup.struck_out(root, "47")


def test_strikes_are_counted_per_row(tmp_path):
    root = _ledger(tmp_path, ("pickup", 47), ("pickup_release", 47),
                   ("pickup", 33), ("pickup_release", 33),
                   ("pickup", 47), ("pickup_release", 47))
    assert qops_pickup.strikes(root, "47") == 2
    assert qops_pickup.strikes(root, "33") == 1


def test_a_skip_is_not_a_strike(tmp_path):
    """#48 skips a row before the claim and spends nothing. Counting that as a
    failure would apply no-auto to a row no session ever attempted."""
    root = _ledger(tmp_path, ("pickup_skip", 13), ("pickup_skip", 13),
                   ("pickup_skip", 13))
    assert qops_pickup.strikes(root, "13") == 0


def test_the_strike_count_is_windowed(tmp_path):
    """A ledger grows forever and an enablement six weeks ago is not this
    week's evidence. Releases outside the window do not count."""
    d = tmp_path / ".qops"
    d.mkdir(exist_ok=True)
    old, new = "2026-06-01T00:00:00+00:00", "2026-08-20T00:00:00+00:00"
    with (d / "ledger.jsonl").open("w", encoding="utf-8") as fh:
        for ts in (old, old, old):
            fh.write(json.dumps({"ts": ts, "event": "pickup_release",
                                 "issue": "47"}) + "\n")
        fh.write(json.dumps({"ts": new, "event": "pickup_release",
                             "issue": "47"}) + "\n")
    assert qops_pickup.strikes(tmp_path, "47", now="2026-08-20T12:00:00+00:00") == 1


def test_removing_no_auto_hands_a_struck_row_back(tmp_path):
    """The strike-out comment promises `Remove no-auto to hand it back to the
    loop`. struck_out() must honor that: with no-auto still on the row the
    three-strike history stands, and once it is removed the count restarts
    after the pickup_struck_out event rather than reading the row as struck
    out for the rest of the window (#99)."""
    root = _ledger(tmp_path, ("pickup", 47), ("pickup_release", 47),
                   ("pickup", 47), ("pickup_release", 47),
                   ("pickup", 47), ("pickup_release", 47),
                   ("pickup_struck_out", 47))
    assert qops_pickup.struck_out(root, "47", {"no-auto"})
    assert not qops_pickup.struck_out(root, "47", set())


def test_a_pass_where_every_row_struck_out_names_that_as_the_reason(monkeypatch, tmp_path, capsys):
    """Falling through to the `unwritable` message when every eligible row
    was actually skipped as struck out names the wrong cause (#48's message
    for #49's skip). The final line must say struck out."""
    row = {"number": 47, "title": "struck", "updatedAt": "2026-08-20T01:00:00Z",
           "body": "just a body", "labels": [{"name": "state:planned"},
                                              {"name": "gate:machine"},
                                              {"name": "ready:auto"}]}
    _ledger(tmp_path, ("pickup", 47), ("pickup_release", 47),
           ("pickup", 47), ("pickup_release", 47),
           ("pickup", 47), ("pickup_release", 47))
    monkeypatch.setattr(qops_pickup, "backlog", lambda _root: [row])
    monkeypatch.setattr(qops_pickup.qconfig, "load", lambda _root: {})
    qops_pickup._run([], tmp_path)
    printed = capsys.readouterr().out
    assert "struck out" in printed
    assert "cannot write" not in printed


def test_striking_out_is_loud_and_says_it_is_a_widening(monkeypatch, tmp_path):
    """A machine writing no-auto is a real widening: every other no-auto in
    this substrate is the owner's. It is defensible only because the
    alternative is an unbounded spend, and it must say so on the row."""
    calls = []
    monkeypatch.setattr(qops_pickup.subprocess, "run",
                        lambda *a, **k: calls.append(a[0]) or _Ok())
    qops_pickup.strike_out(tmp_path, "47", 3, "no commit and no PR")
    flat = " ".join(" ".join(c) for c in calls)
    assert "no-auto" in flat
    assert "3" in flat and "no commit and no PR" in flat
    assert "owner" in flat.lower()


class _Ok:
    returncode = 0
    stdout = ""
    stderr = ""


# --------------------------------------------------------------------------
# #50 — a launched run leaves a readable account. When #47 failed four times
# the substrate had recorded only "produced nothing (no commit and no PR)",
# which names the symptom. The cause (#48) was recoverable only from raw
# Claude Code transcripts in ~/.claude/projects, and only because the ledger
# happens to record session_id. CLAUDE.md: a swallowed exception must leave a
# state change behind - the release was the state change, and the account was
# the missing half.
# --------------------------------------------------------------------------

def test_a_launched_run_leaves_a_readable_log_the_release_names(monkeypatch, tmp_path):
    (tmp_path / ".qops").mkdir()
    log = qops_pickup.run_log_path(tmp_path, "47")
    assert log.parent == tmp_path / ".qops" / "runs"
    assert log.name.startswith("47-") and log.suffix == ".log"

    calls = []
    monkeypatch.setattr(qops_pickup.subprocess, "run",
                        lambda *a, **k: calls.append(a[0]) or _Ok())
    monkeypatch.setattr(qops_pickup, "strikes", lambda *a, **k: 1)
    qops_pickup.release(tmp_path, "47", "no commit and no PR", log)
    body = " ".join(" ".join(c) for c in calls)
    assert log.name in body, "the release must name where the account is"


def test_a_run_that_produced_nothing_says_so_on_the_row(monkeypatch, tmp_path):
    """#93 — the owner heard nothing until the third strike, because the run's
    own account never left the host. `release()` now attaches the log tail,
    bounded by `RELEASE_TAIL_CHARS`, and skips a second copy for a run already
    reported."""
    (tmp_path / ".qops").mkdir()
    log = qops_pickup.run_log_path(tmp_path, "82")
    log.write_text("x" * (qops_pickup.RELEASE_TAIL_CHARS * 3) + "TAIL END",
                    encoding="utf-8")

    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if cmd[:3] == ["gh", "issue", "view"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(qops_pickup.subprocess, "run", fake_run)
    monkeypatch.setattr(qops_pickup.ledger, "append", lambda *a, **k: None)

    qops_pickup.release(tmp_path, "82", "no commit and no PR", log)

    comment = next(c for c in calls if c[:3] == ["gh", "issue", "comment"])
    posted = comment[-1]
    assert "no commit and no PR" in posted
    assert str(log) in posted
    assert "TAIL END" in posted
    assert len(posted) < qops_pickup.RELEASE_TAIL_CHARS + 500

    # Same run, already reported: the marker is in the tracker's comments.
    calls.clear()
    monkeypatch.setattr(qops_pickup.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(
                            cmd, 0, posted, "") if cmd[:3] == ["gh", "issue", "view"]
                        else calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""))
    qops_pickup.release(tmp_path, "82", "no commit and no PR", log)
    assert not any(c[:3] == ["gh", "issue", "comment"] for c in calls)


def test_a_deduped_release_still_counts_as_a_strike(monkeypatch, tmp_path):
    """The comment is the report; the ledger row is the state. `strikes()`
    reads a `pickup` with no `pickup_release` after it as a run that *worked*
    and resets the count, so a release that returns early without writing one
    disarms #49's three-strike budget — and the row is re-picked hourly,
    forever, which is the failure that budget exists to stop."""
    (tmp_path / ".qops").mkdir()
    log = qops_pickup.run_log_path(tmp_path, "82")
    log.write_text("refused, again", encoding="utf-8")
    already = f"pickup-loop: run {log.name} produced nothing"

    monkeypatch.setattr(qops_pickup.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(
                            cmd, 0, already if cmd[:3] == ["gh", "issue", "view"]
                            else "", ""))
    for _ in range(qops_pickup.STRIKES):
        ledger.append(tmp_path, "pickup", {"issue": "82"})
        qops_pickup.release(tmp_path, "82", "no commit and no PR", log)

    assert qops_pickup.strikes(tmp_path, "82") == qops_pickup.STRIKES
    assert qops_pickup.struck_out(tmp_path, "82")


def test_the_run_log_directory_is_ignored_by_git():
    """This repo is public (ADR-0022) and the log is a transcript of an
    unattended session. `.gitignore` enumerates `.qops/` paths one by one, so a
    new directory under it is tracked by default - the failure mode is a
    transcript published, and a secret in a public repo is rotated first.

    Asserted by asking git, not by grepping .gitignore: the pattern can be
    present and not match."""
    probe = "'.qops/runs/47-20260820T170000Z.log'".strip("'")
    out = subprocess.run(["git", "check-ignore", "-q", probe],
                         cwd=REPO, capture_output=True, text=True)
    assert out.returncode == 0, f"{probe} is not ignored — it would be committed"


def test_the_log_does_not_change_what_counts_as_a_failed_run():
    """Capturing output must not become the thing that decides. `produced_work`
    counting commits ahead of the default branch is what stands behind #122,
    and an empty branch scoring as success is how two sorties died silently."""
    src = (REPO / "scripts" / "qops_pickup.py").read_text(encoding="utf-8")
    launch = src[src.index("def main("):]
    assert "produced_work(root, num, before)" in launch
    assert "rev-list" in src or "produced_work" in src


# --------------------------------------------------------------------------
# #55 — the planner writes the plan onto the row. 16 of 19 open rows sat at
# state:triage because moving one to state:planned meant a person writing what
# done looks like. The role already knew what a plan must carry and had nowhere
# to put it: "a plan that lives only in a message is lost", its own words.
# --------------------------------------------------------------------------

def test_a_plan_is_machine_input_and_an_ask_is_one_page():
    """Two shapes, asserted separately. The ask format must not leak into
    plans - nobody reads them (ADR-0028 §3) - and must not be dropped from
    `type:decision` rows, where the owner genuinely is the reader and where
    both rows in the whole corpus that changed an outcome were asks."""
    role = _role("planner")
    assert "machine input" in role
    assert "a spec a coder executes and a test checks" in role
    assert "one page, and one page only" in role
    assert "`type:decision`" in role


def test_the_planner_appends_and_never_replaces():
    """The filing is the licence (ADR-0028). Overwriting it destroys the
    evidence of what the owner actually asked for, and #46's shape 1 needs the
    original intact."""
    role = _role("planner")
    assert "append" in role
    assert "never replace" in role


def test_the_planner_writes_state_planned_and_neither_owner_flag():
    role = _role("planner")
    assert "state:planned" in role
    assert "never `ready:auto`" in role
    assert "never `no-auto`" in role


def test_the_plan_must_clear_the_filing_bar_it_will_be_measured_by():
    """The planner sets state:planned, and #42's bar fires the moment a row
    leaves triage. A planner that writes a plan with no acceptance section
    turns `doctor` red on the row it just planned."""
    role = _role("planner")
    assert "## acceptance" in role
    assert "#42" in role or "filing bar" in role


@pytest.mark.parametrize("role", ["planner", "triager", "coder", "reviewer",
                                  "scribe", "interactor"])
def test_no_agent_cites_a_file_that_does_not_exist(role):
    """`CONTEXT.md` was cited twice in planner.md and has been CLAUDE.md since
    the extraction. The doc-link check only scans `.py`, so a role could cite a
    ghost indefinitely."""
    import re as _re
    body = (AGENT_DIR / f"{role}.md").read_text(encoding="utf-8")
    for cited in _re.findall(r"`([A-Za-z0-9_./-]+\.md)`", body):
        if cited.startswith("docs/") or cited.endswith("CLAUDE.md"):
            assert (REPO / cited).exists(), f"{role}.md cites missing {cited}"
        assert cited != "CONTEXT.md", f"{role}.md cites CONTEXT.md; it is CLAUDE.md"


def test_gitattributes_declares_text_auto():
    text = (REPO / ".gitattributes").read_text(encoding="utf-8")
    assert "* text=auto" in text


# --------------------------------------------------------------------------
# #71 — auto-eligible AND unlaunchable is the reportable state, not either
# half alone. #57 passed every eligibility test and named .claude/agents/, and
# the queue read empty for an hour with nothing saying why.
# --------------------------------------------------------------------------

_ROLE_FILES = "## Files\n\nExpected to touch: `.claude/agents/triager.md`\n"
_OK_FILES = "## Files\n\nExpected to touch: `qops/install.py`\n"


def test_an_auto_eligible_row_the_launch_cannot_write_is_reported():
    auto_eligible_and_unwritable = {
        "number": 57, "body": _ROLE_FILES,
        "labels": [{"name": "state:planned"}, {"name": "gate:machine"},
                   {"name": "ready:auto"}],
    }
    unwritable_not_auto_eligible = {
        # #13's shape: names no test, so eligible() is False by every route.
        "number": 13, "body": _ROLE_FILES,
        "labels": [{"name": "state:planned"}, {"name": "gate:machine"}],
    }
    auto_eligible_and_writable = {
        "number": 70, "body": _OK_FILES,
        "labels": [{"name": "state:planned"}, {"name": "gate:machine"},
                   {"name": "ready:auto"}],
    }
    no_files_section = {
        "number": 42, "body": "just a body",
        "labels": [{"name": "state:planned"}, {"name": "gate:machine"},
                   {"name": "ready:auto"}],
    }
    problems = install.unlaunchable_and_auto_eligible(
        [auto_eligible_and_unwritable, unwritable_not_auto_eligible,
         auto_eligible_and_writable, no_files_section])
    assert len(problems) == 1
    assert "#57" in problems[0]
    assert ".claude/agents/triager.md" in problems[0]
    assert not any("#13" in p or "#70" in p or "#42" in p for p in problems)


# --------------------------------------------------------------------------
# migrate — #103. `install.issue_invariants` reports a row missing `origin:`,
# a `gate:`, or ADR-0028's outcome statement; this is what fixes it, and
# ADR-0030 says nothing is written to the tracker until the owner has read
# one diff. Fixture-driven throughout: `propose()` is pure, and `dry_run` /
# `execute` / `verify` take an injected `run` the same way reconcile does.
# --------------------------------------------------------------------------

from qops import migrate as migratemod  # noqa: E402


class FakeMigrateGh:
    """Holds open issues by number; `edit --body`/`--add-label` mutate them
    in place, so a second call sees the first call's effect."""

    def __init__(self, issues):
        self.issues = {str(i["number"]): i for i in issues}
        self.calls = []

    def _list(self):
        return json.dumps(list(self.issues.values()))

    def __call__(self, args):
        self.calls.append(args)
        if args[:2] == ["issue", "list"]:
            return self._list()
        if args[:2] == ["issue", "edit"]:
            num = args[2]
            issue = self.issues[num]
            names = {l["name"] for l in issue["labels"]}
            i = 0
            while i < len(args):
                if args[i] == "--add-label":
                    names.add(args[i + 1])
                elif args[i] == "--remove-label":
                    names.discard(args[i + 1])
                elif args[i] == "--body":
                    issue["body"] = args[i + 1]
                i += 1
            issue["labels"] = [{"name": n} for n in sorted(names)]
            return ""
        raise AssertionError(f"unexpected gh call: {args}")


def _undermigrated_row(number=200):
    return {"number": number, "body": "no acceptance here",
            "labels": [{"name": "state:planned"}, {"name": "type:code"}]}


def test_migrate_propose_names_missing_origin_gate_and_outcome():
    plan = migratemod.propose([_undermigrated_row()])
    row = plan["rows"][0]
    assert set(row["add_labels"]) == {"origin:pending", "gate:machine"}
    assert row["body"] is not None and "## Acceptance" in row["body"]
    assert row["disposition"] == "keep"


def test_migrate_propose_leaves_a_compliant_row_alone():
    compliant = {"number": 201, "labels": [{"name": "origin:owner"},
                                           {"name": "gate:machine"}],
                "body": "## Acceptance\n\nSomething a machine can check."}
    plan = migratemod.propose([compliant])
    row = plan["rows"][0]
    assert row["add_labels"] == [] and row["body"] is None


def test_migrate_dry_run_writes_the_plan_and_touches_no_tracker_call(tmp_path):
    (tmp_path / ".qops").mkdir()
    gh = FakeMigrateGh([_undermigrated_row()])
    plan = migratemod.dry_run(tmp_path, "o/r", run=gh)
    assert migratemod.plan_path(tmp_path).exists()
    assert plan["rows"][0]["add_labels"]
    # Wrong if: any code path writes to the tracker during --dry-run.
    assert all(c[:2] == ["issue", "list"] for c in gh.calls)
    # And the tracker itself is unchanged.
    assert gh.issues["200"]["labels"] == _undermigrated_row()["labels"]


def test_migrate_execute_applies_the_plan_once(tmp_path):
    (tmp_path / ".qops").mkdir()
    gh = FakeMigrateGh([_undermigrated_row()])
    migratemod.dry_run(tmp_path, "o/r", run=gh)
    result = migratemod.execute(tmp_path, "o/r", run=gh)
    assert result["ok"] and result["applied"] == ["200"]
    names = {l["name"] for l in gh.issues["200"]["labels"]}
    assert {"origin:pending", "gate:machine"} <= names
    assert "## Acceptance" in gh.issues["200"]["body"]


def test_migrate_execute_refuses_on_a_corpus_that_moved(tmp_path):
    (tmp_path / ".qops").mkdir()
    gh = FakeMigrateGh([_undermigrated_row()])
    migratemod.dry_run(tmp_path, "o/r", run=gh)
    # The corpus moves after the plan was drawn — a label edit nobody replayed.
    gh.issues["200"]["labels"].append({"name": "no-auto"})
    result = migratemod.execute(tmp_path, "o/r", run=gh)
    assert not result["ok"] and result["applied"] == []
    # Nothing was written: only the two reads (one per dry_run/execute call).
    assert not any(c[:2] == ["issue", "edit"] for c in gh.calls)


def test_migrate_verify_passes_after_a_full_apply(tmp_path):
    (tmp_path / ".qops").mkdir()
    gh = FakeMigrateGh([_undermigrated_row()])
    migratemod.dry_run(tmp_path, "o/r", run=gh)
    migratemod.execute(tmp_path, "o/r", run=gh)
    result = migratemod.verify(tmp_path, "o/r", run=gh)
    assert result["ok"] and result["mismatches"] == []


def test_migrate_verify_fails_on_a_half_applied_fixture(tmp_path):
    (tmp_path / ".qops").mkdir()
    gh = FakeMigrateGh([_undermigrated_row()])
    migratemod.dry_run(tmp_path, "o/r", run=gh)
    migratemod.execute(tmp_path, "o/r", run=gh)
    # Simulate a half-applied state: the label landed, the body edit did not.
    gh.issues["200"]["body"] = "no acceptance here"
    result = migratemod.verify(tmp_path, "o/r", run=gh)
    assert not result["ok"]
    assert any("body" in m for m in result["mismatches"])


def test_migrate_main_dispatches_the_three_flags(tmp_path, capsys):
    (tmp_path / ".qops").mkdir()
    cfg = {"repo": "o/r"}
    gh = FakeMigrateGh([_undermigrated_row()])
    import qops.migrate as m
    orig = m.gh
    try:
        m.gh = gh
        assert m.main(["--dry-run"], tmp_path, cfg) == 0
        assert m.main(["--execute"], tmp_path, cfg) == 0
        assert m.main(["--verify"], tmp_path, cfg) == 0
        assert m.main([], tmp_path, cfg) == 2
    finally:
        m.gh = orig


# --------------------------------------------------------------------------
# init — #104. `qops init` in an empty folder scaffolds the five mechanical
# preconditions docs/reference/qops-contract.md names; `qops doctor` there
# must then report only the three owner/machine ones, by name (ADR-0024: by
# execution, not by inspecting the template).
# --------------------------------------------------------------------------

def test_init_refuses_a_repo_that_already_has_a_config(tmp_path):
    (tmp_path / ".qops").mkdir()
    (tmp_path / ".qops" / "config.yml").write_text("project: x\n",
                                                    encoding="utf-8")
    rc = initmod.main(["--project", "x", "--repo", "a/b", "--python", "python3"],
                      tmp_path, {})
    assert rc == 2


def test_init_missing_flags_refuses_without_a_tty(tmp_path, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    rc = initmod.main(["--project", "x"], tmp_path, {})
    assert rc == 2
    assert not (tmp_path / ".qops" / "config.yml").exists()


def test_qops_init_then_doctor_leaves_only_the_owner_preconditions(
        tmp_path, monkeypatch):
    # `doctor` reads the three only off a PR, and this suite's own gate run is
    # on one: both refs are set for every job of a `pull_request` workflow.
    monkeypatch.delenv("GITHUB_BASE_REF", raising=False)
    monkeypatch.delenv("GITHUB_HEAD_REF", raising=False)
    rc = initmod.main(
        ["--project", "demo", "--repo", "qvajda/qops-init-104-fixture",
         "--python", "python3"], tmp_path, {})
    assert rc == 0

    for expect in (".qops/config.yml", "CLAUDE.md", ".claude/settings.json",
                  "skills-lock.json", ".claude/skills/interview/SKILL.md",
                  ".claude/skills/spec-to-issue/SKILL.md",
                  ".claude/skills/triage/SKILL.md"):
        assert (tmp_path / expect).exists(), f"{expect} not written"
    for name in install.WORKFLOWS:
        assert (tmp_path / ".github" / "workflows" / name).exists()

    cfg = qconfig.load(tmp_path)
    assert install.drift(tmp_path, cfg) == []
    assert install.skill_drift(tmp_path, cfg) == []
    assert install.undeclared_labels(cfg) == []

    problems = install.doctor(tmp_path, cfg)
    assert len(problems) == 3, problems
    assert any("branch protection" in p for p in problems)
    assert any("auto-merge" in p for p in problems)
    assert any("workspace has not been trusted" in p for p in problems)


def test_doctor_does_not_judge_the_owner_preconditions_on_a_pull_request(
        tmp_path, monkeypatch):
    """The three are unanswerable in `gate`, and `gate` is a required check.

    A runner's token cannot read branch protection and a stateless runner has
    no `~/.claude.json`, so all three read "not confirmed" there whatever the
    repo is actually set to - PR #109 sat red on a `master` that had every one
    of them on. A PR cannot fix any of them, so the branch never merges.
    """
    rc = initmod.main(
        ["--project", "demo", "--repo", "qvajda/qops-init-104-fixture",
         "--python", "python3"], tmp_path, {})
    assert rc == 0
    cfg = qconfig.load(tmp_path)

    monkeypatch.setenv("GITHUB_BASE_REF", "master")
    monkeypatch.setenv("GITHUB_HEAD_REF", "feat/104-qops-init")
    on_a_pr = install.doctor(tmp_path, cfg)
    assert on_a_pr == [], on_a_pr

    # Not dropped, only moved off the merge path: the instrument still reads
    # them where they are answerable, and `init` prints them as next steps.
    assert len(install.owner_preconditions(tmp_path, cfg)) == 3


def test_init_prints_the_contracts_owner_only_next_steps():
    text = initmod.NEXT_STEPS
    for phrase in ("qops_import.py --labels", "/interview",
                  "branch protection", "Allow auto-merge",
                  "Automatically delete head branches", "trust this workspace",
                  "enable the loop"):
        assert phrase in text, f"{phrase!r} missing from the printed next steps"
