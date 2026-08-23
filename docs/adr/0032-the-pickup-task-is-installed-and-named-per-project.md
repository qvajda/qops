---
status: accepted
revisit-after: 2026-12-01
---

# The pickup task is installed from the config, and its name carries the project

`pickup-loop` is the only loop that costs money and, until this decision, the
only part of qops with no installer. Its registration was a hand-made machine
fact: an absolute interpreter path, an absolute checkout path, and the
machine-global name `qops-pickup-loop` at the root of the Windows task
namespace. Every other loop is a rendered file under version control.

Three things followed from that, and the third is the expensive one:

- A second project installing qops either collided on the name or, with a force
  flag, **replaced the first project's loop in silence** — the failure is not
  two loops fighting, it is project 1's loop quietly ceasing to exist. ADR-0009
  already names this class: the desktop host's failure mode is silence.
- `.qops/config.yml` carries `python: py -3` precisely because a hardcoded
  absolute interpreter is not committable, and the task ignored that and baked
  one in anyway. The config's claim to be the only place a project specific may
  live was false while the task existed.
- The definition could not be reinstalled into a second project, could not be
  rebuilt after a machine rebuild, and existed in exactly one place that was
  not the repo.

## Decision

**`qops install` owns the task, and its identity is derived from the config.**

- **Task path `\qops\<project>\pickup-loop`**, with `<project>` read from
  `.qops/config.yml`. A folder rather than a flat prefix with a suffix: one
  query over the `qops` folder lists every project's loop on the machine at
  once, which a suffixed flat name cannot do. The runner-up — keeping the flat
  name and appending the project — is rejected on the record: it is a smaller
  diff that leaves the loops unlistable as a group and leaves the
  hand-registration problem, which is the expensive half, untouched.
- **The command is rendered from the config**, the same way the workflows are:
  interpreter from `python:`, script and `--root` from the root the installer
  was run in. No absolute interpreter path is committed anywhere.
- **Registering never enables.** A fresh registration is disabled; a
  re-install of a task the owner had enabled leaves it enabled rather than
  quietly undoing their act. An installer that helpfully starts the expensive
  loop would be a bigger defect than the one this closes — ADR-0009's whole
  cost argument rests on the loop being off unless the owner turned it on.
- **`--launch` moves out of the task definition and behind `pickup_launch:`,
  default off.** Baked into the schedule, the dry run was unreachable from it,
  so there was no way to prove the wiring fires without spending.
- **`qops doctor` reports the task**: drift between the registered command and
  what the config renders is a problem; the enabled/disabled state is printed
  and is never a problem and never changed. Which state the expensive loop is
  in is the owner's answer, not the instrument's.
- **`qops install --unregister-task` is the deregistration path**, so a project
  that goes away does not leave an orphan firing hourly at a deleted tree.

## What makes this wrong

An installer that enables the task. Everything else here is hygiene; that one
is the defect the fix would have introduced.

## Consequences

- The naming scheme binds every future project on the machine, which is why
  this is an ADR and not a commit message. `\qops\` is now reserved.
- A host without a scheduler — every CI runner — answers nothing rather than
  answering "clean": `registered_task` returns `None` there, and both the drift
  check and the state line report unknown instead of agreement.
- The previously hand-registered flat task is not migrated by code. It is one
  `Unregister-ScheduledTask` by the owner, and a machine that keeps it fires
  two pickers at one root.
