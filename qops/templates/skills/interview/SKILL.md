---
name: interview
description: Grill a plan, a decision or a half-formed idea into shape, one round of questions at a time, before any code or any issue is written. Use when the owner says "grill me", "stress-test this", "interview me", or when a request is about to become a sortie and its acceptance criteria are still prose.
---

# Interview

The design-tree round mechanic. qops-native, because the rounds have to end in
*this* repo's artefacts — an ADR under `docs/adr/`, a constraint record under
`docs/constraints/`, a sortie issue carrying `.qops/config.yml`'s taxonomy — and
not in a generic summary.

Owner present, always. This is the one procedure that spends the scarce
resource, so it is sized to be worth it.

## The round

One round is: **three to six questions, asked together, answered together.**
Never one question at a time — that turns a ten-minute interview into an
afternoon of turn-taking.

Each question must be able to change what gets built. If an answer changes
nothing, it is a question about your own understanding; go read the code
instead. Read `CONTEXT.md` and `docs/adr/` *before* round one so you do not
spend a round asking what is already decided.

Rounds stop when the next round would only confirm. Three rounds is a lot; five
means the thing is a mission, not a sortie, and the finding is the split.

## What each round hunts

1. **Round one — the premise.** Does this need to exist? What breaks today, for
   whom, observed how? A defect nobody observed is a guess.
2. **Round two — the boundary.** What is explicitly *not* in scope? Which files
   must not be touched? What is the smallest version that is still worth doing?
3. **Round three — the failure.** What would make this wrong? What check would
   catch that, and does the check exist? Name the gate: `machine` or `taste`.
   A gate of neither class is not a gate.

## Standing rules for this repo

- **Never open a round on activation, or on rewriting git history.** Both are
  closed owner decisions (CLAUDE.md). Asking again is not diligence.
- **A hard constraint is not a variable.** If an answer would require changing
  one, say so and stop the round; the move is to amend its record, not to
  negotiate it inside an interview.
- **An instruction is a preference; a check is a control.** When a round lands
  on "the copy must never say X", the round is not finished until it has named
  where the assertion goes.

## Ending it

Every interview ends in something written down, or it did not happen:

- a decision that binds future sessions → an ADR (`docs/adr/`, next number);
- an external fact we do not control → `docs/constraints/`;
- work to do → `/spec-to-issue`, which writes the taxonomy.

State which one you are producing before you produce it, and hand the owner a
one-page ask if a decision is still needed: summary first, at most four
options, one recommendation.
