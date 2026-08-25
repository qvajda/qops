---
status: accepted
revisit-after: 2026-11-01
amends: 0016
---

# A green `gate:machine` PR merges itself

**Date:** 2026-08-15 · **Session:** Phase 7 · **Amends:** ADR-0016 and
`docs/reference/loops.md` · **Origin:** owner sign-off item 8 (owner-initiated,
not proposed).

## Context

ADR-0016 set `required_approving_review_count: 0` because GitHub does not let a
PR's author approve it and this repo has one maintainer — the count made
`master` permanently unmergeable. It concluded that the taste gate is *a merge,
not a review object*: the owner reads the PR and clicks merge.

That last click is the remaining piece of hand-sequencing. On a `gate:machine`
PR there is nothing for a human to judge — the gate has judged it — so the click
is a mindless approval button, and **a mindless approval button is not a
control.** ADR-0017 already says `gate:machine` work must not reach the owner
before review; leaving the merge manual reintroduces the contact ADR-0017
removed, one step later.

## Decision

**A PR merges automatically when all of these hold:**

| Condition | Why |
|---|---|
| `gate` green | the machine gate has judged it |
| `guard` green — `tripwires` and `doc-links` | the constraints hold |
| `test` green | required check (ADR-0016) |
| label `gate:machine` | `gate:taste` is a human judgement and never auto-merges |
| branch matches `<type>/<issue#>-<slug>` | ADR-0019's convention; an unrouted branch is not autonomous work |
| no `no-auto` label | the standing per-issue veto, already in the taxonomy |

Squash merge, delete the branch. **Owner review is reserved for `gate:taste`.**

**Amended 2026-08-19 (qops#3), and it corrects this ADR's mechanism rather than
its decision.** Every sentence above rests on "the merge is gated on *checks*,
which is precisely what branch protection was configured to trust". That is true
only where required status checks exist. The extracted substrate's second-ever
PR was merged by this workflow **ten seconds before its own gate finished**, in a
repo whose protection had not yet been configured: with no required checks a PR
is mergeable the instant it opens, and `gh pr merge --auto` merges rather than
queues. The workflow documented as "does NOT merge" merged.

So the proviso is now enforced instead of assumed. The `enable` job uses the
`enablePullRequestAutoMerge` mutation, which *fails* when there is nothing to
queue behind, and treats that failure as a stop naming branch protection as the
cause. A consumer who installs qops and does not finish protection now gets a red
job saying so, rather than silent auto-merge of everything `gate:machine`.

Branch deletion moved with it: `--delete-branch` was a flag on the old call, and
is now the repo's `delete_branch_on_merge` setting — an owner setting, like the
rest of them.

**Amended 2026-08-16, during implementation (#119).** The labels are read from
the **linked issue**, not from the pull request. The first cut read
`github.event.pull_request.labels` and was structurally incapable of firing:
nothing labels a PR, `gh pr create` inherits nothing from the issue, and issues
are the source of truth (CLAUDE.md). The issue number comes from the branch, by
the same rule `qops brief` uses.

**Consequence, and it tightens the ADR rather than loosening it:**
`no-issue/<slug>` has no linked issue, so it has no gate to read and **never
auto-merges**. The recorded escape hatch stays a human decision, which is the
right answer for a branch that opted out of routing.

## Consequences

**ADR-0016's consequence paragraph is amended, not contradicted.** Its reasoning
— that a gate which can approve itself is not a gate — stands untouched: nothing
here approves anything. `required_approving_review_count` stays 0,
`enforce_admins` stays true, the required checks are unchanged. The merge is
gated on *checks*, which is precisely what branch protection was configured to
trust.

**`docs/reference/loops.md` must be updated in the same change.** `pickup-loop`
is documented as *"branch + commit + PR; **never merge**"*, and line 63's
acceptance check is "it stops there". Under this ADR a `gate:machine` pickup may
merge. The two documents disagreeing is worse than either rule — the loop table
is what a future session reads to know what an unattended agent may do.

**The line an unattended agent still may not cross is unchanged and is the
important one:** it never activates a listing (a standing owner decision), never
merges a `gate:taste` PR, and never merges anything the gate has not passed.

**Failure mode this creates:** a defect that the machine gate cannot see now
reaches `master` without a human ever having read the diff. That is accepted —
it is the same exposure the gate already carried on every PR the owner merged
without reading, made honest. The mitigation is the gate's coverage, so a defect
that lands this way is a missing check, and the fix is the check, not the
restoration of the click.
