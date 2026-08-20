# The `gate:taste` audit — measurement, re-triage, and what #25 still needs

Evidence behind `docs/adr/0026-gate-taste-is-a-preference-that-is-an-input.md`.
Corpus: `qvajda/qops` (26 rows) and `qvajda/qhoto_printshop` (127 rows), read
2026-08-20.

## 1 — Was the owner's read ever load-bearing?

Every **resolved** `gate:taste` row in both trackers, N = 14. One question per
row: did an owner action change the outcome, or did the owner transcribe a
conclusion something else had already reached? Merges, closes and label moves
count as transcription unless the owner's read altered what shipped.

| # | Row | Resolution | Verdict |
|---|---|---|---|
| 1 | ps#49 | migrated to qops#17 | transcription |
| 2 | ps#114 | migrated to qops#14 | transcription |
| 3 | ps#123 | migrated to qops#13 | transcription |
| 4 | ps#124 | migrated to qops#12 | transcription |
| 5 | ps#126 | migrated to qops#11 | transcription |
| 6 | ps#137 | migrated to qops#10 | transcription |
| 7 | ps#152 | migrated to qops#9 | transcription |
| 8 | ps#176 | migrated to qops#7 | transcription |
| 9 | ps#177 | migrated to qops#6 | transcription |
| 10 | ps#139 Telegram ack listener | criterion written in the body before the work; owner tapped a live button, log read | transcription — owner as instrument, not judge |
| 11 | ps#3 → qops#3 automerge fails closed | mechanical fix, merged as specified | transcription |
| 12 | qops#34 ADR-0025 | closing comment: "nothing left for a mechanism to derive here" | transcription |
| 13 | **ps#151** "requests review" clause | three shapes offered, owner picked a fourth | **outcome changed** |
| 14 | **ps#112** Phase 7 sign-off | item 7 DECLINED; items 8–10 owner-initiated additions | **outcome changed** |

**2 / 14.** Not zero. The two exceptions are what the predicate is built from —
in both, the row's *deliverable was the owner's preference*, an input the work
could not proceed without. In ps#139 — one of the owner's own seven moments —
the criterion **was** stateable in advance, which is what falsifies the
"couldn't be written down beforehand" hypothesis as the separator.

### Supporting figures

| Figure | Value |
|---|---|
| `gate:taste` rows ever | 47 |
| …closed | 14 (30%) |
| …closed for reasons other than a repo migration | **5 (11%)** |
| `gate:machine` rows ever | 43 |
| …closed | **24 (56%)** |
| Open `gate:taste` printshop rows whose own body says `gate: none — defined when the sortie is planned` | **14 of 22** |
| Migrated rows that arrived in `qvajda/qops` still carrying `gate:taste` | 9 of 9 |
| Open `gate:machine` rows that are `type:code` | 19 of 19 — `gate:machine` + `type:manual` never once occurred |

The GL-1…GL-75 era (ps#17–#102) predates the gate taxonomy entirely and carries
`gate:none`; 62 closed rows sit outside this corpus for that reason. That is
also where five of the owner's seven taste moments happened, so **the taxonomy
has never once been asked to label the work it was designed around.**

## 2 — Re-triage of the live backlog

All 33 open `gate:taste` rows, under ADR-0026's predicate: *is the row's
deliverable itself a choice only the owner can make?*

### Stays `gate:taste` — 9

| Row | Why the deliverable is a preference |
|---|---|
| qops#9 | the choice of where an unattended sortie runs; tradeoffs only the owner weighs |
| qops#17 | GL-24, the qops overhaul — the owner's design of his own way of working |
| qops#28 | epic; its routing round is a decision, and an epic is not a sortie |
| ps#53 | SynthID disclosure — a legal/brand judgement, R3's own example, correctly applied |
| ps#69 | the About section's media — the owner authors the brand images |
| ps#132 | QA of art in non-botanical niches; acceptance *is* the owner's eye (his moment 2) |
| ps#134 | routing decision by construction — `type:decision` |
| ps#154 | picking 3 art pieces for the banner is the owner's eye; **oversized, split it** |
| ps#156 | its body says the output is a go/no-go on a niche |

### Moves to `gate:machine` — 24

| Row | Was parked because | Under the predicate |
|---|---|---|
| qops#6 | `git -C` guard defect | stated criterion, testable |
| qops#7 | registration is a machine fact | the check that registration matches config is a test |
| qops#10 | research | a finding, not a preference |
| qops#11 | research | a finding |
| qops#12 | research + code, `no-auto` already on | `no-auto` stays and carries the authority |
| qops#13 | research | a finding |
| qops#14 | research | a finding |
| qops#19 | ADR-0024 reasoned it taste because no template reaches per-machine state | that is verification reach; `doctor` can warn, the remedy is `type:manual` |
| ps#28 | copy-template build | **the spec is already written** — criterion stateable |
| ps#31 | needs a GCP project stood up | **authority → add `no-auto`** |
| ps#40 | poll relaxation | "verify first; latency win only" — stated |
| ps#51 | compositor refinement | named technical defects (grey band, `flat_leaning`); **oversized, split the open-ended half** |
| ps#55 | programmatic activation, parked | reopen triggers are observable facts; **`no-auto` already on and is the real control** |
| ps#67 | research | a finding |
| ps#68 | research | a finding |
| ps#76 | three dry-run stub candidates | mechanical repair; **authority → add `no-auto`** (it publishes) |
| ps#91 | dashboard thumbnail anomaly | an investigation with a finding |
| ps#92 | "parked by the owner" | parking is **state and priority, not a gate**; the finish line if worked is a diagnosis |
| ps#93 | two stranded designs | "current standards" are written down; **authority → add `no-auto`** (it publishes) |
| ps#94 | owner *proposal* for a refresh path | a build with a stated ask |
| ps#131 | research | a finding |
| ps#133 | craft-literature research | a finding |
| ps#155 | Etsy title-length nudge | investigate, then change the prompt — stated |
| ps#157 | candidate 49 title defect | root cause + fix |

**Delta: 24 of 33 (73%) move to `gate:machine`. Nothing moves the other way** —
all 19 open `gate:machine` rows survive the predicate unchanged, so it is
one-directional on this backlog.

### The five that surprised me

1. **ps#92** — the gate was carrying *"the owner parked this"*. Parking is
   priority, and priority has never been a gate. The label was being used as a
   snooze button, which is the clearest single instance of the overload.
2. **ps#31, #55, #76, #93** — four rows whose owner-need is **authority**
   (stand up a cloud project, activate programmatically, publish a listing),
   never judgement. Three of the four had **no `no-auto`**: the control that
   should have held them was absent, and `gate:taste` was standing in for it
   badly. Moving the gate without adding the flag would *lose* a control, which
   is why the flag is part of the same edit.
3. **qops#19** — ADR-0024 argued it to `gate:taste` explicitly and by name. A
   correctly reasoned ADR reached the wrong label because the taxonomy gave it
   only one place to put "a machine cannot see this."
4. **ps#28** — parked as taste while **its own spec document already existed**.
   The criterion was written before the row was labelled.
5. **ps#132 is the only open row in either tracker matching the owner's moment
   2** (art quality). One row out of 47 ever carried the thing the label was
   invented for.

### Not applied

The labels are **not written** by this session. The predicate is not law until
this ADR merges, and re-labelling `qvajda/qhoto_printshop` from here would edit
another project's tracker on a rule its owner has not yet accepted (#6 is open
against `git -C <other-repo>` for the adjacent reason). The table above is the
delta and is mechanically applicable in one pass once the ADR lands.

## 3 — What #25 still needs, scoped and not built

#25 asks for steps 2–4 (label, plan, grant) to stop being manual. ADR-0023 took
step 4. ADR-0026 makes step 2's hardest column decidable without the owner.
What remains is a build, and it needs a PRD and sign-off — **not taken here.**

| Piece | Shape | Size | Blocked on |
|---|---|---|---|
| **A. Apply the re-triage** | one pass over the table in §2, `gate:` plus four `no-auto` adds | one sortie, mechanical | this ADR merging |
| **B. `triager` proposes `gate:` and `type:`** | the agent exists and is forbidden every label. Under ADR-0026 the gate is decidable from the row alone, so it can *apply* `gate:` and `type:` and still never touch `ready:auto` or `no-auto` | one sortie + a test that it cannot write the two owner labels | A |
| **C. `no-auto` becomes triager-proposable, never triager-applied** | authority is decidable from the row (the act's surface is named) but the flag is a control; propose in a comment, owner grants in batch, same shape ADR-0023 gave `origin:agent` | one sortie | B, #26 (`origin:`) |
| **D. `planner` drafts the plan into the row** | #25's own "real lever" — turns writing into reading. Leaves the row at `state:triage`; never `state:planned` | needs a PRD: cost per row, and what a bad draft costs to unpick | B |
| **E. R8 mechanised** | #27 already tracks it (a test *proves* it, red-before/green-after, not merely named) | — | already filed |

**The boundary this session stops at:** A is queued behind a merge, B–D are
builds. Nothing above adds a label, a config key or a CLI verb — the contract
stays frozen, which was the constraint.
