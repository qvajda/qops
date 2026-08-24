---
name: pending
description: Answer "what's the status", "what's waiting on me", "what's next" by running `qops pending` and reading its sections back. Use whenever the owner asks about backlog status instead of naming a row.
---

# Pending

The owner does not type `python -m qops pending` at the moment he wants to
know the status — he asks. This skill is that access path: run the verb, read
its sections back, offer to act on what it shows. It does not re-derive the
answer itself.

## Run it

```
python -m qops pending
```

Use the `python:` value from `.qops/config.yml` if a bare `python` is not the
right interpreter on this host.

## Read the sections back, don't re-derive them

The verb's output is already organized into sections — present them as they
come, do not re-sort, re-filter, or re-judge which row belongs where. Any
eligibility judgement about a row belongs in the code behind the verb, not in
this skill's reading of the output.

- **Waiting on you** — rows that need an owner act: a taste call, eyes on a
  review, something withheld pending a decision, a row that stalled after
  repeated failed attempts. Report each line as printed. If the verb printed
  `nothing` here, say plainly that nothing is waiting on him — do not soften
  it into "looks quiet" or omit the section.
- **Parked**, if present — rows deliberately set aside; mention the count, not
  each row.
- **With you (already claimed)** — rows an open session already holds. These
  are informational, not an ask.
- **qops doctor**, if present — problems the verb surfaced while it was
  reading the backlog anyway. Report them, don't investigate further unless
  asked.
- **What the loop takes next** — the build, plan, and decompose queues, each
  either the next row it would pick or `empty`. Report this section every
  time, even when the first section had entries — a session that only reports
  the owner's half makes an idle loop and a stuck loop look the same. If a
  queue prints `empty`, say so; don't drop the line.

## Offer, never take

You may offer to act on a row this lists — open it, start it, draft a reply.
You may not act on your own read of the list. The rows the first section
lists are, by construction, exactly the ones an agent does not get to move
forward unilaterally; treat every one of them that way even if the reason a
particular row is listed isn't obvious from its line.

## What this skill is not

Not a second reader of the backlog. If the verb's output looks wrong, that is
a bug in the verb, not something to work around by listing rows yourself and
judging them by hand — a hand-rolled reconstruction drifts from the real
picker within a week and disagrees with the machine it claims to report on.
Say what looks wrong and stop there.
