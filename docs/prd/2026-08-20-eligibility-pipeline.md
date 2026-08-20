# PRD — the eligibility pipeline

**Closes #25.** Scope: `qvajda/qops` only. Decisions behind it: ADR-0026 (the
gate answers judgement), ADR-0027 (one row is one sortie), ADR-0028 (the filing
is the licence). Evidence: `docs/2026-08-20-gate-audit.md`.

## The goal, in the owner's words

> My primary goal at the moment is to have qops develop itself automatically
> with minimal intervention from me.

And the constraint that shapes every choice below:

> My role is to set the direction, not define how to get there.

## What the chain becomes

| # | Step | Today | After |
|---|---|---|---|
| 1 | Row is filed, stating a goal | owner | **owner — the only remaining act** |
| 2 | `type:` / `gate:` / `state:` applied | owner | triager (ADR-0026 made `gate:` decidable) |
| 3 | Plan written onto the row, `state:planned` | owner or a session, ad hoc | planner, as machine input |
| 4 | `ready:auto` granted | owner | mechanical on `origin:owner` (ADR-0023) |
| 5 | Branch, commit, PR, gate, merge, close | automated | unchanged |

Step 1 is the licence. Everything after it derives. That is the whole PRD, and
ADR-0028 is where the case for it is argued and its single-point risk is named.

## Prerequisites — already filed, now blocking rather than following

| Row | Why it became a prerequisite |
|---|---|
| **#26** — `origin:` label at filing, `doctor` asserts presence | step 4 is mechanical *only* on `origin:owner`. Without the label there is no path to distinguish, and every row would take the `origin:agent` route |
| **#27** — R8 from "names a test" to "the test proves it" | with no grant-time check left, R8 is the only thing standing between a plan and a merge. A named-but-vacuous test is a green gate over nothing |

Neither is new work discovered here; both were filed out of ADR-0023 and are
open, `gate:machine`, in this tracker.

## The build — five sorties

Each is one row under ADR-0027, each names the test that proves it.

### S1 — The filing bar

A row may not leave `state:triage` unless its body states an outcome that can
become acceptance criteria. `qops doctor` reports a row that does not; the
triager refuses to label it.

- **Gate:** machine. **Type:** code.
- **Acceptance:** `doctor` exits 1 naming the row; a row with a stated outcome
  passes; the check runs against the live tracker the same way the open-issue
  invariants already do.
- **Test:** `test_doctor_refuses_a_row_with_no_stated_outcome`, plus a fixture
  pair — one barren body, one that states an outcome — so the discrimination is
  executed and not pattern-matched (ADR-0024's lesson).
- **Why first:** it is the control that replaces the three ADR-0028 removes.
  Nothing downstream may ship before it.

### S2 — The triager applies `gate:` and `type:`

Today it is forbidden every label. Under ADR-0026 the gate is decidable from the
row alone, so it applies `gate:` and `type:`, and never `ready:auto` or
`no-auto`. It refuses — and reports — an ambiguous row, an oversized row
(ADR-0027) and a row below the filing bar (S1).

- **Gate:** machine. **Type:** code.
- **Model:** `agents.triager.model` haiku → **sonnet**, for the gate column
  only in intent but the whole role in practice. Owner decision, taken in the
  interview. Costs rate, not money: `claude -p` bills against whatever the CLI
  is authenticated with, and ADR-0009's "cost (zero)" was about hosting.
- **Amends, and it must be in the same change or S2 does not ship:**
  `docs/reference/loops.md` states triage-loop "**warns and does not label**" as
  a *deliberate limit*, and `.claude/agents/triager.md` says a guessed label is
  worse than a gap. The first is now false; the second stays true and is why the
  three refusals exist.
- **Test:** `test_the_triager_may_write_the_gate_and_never_the_grant` —
  asserts `ready:auto` and `no-auto` are unreachable from the role, which is
  the ADR-0023 boundary held in code rather than in prose.

### S3 — The planner writes the plan onto the row

The plan lands on the issue and sets `state:planned`. It is machine input: a
spec a coder executes and a test checks, not a one-page ask. An oversized row
reported by S2 is split into children here.

- **Gate:** machine. **Type:** code.
- **Test:** `test_a_plan_is_machine_input_and_an_ask_is_one_page` — two shapes,
  asserted separately, so the ask format cannot leak back into plans and cannot
  be dropped from `type:decision` rows.

### S4 — The reviewer gate

A rendered workflow comparing the PR's diff to the row's stated goal. Verdict
blocks (ADR-0028 §4).

- **Gate:** machine. **Type:** code.
- **Fail behaviour:** verdict against → red. Could-not-run → **green**, saying
  so in the check output. An LLM that did not run is not a rejection, and under
  `enforce_admins: true` a flaky refusal makes `master` unmergeable for the
  owner too.
- **Owner precondition:** adding it to the branch's **required status checks**
  is a repo setting, denied to every agent by `.claude/settings.json` on
  purpose. It joins contract preconditions 6 and 7 — a one-time decision, not
  recurring toil.
- **Test:** `test_the_reviewer_check_fails_open_on_infrastructure_and_closed_on_a_verdict`,
  executed against both exit paths.

### S5 — Enable the schedule

`pickup-loop`'s task is registered and disabled and has never run on its own
cron. Turning it on is the owner's act and the point of everything above.

- **Gate:** taste. **Type:** manual. It is a decision, and its acceptance is a
  judgement about whether he trusts the chain — the correct residue.
- **Blocked on:** #9 (it branches under a live session) and #7 / #12 (the
  registration is a machine fact). Named here so S5 is not mistaken for a
  formality.

## Sequence

```
#26, #27  ->  S1  ->  S2  ->  S3  ->  S4  ->  (#9, #7, #12)  ->  S5
```

S1 gates everything: it is the replacement control. S4 may be built in parallel
with S3 but must not be made *required* until S3 exists, since it reads a goal
the planner is responsible for carrying into the PR.

## Out of scope, stated so it is not quietly added

- **`qhoto_printshop`.** It inherits by pinning a later qops. Its rows touch
  listings, money and a live storefront, and six of the nine surviving
  `gate:taste` rows are its. Nothing here runs against it.
- **A per-day sortie cap.** Declined in ADR-0028 §5: hourly is the bound, and a
  cap would need a config key against a frozen contract.
- **Removing the branch-implies-the-row inference.** Declined in ADR-0027;
  ADR-0027 makes it sound rather than removing it.
- **Any config-schema or CLI-contract change.** One value changes
  (`agents.triager.model`) and one optional key gains a value (`prd`). No new
  key, no new label, no new verb.

## What has to be true for this to be wrong

Named per the interview's third round, and each is the thing to watch rather
than a thing already handled:

1. **The filing bar is lenient**, so a one-line row licences the chain. This is
   ADR-0028's single-point risk and the reason its `revisit-after` is short.
2. **The bar is strict**, so the owner pays at filing time — the one place he
   said he wants to spend less. S1's acceptance should be measured against real
   filings, not fixtures, before S2 ships.
3. **The reviewer is wrong and blocks good work**, and with `enforce_admins`
   there is no override but turning the check off. Fail-open covers the
   infrastructure half; a wrong *verdict* is a missing check, and the fix is the
   check.
4. **A row grows after it is labelled.** Nothing re-checks a passed row
   (ADR-0027's accepted failure mode).

## First measurement to take, before S2

Run the triager over the 24 rows the 2026-08-20 re-triage decided by hand and
count the disagreements. The re-triage was opus at high effort with ADR-0026 in
context; S2 is sonnet at low. That number belongs in S2's issue before S2 is
planned, and it is cheap: the ground truth already exists in
`docs/2026-08-20-gate-audit.md` §2.
