---
name: spec-to-issue
description: Turn a decided thing into a sortie issue on the tracker, carrying this repo's label taxonomy and acceptance criteria something can actually check. Use when work has been decided and is not yet an issue, or when the session is about to start building against a spec that lives only in the conversation.
---

# Spec → issue

Synthesis that writes **this** taxonomy. An external equivalent writes a generic
one, which is how `gh issue list` — the source of truth — stops agreeing with
`.qops/config.yml`.

**Invocable by the model; the publish step is not.** Noticing a spec is missing
and drafting one is the reflex we want (ADR-0019). Opening an issue from a
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
  session this is and refuses a filing that claims the other one (ADR-0023). It
  is the input to the `ready:auto` grant, which is why it is not yours to pick.

**Never apply `ready:auto`.** It means an unattended agent may start the work
unsupervised, and it is the owner's alone to grant (ADR-0019, `loops.md`).

## Blocking edges

Declare dependencies as native tracker links (`gh issue edit --add-sub-issue`,
or `Blocked by #N` in the body if the API path is not available), never as an
ordered list in an epic body — a list nothing reads is not a dependency.

## After the owner confirms

1. `gh issue create` with the body and labels above.
2. Print the issue number and the branch it implies: `<type>/<issue#>-<slug>`.
   That branch name is what the PreToolUse guard and `qops brief` both parse.
