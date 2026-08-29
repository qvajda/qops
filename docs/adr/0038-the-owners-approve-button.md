---
status: accepted
revisit-after: 2026-12-15
amends: 0016, 0020
depends-on: 0009, 0019, 0021, 0025
---

# The owner's approve button: the guard holds the `gh api` write, not the deny

**Date:** 2026-08-28 · **Session:** #235 · **Amends:** ADR-0016 and ADR-0020
where they rest on `.claude/settings.json` denying `gh api` writes, and the
CLAUDE.md line that records that denial as a taken decision.

**Accepted 2026-08-29**, on #253. The row was `gate:taste`: the owner read the
PR and took the move rather than keeping the deny (ADR-0036). What follows is
the decision as taken, including the measurement that killed one of the two
properties the row was filed on.

## Context

The three owner preconditions (contract items 6–8) cost a manual browser trip
per new project. Two of the three — branch protection and the two auto-merge
settings — are already fully specified: ADR-0016 records the exact table, and
nothing in it is a judgement. They are two `gh api` calls with recorded
arguments.

They stay manual because `.claude/settings.json` denies `gh api` with a write
method (six entries), and CLAUDE.md records that denial as a taken decision.
That is right about **who** may act and wrong about **how** it holds: a deny is
absolute, so it never falls through to a permission prompt. The owner, at a
keyboard, in an interactive session, entitled to make the call, has no way to
approve it. There is no button, only a refusal.

The substrate already draws the distinction the deny cannot. `launch_env()`
sets `QOPS_UNATTENDED=1`, the guard reads it (`qops/guard.py:482`), and
`check()` already refuses `dangerouslyDisableSandbox` on that basis alone
(#122): an owner at a keyboard can still make that call, a pickup-loop launch
cannot, because nobody is reading. The unattended launch is `claude -p …
--permission-mode acceptEdits` (`scripts/qops_pickup.py:1083`) — headless, and
`acceptEdits` covers file edits rather than Bash, so a prompt it cannot answer
is a refusal.

### The precondition, and what measuring it changed

The row was filed with two properties said to make the guard **stronger** than
the deny it replaces, the second flagged as unverified. It is now verified, and
it is false.

**Established (documented, `code.claude.com/docs/en/permissions`, "What runs
before you trust a folder"):** hooks in settings files are *Used* in both
untrusted situations — a folder trusted only via its parent, and `claude -p` or
the SDK in a folder never trusted, which is the pickup launch's situation. So a
PreToolUse hook fires on an untrusted workspace, and the guard can hold there.

**And the other half of the same table:** only `permissions.allow` rules and
`additionalDirectories` are gated by trust, because they *grant* capability —
"`deny` and `ask` rules aren't affected, since they only restrict." So the deny
holds on an untrusted workspace too.

That kills the row's property 2 ("it may hold where the deny does not"), which
rested on reading #19 as "every `permissions.allow`/`deny` entry is ignored".
#19's own observed message says `Ignoring 32 permissions.allow entries` — allow
only. The correction is recorded here rather than quietly dropped, because the
row said the move had to argue both properties or be a relaxation dressed up as
a move, and now it cannot.

Method: read from the published documentation. Two attempts to establish it by
measurement on this host — a nested `claude -p` into a fresh untrusted
directory carrying a probing PreToolUse hook, and reading the CLI binary's
strings — were refused by the session's own permission classifier. Recorded as
what it is: documented, not measured here.

## Decision

**The control moves from the deny list into the guard.**

1. `qops/guard.py` gains `gh_api_refusal`, in the existing chain beside
   `git_refusal`/`origin_refusal`/`role_refusal`: a `gh api` call carrying a
   write flag (`-X`, `--method`, `-f`, `--field`, `-F`, `--input`,
   `--raw-field`) is refused when `ctx["unattended"]`.
2. `qops/templates/settings.json.tmpl` drops the six `gh api` deny entries, so
   an attended call reaches the harness's own permission prompt — the approve
   button that did not exist.
3. `qops init`'s next steps print the two runnable commands carrying ADR-0016's
   table, in place of the present prose.

`gh api` bare — a GET — is untouched and stays allowlisted.

## Why this is not a relaxation

**It survives "don't ask again."** Narrowing the deny to get a prompt instead
would mean one "yes, and don't ask again" writing a permanent allow into
`settings.local.json` — saved, in the harness's own words, permanently per
repository and command, and open to every later session on that machine. A
PreToolUse hook is not consulted from the allow list: it runs on every matching
call, allowed or not, and refuses on `QOPS_UNATTENDED` regardless of what any
allow entry says.

**It reaches further than the pattern it replaces.** A deny entry matches a
command by prefix, so `something && gh api -X PUT …` was never denied. The
guard reads argv (ADR-0021), so a chained call, a `bash -c` payload and a
`--method=POST` joined form are all the same parse. `--raw-field` — a write
flag the deny list never named — is covered too.

**What is honestly given up.** The deny applied to the owner's own attended
session as well; after this, an attended write reaches a prompt instead of a
wall, which is the entire point and is a real reduction in stopping power for a
mis-aimed attended call. And the guard is not a security control — its own
docstring says so, an agent that runs with hooks disabled walks through it —
but neither is a deny entry an agent can rewrite. The half that actually holds
against an agent is unchanged and server-side: `enforce_admins: true`, plus
`QOPS_AGENT_TOKEN` carrying no administration scope, measured behaviourally in
ADR-0016.

## Consequences

- ADR-0016 and ADR-0020 no longer rest on a deny entry for the "settings are
  what the owner last set them to" assumption. They rest on the guard for the
  unattended half and on the token's missing scope for the CI half.
- Contract items 6 and 7 become two commands the owner runs and approves at a
  prompt, instead of a browser trip. Item 8 (workspace trust) is unchanged: no
  API sets it, and it stays manual.
- Not covered, deliberately: `gh repo create` and the first push (the owner's),
  the label taxonomy (already `scripts/qops_import.py --labels`), `UNWRITABLE`,
  and what a launched agent may write.

## The critic

An instruction in a prompt is a preference. In `tests/test_qops.py`:

- `test_guard_refuses_a_write_api_call_when_unattended` — each write flag, with
  `QOPS_UNATTENDED=1`, is refused by `guard.check`; the same calls with the
  variable unset are not refused; a bare `gh api repos/x` GET is refused in
  neither.
- `test_settings_no_longer_denies_what_the_guard_now_refuses` — the rendered
  settings carry no `Bash(gh api -X` deny entry, and the guard's refusal covers
  every flag the removed entries named, asserted by iterating that same list.
