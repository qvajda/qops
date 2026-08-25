---
name: triage
description: Walk open issues through the qops state machine, applying the label taxonomy from .qops/config.yml. Owner-invoked only.
disable-model-invocation: true
---

# Triage

**Owner-only, by decision (CADR-0005).** Triage walks a state machine over many
issues and relabels in a batch. `gh issue list` is the source of truth, so a
mis-read taxonomy corrupts the thing every future session reads first. The
reflex we want from an agent is `/spec-to-issue`, not this.

## The taxonomy is a file, not this document

Read `.qops/config.yml` `labels:` at the start of every run. Do not write a
label from memory and do not re-declare a vocabulary here — this skill exists
precisely because an external one did.

Every **open** issue carries exactly one `type:`, one `state:` and one `gate:`
(`validate.require_on_open`). `mission:` is one of the configured missions.

## States and the transitions that are legal

```
triage → planned → building → gate → review → done
   ↓         ↓         ↓         ↓        ↓
        blocked (any state) · cancelled (any state)
```

- `triage` — imported, not yet specified. The only legal state at import.
- `planned` — acceptance criteria and a real gate exist. `/spec-to-issue`
  produces this; triage does not invent it.
- `building` — claimed. `pickup-loop` sets this itself before launching; do not
  set it by hand for an unattended sortie or the claim stops being the
  no-progress stop.
- `gate` — a PR is open and the machine checks are running.
- `review` — gates green, waiting on a taste review. Only `gate:taste` work
  legitimately rests here; a `gate:machine` PR merges itself (CADR-0006).
- `done` — `qops close` writes this. Do not set it by hand.
- `blocked` / `cancelled` — terminal-ish; both need a reason in a comment, or
  the label is a guess with a label's authority.

## What triage may and may not do

**May:** apply a missing `type:`/`state:`/`gate:`/`mission:`; correct a label
that contradicts the issue body; list what it could not classify.

**May not:**
- apply **`ready:auto`** — ever, in any circumstance. That flag means an
  unattended agent may start the work, and it is the owner's alone to grant.
  Refuse it in particular on a row whose body names no test file (a
  `tests/…​.py` path or a `test_*` node id) — nothing can prove it done (R8).
  Report such a row as untriaged; do not edit it to add one.
- decide priority, close an issue, or edit an issue body.
- guess. When `type:` or `gate:` is genuinely ambiguous, **leave it and list
  it.** A guessed label reads exactly like a decided one, which is worse than a
  gap.

## A run

1. `gh issue list --state open --json number,title,labels` — one call, not one
   per issue.
2. Bucket: **untriaged** (missing a required label), **contradicted** (label
   disagrees with the body), **stale** (`state:building` with no open PR).
3. Show the buckets with a one-line summary each and the exact `gh issue edit`
   commands you propose. Wait.
4. Apply what the owner approves. Report what you left, and why.

`gate:none` is legal and is not a defect to fix in bulk — it blocks `ready:auto`
until a real gate is chosen when the sortie is planned. Report the count; do not
convert them.
