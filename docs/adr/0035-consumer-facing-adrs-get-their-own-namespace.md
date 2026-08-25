---
status: accepted
revisit-after: 2027-02-01
---

# Consumer-facing ADRs get their own namespace, `CADR-NNNN`

#181, verified live in printshop (#105): a rendered workflow or native skill
cites a qops ADR by bare number (`ADR-0024`, `ADR-0016`, …), but nothing
copies the cited decision into a consumer's tree and nothing checks the
citation resolves there. Two failure modes, not one — a citation to a number
printshop never had (dead), and a citation to a number printshop *does*
have, for its own pre-split `ADR-0023`, a different decision entirely
(silently wrong).

## Decision

A citation meant to survive installation — anything a rendered `.github/
workflows/*` or `.claude/skills/*/SKILL.md` may name — is numbered
`CADR-NNNN`, never bare `ADR-NNNN`. The two prefixes can never collide by
construction, so a consumer's own `docs/adr/000N-*.md` numbering is untouched
and untouchable by qops's growth.

The `CADR-` files themselves are package data (`qops/templates/adr/*.md`,
`pyproject.toml`'s `package-data`) and `qops install`/`qops init` copy them
verbatim into the consumer's `docs/adr/consumer/` — `install.render_adr_consumer()`.
That is the fix for "nothing copies the cited ADR into a consumer's tree":
the citation now names a file that exists in the tree that reads it, not
just in qops's own.

`qops doctor` gained `install.broken_adr_citations()`: it scans a consumer's
*rendered* workflows and skill bodies (not `qops/templates/` — the template
source always resolves against `templates/adr/` in the same checkout) for
`CADR-NNNN` and fails if `docs/adr/consumer/CADR-NNNN-*.md` is missing. A
missing file is the only failure mode left once the split holds: collision
with a consumer's own ADR is now structurally impossible, not merely
checked-for.

## The first mapping

qops's own `docs/adr/*` were reclassified once, in issue order, by which
number a template already cited (`grep -rhoE 'ADR-[0-9]{4}' qops/templates/`):

| old (qops `docs/adr/`)                                              | new (`CADR-`) |
|-----------------------------------------------------------------------|---------------|
| 0001 hook-spike                                                        | CADR-0001 |
| 0009 local-desktop-cron-host                                           | CADR-0002 |
| 0016 branch-protection-without-an-approval-count                       | CADR-0003 |
| 0018 qops-native-skills-sized-by-the-substrate                         | CADR-0004 |
| 0019 enforcement-hooks-block-branchless-edits-and-record-unfinished-work | CADR-0005 |
| 0020 auto-merge-green-machine-gated-prs                                | CADR-0006 |
| 0023 ready-auto-grant-splits-by-issue-provenance                       | CADR-0007 |
| 0024 a-rendered-workflow-must-run-in-a-repo-shaped-unlike-the-renderer  | CADR-0008 |
| 0025 a-gate-machine-close-is-not-a-judgement-either                    | CADR-0009 |
| 0027 one-row-is-one-sortie                                             | CADR-0010 |
| 0028 the-filing-is-the-licence                                        | CADR-0011 |
| 0029 the-loop-plans-what-the-owner-licensed                            | CADR-0012 |
| 0032 the-pickup-task-is-installed-and-named-per-project                | CADR-0013 |

The originals stay exactly where they are, at their original numbers, under
qops's own `docs/adr/` — this repo's own development history is not a
consumer-facing citation and the split does not touch it. `CADR-` is a
second, parallel numbering of the *subset* a template cites, not a rename of
the source. A future ADR that a template comes to cite gets the next
`CADR-` number and a new file under `templates/adr/`; the source ADR keeps
whatever number it already has in `docs/adr/`.

## What this deliberately leaves alone

Cross-references *inside* an ADR's own body (an ADR citing another ADR in
its prose) still use the bare, pre-split numbers — rewriting those is
touching a decision's own content, which is exactly the line "must not
touch: any project-specific ADR content" draws. A reader of a `CADR-` file
who follows one of those inner citations back into `docs/adr/` is reading
qops's own development history, correctly: that was never the citation this
issue was about.

Printshop's own `ADR-0023` — the reason this split exists — is never
renumbered, never migrated, and never touched. That was the entire point.
