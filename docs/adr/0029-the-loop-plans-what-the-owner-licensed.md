---
status: accepted
revisit-after: 2026-11-15
amends: 0017, 0023, 0028
---

# The loop plans what the owner licensed, and `origin:` says who licensed it

**Date:** 2026-08-21 · **Session:** the #67 interview, rounds one to three ·
**Amends:** ADR-0017's epic routing, ADR-0023's meaning of `origin:`, and
ADR-0028's build order · **Depends on:** ADR-0027, ADR-0028.

## Context

#67 asked whether "nothing unattended moves a row from `state:triage` to
`state:planned`" is still the design once the grant is mechanical. The question
carried a premise the interview removed in round one: that planning is an owner
act. It has never been one. ADR-0028 records the owner, verbatim:

> i don't write plans ever. None of the plans in qops or printshop were written
> by me. […] My role is to set the direction, not define how to get there.

So an agent already writes every plan. The only open question was **what invokes
that agent** — a session the owner started, or a schedule. That is a much
narrower question than #67 asked, and on its own it would not have been worth an
ADR.

Round one also moved the goal. The ask is not "the loop must never idle". It is:

> it shouldn't idle unless truly blocked behind a human-is-required task (manual
> setup of something, true taste needs to be given, major milestone like a
> go-live).

### What round three actually found

Three facts, each measured rather than reasoned:

**The semantic reader was decided and never built.** ADR-0028 §4 decided the
reviewer's verdict becomes a required status check, fail-closed on a real
verdict. The required checks on `master` today:

```
["test","gate","tripwires","doc-links"]
```

All four are mechanical. ADR-0028 accepted an unread chain *with* §4 as the
compensating control, and §4 does not exist. Every sortie merged this week
merged with no reader of any kind between the filing and `master`.

**Inheritance would hand back the authority #26 just closed.** #26 shipped on
2026-08-21: the guard refuses a filing that claims an `origin:` its session
cannot honestly claim, because on an `origin:owner` row the filing *is* the
grant. Letting a planner mark a child row `origin:owner` re-opens exactly that
hole, unless the parent link is a verified structural fact rather than a label
the filer asserts.

**The stuck-row path deadlocks as currently designed.** A planner running
unattended files its clarifying row as `origin:agent`. Under ADR-0023 an
`origin:agent` row is pickable only via `ready:auto`, granted by the owner in a
batch-approval flow that does not exist (#28). The loop would stall one step
further along than it does now, and look like progress while doing it.

## Decision

### 1. Planning goes unattended — after the reviewer gate, not before

`state:triage → state:planned` becomes a scheduled act. The owner's session is
no longer what invokes the planner.

**It ships after ADR-0028 §4's reviewer gate, not before.** The interview
considered shipping it first, and considered a narrowed first version limited to
`type:research` and `type:docs` rows. The owner rejected the narrowed version on
the ground that almost no rows would be covered by it, and chose the ordering
that builds the reader first. This is the one place in this ADR where the cheap
option was available and declined, and the reason is stated plainly: unattended
planning removes the last human from the chain, and the control that was
supposed to compensate for that was never built. Shipping the removal before the
compensation is how a single-point risk becomes an incident.

The build order is therefore: **reviewer gate → unattended planner**, and the
reviewer gate is a hard prerequisite, recorded as a blocking edge on the tracker
rather than as a sentence in a plan.

### 2. `origin:` means whose licence covers the row, not who typed it

ADR-0023 set `origin:` by which path filed the row. That reading makes the label
useless the moment a planner files anything: everything an agent creates is
`origin:agent`, including work the owner plainly licensed by filing its parent.

The distinction that survived the interview is **derivation versus discovery**:

| | What it is | `origin:` |
|---|---|---|
| **Derivation** | A child of a goal the owner filed, or a clarification of a specific row. The work was already licensed; the planner is elaborating the owner's filing. | inherits the parent's |
| **Discovery** | Something noticed mid-sortie, traceable to no filed row — *"the copy mentions frames but that is not an option"*. Nobody licensed this. | `origin:agent` |

Discovery is what ADR-0023 reserved batch approval for, and it stays there. The
label is non-empty on both sides, which the pure-authorship reading could not
manage.

### 3. Inheritance is derived from a structural link, never claimed

A filer may not assert inheritance. `origin:` is derived from a **native
sub-issue link to a row that is itself `origin:owner`** — a fact on the tracker,
not prose in a body and not a label an unattended session chose.

Between filing and linking, a row carries **`origin:pending`**. It satisfies
`validate.require_on_open`, it is not `origin:owner`, and it is therefore not
auto-eligible by `eligible()`'s second route. A freshly filed child being briefly
un-pickable is correct: the loop reads state on a schedule and will see it on the
next pass.

ADR-0023 said `origin:` is "never inferred after the fact". That is amended to
what it meant: never inferred **from prose, from the author, or from anything an
agent wrote**. A verified parent link is none of those.

### 4. An epic keeps its interview; what happens under it does not

ADR-0017 routes `type:epic` to *"Mission - interview before any issue is written"*,
and `qops brief`'s `routing()` says so on every session. That rule stands, and
the owner chose it explicitly over filing an epic in one line:

> a — interview then decompose

An epic is where direction that only the owner holds gets set — *"a new project
that sells CV templates on Etsy, from an empty repo"* is not a row, it is a
brief. So the interview survives at the top, and **decomposition below it becomes
automatic**: the epic's children are derived, and by §2 they inherit the licence
the interview granted.

This is the shape the owner described: he sets the goal or milestone, files
smaller items when he has them, and the steps to reach the goal — including
issues found on the way — arrive without him.

### 5. A stuck planner marks the row and files a clarification

When the planner cannot plan a row — underspecified, oversized (ADR-0027), or
actually a taste row — it does not guess and does not retry. It marks the row and
files a `type:research` clarification row against it. That row may itself carry a
taste gate if the clarification genuinely needs the owner's preference.

The clarification is a **derivation** under §2, so it inherits the licence and an
investigation agent can pick it up. That is what makes this path terminate rather
than deadlock.

### 6. The floor when nothing unattended can clear it is a label and a digest line

If the investigation cannot clear the row either, the row gets `no-auto`, a label,
and a line in the digest. The owner sees it next time he looks. No push, no
interruption.

**Remote Control is an upgrade, not a dependency.** `.claude/agents/interactor.md`
already routes owner approvals through it and cites **ADR-0002** — which is not in
this repo and has no record in `docs/constraints/` either. The design may not
depend on a decision this repo cannot see. When Remote Control is ported, the
escalation becomes a message; until then it is a line the owner reads.

### 7. The planner sees how its previous plans fared

Considered and declined: a hard stop on the planner after n struck-out rows. #49
already stops a *row* after three failed sorties, and a second circuit breaker on
top of it would mostly fire on the first one's failures.

What is missing instead is feedback. A planner that plans badly but plausibly
produces rows that fail in the coder, three times each, and learns nothing. So
the planner is given the outcomes of the rows it planned — which struck out and
why — as input. It is a weaker control than a stop and a more useful one: a stop
only prevents, and this can correct.

### 8. One answer for every repo, for now

Not a config key. The owner chose a single answer for the substrate and for
consuming projects, with a revisit later. The contract is frozen during a
consumer's first week (`docs/reference/qops-contract.md`), and inventing a
per-repo key on the eve of a migration is exactly the change that rule exists to
prevent. If printshop turns out to want a different answer, that is a schema
change collected during its first week and applied after it.

## Consequences

- **#46 was load-bearing on this, and the owner declined to make it hold.**
  The filing bar cannot detect a mislabelled taste row, because the planner's
  writing satisfies the bar it is checked against. Unattended planning makes that
  writing arrive faster and more often. This ADR first recorded #46 as a
  prerequisite for §1 being honest; on 2026-08-21, presented with four shapes and
  the measurement, the owner chose the fourth — **accept the rate**. See
  "The rate, accepted" below. §1's prerequisite is therefore ADR-0028 §4's
  reviewer alone, and the ordering in §1 stands unchanged.
- ADR-0028's single-point risk — *"everything rests on the filing bar"* — is
  partly relieved by building §4's reviewer, and partly worsened by §1. The net
  is deliberately not claimed to be an improvement; it is a trade taken with the
  reader built first.
- `labels.origin` gains `pending`. That is a taxonomy change, and it lands before
  a consumer migration rather than during one, on purpose.
- The loop's idle condition becomes meaningful. Today an idle queue means nothing
  was planned. After this it means every row is blocked behind something a human
  must do — which is the state the owner asked for.

## The rate, accepted

The S2 measurement: 33 rows, blind, a sonnet triager against the 2026-08-20
re-triage as ground truth. **Two rows mislabelled, both `taste` → `machine`** —
the direction that ships a decision in the owner's name. 92% correct on the rows
it answered, and no error in the safe direction.

#46 offered four shapes for closing that. Two were verified during the #46
decision session and are recorded here because a future session will otherwise
re-derive them:

- **Reading the filing through the API is possible but wrong.** GraphQL
  `userContentEdits` does return prior bodies. But every edit is authored by the
  owner's login — agents use his token — so "the last human revision" is not
  implementable, and the shape collapses to "the first revision", which would
  permanently fail a one-line row the owner later elaborates himself. ADR-0028
  protects exactly that filing.
- **The reviewer does not cover the gap.** #80 judges a diff against the row's
  *stated outcome*. A taste row carrying machine-shaped criteria yields a diff
  that serves them, and the check goes green; it never sees that the criteria
  should not have been machine-authored. #80's scope excludes judging labels on
  purpose. So "let the reviewer carry the semantic half" costs a wider #80, not
  a cheaper #46.

The owner chose to accept the rate rather than pay for either. That is ADR-0020's
standing bet — the merge is reversible, `master` is protected, every step is on
the tracker — and it is recorded here with the objection ADR-0025 already made
against it: **reversibility has never covered a decision taken in the owner's
name.** A mislabelled taste row is that kind of decision, and two of them are
expected per hundred.

What this buys: #82 stops waiting on a row nobody wants to build, and the design
ships with one prerequisite instead of two. What it costs is stated above rather
than discovered later. The `revisit-after` on this ADR is the check.

## The risk this design carries

**Two removals and one addition, and the addition is an LLM.** §1 removes the
session that invoked the planner; ADR-0028 §4 adds a reviewer whose verdict
blocks. The chain's only semantic reader will be a model, judged by no test, and
its fail-open case (auth, rate limit, timeout) is a green check by design.

That is accepted for the same reason ADR-0020 accepted unread merges: the merge
is reversible, `master` is protected, and every step is on the tracker. It is
recorded here rather than left implied, and `revisit-after` is short.
