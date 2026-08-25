---
status: accepted
revisit-after: 2026-11-01
---

# ADR-0001 — Hook availability: what qops may build on

**Status:** accepted · **Date:** 2026-08-13 · **Session:** E14, Phase 1 item 1
**Supersedes:** nothing. **Closes:** review finding B6 (open since 2026-07-26, the
oldest unexecuted item in the plan) and the Claude Code half of E4/B3.
**Measured on:** Claude Code `2.1.231`, Windows 10, this repo.

## Context

Four things in the qops design depend on hooks — the auto-injected session brief,
the auto-written `resume.md`, the ledger, and the local command guard. PRD v3 §7
Phase 1 makes the spike the first item and says nothing in Phase 4 is designable
until it reports. `.qops/hook-spike/` shipped 2026-07-26 and had never run.

The spike was extended beyond its original two events to answer the full
five-question matrix: do `SessionStart` / `Stop` / `PreCompact` / `PreToolUse` /
`SessionEnd` fire; can `PreToolUse` actually **block** a Bash call; is the command
string available to `PostToolUse`; does `Stop` fire per turn.

## Method

`.claude/settings.json` (local, gitignored) registers
`.qops/hook-spike/marker.py` on seven events. `marker.py` was extended to read the
hook payload from **stdin** and record `hook_event_name`, `tool_name`,
`tool_input.command`, `session_id` and the payload's key set — so the answers are
read off a log rather than inferred. A `PreToolUse` probe exits **2** for one
sentinel command string and only that string.

Three independent firings: an interactive Claude Code session (this one), a
headless `claude -p` session, and a `--resume` of that headless session. Blocking
was verified **twice** — once against this session's own Bash call, once from the
headless session. Evidence: `.qops/hook-spike/fired.log`. The log keeps growing while the spike is
armed, so the counts below are the fires that answer each question, not a running
total.

## Decision — the answers, all measured

| Question | Claude Code | Evidence |
|---|---|---|
| `SessionStart` fires | **YES** | 2 fires, one per session start |
| `SessionStart` re-fires on `--resume` | **YES** | resuming session `e57a138b` fired it a second time |
| `UserPromptSubmit` fires | **YES** | 2 fires, one per prompt |
| `PreToolUse` fires | **YES** | fires on every Bash call, `matcher: "Bash"` honoured |
| `PostToolUse` fires | **YES** | fires on every Bash call that was not blocked |
| `Stop` fires **per turn** | **YES — per turn, not per session** | one session, two turns, **2 `Stop` fires** |
| `SessionEnd` fires | **YES** | 2 fires |
| `PreCompact` fires | **UNTESTED** | no compaction occurred; forcing one costs a full window. The hook stays registered, so the first natural compaction answers it for free |
| **`PreToolUse` can block a Bash call** | **YES, hard** | exit 2 blocked the call in both sessions. The blocked call logged `PreToolUse` and **no `PostToolUse`**, and the agent was told it was blocked |
| **command string available to `PostToolUse`** | **YES** | `tool_input.command` present, plus `tool_response` and `duration_ms` |

**Payload, `PreToolUse`:** `cwd`, `effort`, `hook_event_name`, `permission_mode`,
`prompt_id`, `session_id`, `tool_input`, `tool_name`, `tool_use_id`,
`transcript_path`. `PostToolUse` adds `tool_response` and `duration_ms`.

**Two fields the design did not know it had, and both are load-bearing.**
`transcript_path` means a hook can read the session's own JSONL — which is exactly
the instrument Phase −1 used by hand, so **`qops metrics` can compute S1 from a
hook rather than from a scan**. `effort` and `permission_mode` are visible to every
hook, so the guard can condition on them.

**One finding nobody asked for:** the hooks took effect **mid-session, with no
restart** — `.claude/settings.json` was created while this session was running and
the very next Bash call was intercepted. Convenient, and also a caveat: a hook edit
is live immediately, so a bad guard breaks the session that wrote it.

**Cowork: not yet answered.** It needs a Cowork session opened on this folder, and
no such session ran during E14. **The spike is left armed rather than cleaned up** —
`.claude/settings.json` and `marker.py` stay in place, so the first Cowork session
records itself and the row is filled with `marker.py --report`. Cost of leaving it:
one Python process per event, milliseconds, in a gitignored file.

## Consequences

1. **Build §3.1 as designed for Claude Code.** Brief on `SessionStart`, resume on
   `Stop` **or** `SessionEnd` — `Stop` is per-turn, so resume-writing belongs on
   `SessionEnd`, and `Stop` is the right place for per-turn ledger appends. The
   README's fallback ("move resume-writing to `SessionEnd`") is adopted **not** as a
   fallback but as the correct reading of a per-turn `Stop`.
2. **The guard is real, not decorative.** `PreToolUse` exit 2 blocks. It remains a
   local convenience on top of server-side branch protection (PRD §5 B8) — an agent
   can run with hooks disabled, so the guard is not a security control.
3. **`qops metrics` reads `transcript_path`**, not a directory scan.
4. **B3 stays deferred for Cowork only**, and is now bounded to one unknown rather
   than five.
5. **PRD §5 A4 gets sharper.** `.gitignore:43` ignores `.claude/` wholesale, so the
   hook config that makes all of this work **cannot be committed today**. Portability
   (Phase 7) depends on narrowing it to `.claude/*` + `!.claude/settings.json` —
   a directory-style ignore cannot be re-included by a negation, so the two-line
   form is required. Scheduled for Phase 3/4; flagged here because everything above
   is otherwise true of one laptop only.

## Reproduction

```bash
python .qops/hook-spike/marker.py --reset
claude -p "<prompt>" --model claude-haiku-4-5-20251001 --allowedTools Bash
claude -p "Say OK and nothing else." --model ... --resume <session-id>
python .qops/hook-spike/marker.py --report
```
