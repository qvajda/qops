---
status: accepted
revisit-after: 2026-11-01
---

# `master` is protected with required checks and no required approvals

**Date:** 2026-08-14 · **Session:** E15, Phase 4 item 8 · **Closes:** review
finding B8.

## Context

Finding B8: branch protection without "do not allow bypassing" is decorative,
because an agent holding an admin token walks through any rule. So protection
was applied with `enforce_admins: true` and four required status checks
(`test`, `gate`, `tripwires`, `doc-links`), `strict`, no force-push, no
deletion.

It was also applied with `required_approving_review_count: 1`, and **that was
wrong.** GitHub does not let a pull request's author approve it, and this repo
has exactly one maintainer. With `enforce_admins: true` there is no bypass
either. The combination made `master` **permanently unmergeable** — caught by
the owner the first time he tried, not by the session that configured it.

## Decision

**Required approving reviews: 0. Everything else stands.**

| Setting | Value | Why |
|---|---|---|
| `required_pull_request_reviews` | present, count **0** | its *presence* is what forces merges through a PR; the count was the deadlock |
| `required_status_checks` | `test`, `gate`, `tripwires`, `doc-links`, `strict` | the machine gate, which is the half B8 is actually about |
| `enforce_admins` | **true** | the "do not allow bypassing" half. The owner is bound too |
| force-push / deletion | disabled | — |

`hot-path-cap` is deliberately **not** required: `groom.yml` only runs on
`CLAUDE.md` changes, and a required check that never runs blocks every PR
forever — the same failure class as the approval count, avoided by accident
rather than by foresight.

## Consequences

**The taste gate is a merge, not a review object.** It always was: the owner
reads the PR and clicks merge. Nothing merges automatically, and `pickup-loop`
is defined to stop at "request review" and never merge
(`docs/reference/loops.md`), so an unattended agent cannot cross the line an
approval count was imagined to hold.

**The alternative was refused.** Letting `QOPS_AGENT_TOKEN` approve would be a
bot rubber-stamping its owner, and it would hand unattended agents a route to
self-approval — the one thing branch protection exists to prevent. A gate that
can approve itself is not a gate (CONTEXT.md).

**If a second maintainer ever joins, raise the count to 1** and delete this
paragraph. The reasoning above is entirely about a single-maintainer repo.

**Companion token, measured rather than trusted.** `QOPS_AGENT_TOKEN` is a
fine-grained PAT scoped to this repo with `Contents: write`,
`Pull requests: write`, `Issues: write` and no administration. Verified in
Actions by *behaviour* — it created and deleted a ref, opened and closed an
issue, and was refused on an admin-only endpoint. Note that
`GET /repos/{repo}` reports `"admin": true` for it: that is the **owning user's
repo role, not the token's grants**, and reading it would have concluded the
token was over-broad. The behavioural probe is the evidence. GL-22a's rule —
verify by measurement, not by status field — applied to GitHub rather than to
Gelato.
