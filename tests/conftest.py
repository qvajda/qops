"""The suite does not read the runner's environment (#65).

Four PRs merged on 2026-08-20 and one on 2026-08-21 each needed a second push
because a test passed on the owner's laptop and failed on the runner. They were
not five defects. They were one: a test that does not set these variables
inherits whatever the process was started with, so the same test asks a
different question in the two places.

  test_declared_version_is_not_already_tagged (#40)
      red on a laptop, and vacuous on the runner, where `actions/checkout`
      fetches no tags — wrong in both directions at once.
  test_doctor_says_how_many_rows_the_invariants_read (#63)
      asks a tracker-wide question, inherited the runner's GITHUB_HEAD_REF,
      and got the single-row answer.
  test_the_picker_loads_the_substrate_from_the_root_it_names (#74)
      asserted an exit code that depended on `gh` having a token — written,
      with no irony intended, to prove a different environment-shaped defect.

The obvious framing is "give me a way to run the gate's exact environment
locally". This is the cheaper read: a test that depends on ambient environment
is underspecified, and the fix is to stop letting it. A test that wants a PR
context sets one and says so; a test that wants none gets none, on both
machines.

This clears the variables for the pytest process itself, so a subprocess a test
spawns inherits the clean environment too — which is the half that matters for
the picker tests, since the defect they cover lives in how a process starts.
"""

import os

import pytest

# Set by GitHub Actions, or by `gate.yml`, or by a pickup-loop launch. Every one
# of them changes what the code under test decides, and none of them is the
# suite's to inherit.
RUNNER_ENV = ("GITHUB_BASE_REF", "GITHUB_HEAD_REF", "QOPS_STRICT",
              "QOPS_UNATTENDED", "QOPS_ROLE", "PR_NUMBER", "PR_HEAD_SHA")


def pytest_configure(config):
    """Registered here rather than in `pyproject.toml` (#264): the marker is a
    fact about this suite, and `pyproject.toml` is the packaging surface the
    version tests already police."""
    config.addinivalue_line(
        "markers",
        "slow: spawns a venv and installs over the network — runs in CI, "
        "deselect locally with `-m 'not slow'`")


@pytest.fixture(autouse=True)
def _no_ambient_runner_env(monkeypatch):
    for name in RUNNER_ENV:
        monkeypatch.delenv(name, raising=False)
    # `monkeypatch` restores them when the test ends, so a test that wants one
    # sets it with `monkeypatch.setenv` and gets it — this only removes the
    # inheritance, never the ability to ask for it.
    assert not any(n in os.environ for n in RUNNER_ENV)
