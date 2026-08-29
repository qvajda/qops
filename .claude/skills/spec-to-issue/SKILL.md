---
name: spec-to-issue
description: Turn a decided thing into a sortie issue on the tracker, carrying this repo's label taxonomy and acceptance criteria something can actually check. Use when work has been decided and is not yet an issue, or when the session is about to start building against a spec that lives only in the conversation.
---

# Spec → issue

Synthesis that writes **this** taxonomy. An external equivalent writes a generic
one, which is how `gh issue list` — the source of truth — stops agreeing with
`.qops/config.yml`.

**Invocable by the model; the publish step is not.** Noticing a spec is missing
and drafting one is the reflex we want (CADR-0005). Opening an issue from a
half-formed discussion is not. So: draft freely, show the draft, and **run `gh
issue create` only after the owner says so, in this session, for this draft.**

## Read first

`.qops/config.yml` is the taxonomy. Prose descriptions of it anywhere else are
the copy, not the original. Read it every time — do not write labels from
memory.

## One sortie = one session

If the work does not fit one session, say so and propose the split. Do not
widen the issue to cover the mission, and do not fold in adjacent problems you
noticed on the way. A sortie that no longer fits is a finding to report.

## Search before you draft

Read the open backlog in **one** `gh issue list --state open --json
number,title,labels` call — the same single-call discipline `/triage` already
keeps. A call that filters parked rows out is the failure this step exists to
prevent: `priority:parked` is a good idea deliberately made quiet, not one
that has stopped existing.

Report near-matches by **number and title** before drafting anything. Where a
near-match carries `priority:parked`, propose **unparking that row** — drop
the label, update its body if the ask has grown — instead of drafting a new
one. The outcome is one row, not two with a cross-reference.

**Invocable by the model; the edit is not.** Same rule as the publish step
below: proposing the unpark is the reflex we want, editing either row is not.
The owner decides; this skill never edits the matched row or files a
duplicate alongside it.

## The body

```markdown
<what is broken or missing, and how it was observed — file:line where it exists>

## Why it matters now

<what this unblocks, or what it costs to leave. Skip if the answer is "tidiness".>

## Scope

- <change 1, concrete enough to diff against>
- <change 2>
- Explicitly not: <the adjacent thing you are refusing to fold in>

## Acceptance

- <a criterion a machine or an owner can check — a command, a file state, a number>
- <what would make this wrong, stated before the work starts>

## Files

Expected to touch: `<paths>` · Must not touch: `<paths>`
```

## The labels

Exactly one `type:`, one `state:` and one `gate:`, from `.qops/config.yml`:

- `type:` — what the work *is*, not how urgent it is.
- `state:` — `triage` on import; `planned` once this skill has written acceptance
  criteria and a real gate. Nothing else is yours to set.
- `gate:` — `machine` if the acceptance criteria are a command, `taste` if a
  human has to look at it. `gate:none` is legal at import and blocks `ready:auto`
  until a real gate is chosen. **Do not use `gate:none` on a sortie you just
  specified** — if you cannot name the gate, the spec is not finished.
- `mission:` — one, from the configured list.
- `origin:` — `owner` when the owner is present in this session, `agent` when a
  sortie filed it unattended. Not a judgement: the guard already knows which
  session this is and refuses a filing that claims the other one (CADR-0007). It
  is the input to the `ready:auto` grant, which is why it is not yours to pick.

**Never apply `ready:auto`.** It means an unattended agent may start the work
unsupervised, and it is the owner's alone to grant (CADR-0005, `loops.md`).

**Refuse `ready:auto` when the body names no test.** Even when the owner asks
for the label in this session, do not apply `ready:auto` to a body that names
no test file (a `tests/…​.py` path or a `test_*` node id) — nothing can prove
the row done (R8). File the row without the label, in `state:triage`, and say
which line is missing. A row with no test yet is a legitimate triage row; the
refusal is on the label, never on the filing (CADR-0011).

**On a `type:decision` row the body must name an output path.** The proposals
are an artefact that lands in a PR — a draft ADR under `docs/adr/`, or a
document under `docs/` — because that PR is the review moment (`CADR-0015`).
So `## Files` must name the path the proposals will be written to, the same way
every row must name a criterion. A `type:decision` row that names none is filed
in `state:triage` with the missing line called out — the refusal is on the
labelling, never on the filing (CADR-0011).

## Blocking edges

Declare dependencies as native tracker links (`gh issue edit --add-sub-issue`,
or `Blocked by #N` in the body if the API path is not available), never as an
ordered list in an epic body — a list nothing reads is not a dependency.

## After the owner confirms

1. `gh issue create` with the body and labels above.
2. Print the issue number and the branch it implies: `<type>/<issue#>-<slug>`.
   That branch name is what the PreToolUse guard and `qops brief` both parse.
