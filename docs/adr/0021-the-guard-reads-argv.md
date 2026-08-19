---
status: accepted
revisit-after: 2026-12-01
---

# The guard reads argv, not the command string

**Date:** 2026-08-19 · **Session:** Phase 8, P8.1 · **Closes:** #168 ·
**Amends:** ADR-0001's local half.

## Context

`qops guard` matched regexes against the raw Bash command string. Two
consequences, opposite in direction and the same in cause.

**It read past the parse.** The push target was "the last `\S+` after `git
push`, unless it starts with `-`, in which case fall back to the checked-out
branch." Every one of these writes a protected branch and none was caught:

| command | old reading |
|---|---|
| `git push origin :master` | target `:master` — not `master`, allowed |
| `git push --delete origin master` | target `origin`, allowed |
| `git push origin HEAD:master` | target `HEAD:master`, allowed |
| `git push --quiet origin master` | target `origin`, allowed |
| `git -c core.pager=cat push --force origin x` | `\bgit\s+push\b` needs adjacency — no match at all, so the force check never ran |

**It read prose as a command.** `_FORCE` matched anywhere in the string, so
`gh issue comment --body "never force-push"` was refused. The tripwire scan four
lines below has had an exemption for exactly this since it was written; the git
checks did not. The available workaround, `--body-file`, is a path the guard
cannot see into at all — the escape hatch was strictly worse than the rule.

Both halves matter more in a public repo than here: the extracted guard is the
reference implementation consumer #2 copies, and a control a caller routes
around is a speed bump that reads like a control.

## Decision

**Tokenise, then decide. Three rules, in this order:**

1. **Drop the values of prose-carrying flags** (`-m`, `--message`, `--body`,
   `--title`, `--notes`, `--description`, `--reason`) before any git check. A
   comment quoting a git rule documents it; it does not break it. Long forms
   only, plus `-m`: every short form is ambiguous — `-b` is gh's body and
   `git checkout`'s branch — and dropping the token after one hides a ref.
2. **Expand the values of command-carrying flags** (`-c`, `-lc`, `-ic`,
   `--command`, `/c`) recursively. `bash -c "…"` hides its payload from a token
   scan the same way `--body "…"` hid prose from a string scan; the difference
   is that this one runs.
3. **Parse the subcommand, not a substring.** `git_commands()` returns every
   `git <verb>` in the token list with that verb's own arguments: the verb is
   the first non-option token after `git`, and its arguments stop at the next
   shell separator. All six checks read that one parse.
4. **Resolve every push destination**, not one token: strip flags, take the
   positionals after the remote, and read each refspec's destination (after
   `:`, minus a leading `+`, minus `refs/heads/`). No refspec means the
   checked-out branch, which is what git itself pushes; `--all` / `--mirror`
   means all of them.

**Rule 3 was added on the second pass, an hour after the first landed**, and
the way it was found is the argument for it. The first fix still scanned the
token list for the *word* `push`, so the very next command this session ran —
`git stash push -m wip -- tests/x.py && git checkout master` — was refused as a
push to master: `stash push` looked like `push`, and `master` after the `&&`
looked like its refspec. Six checks each doing their own scanning is the defect;
one parse and six decisions is the fix. Two false-positive classes close with
it: a subcommand that merely contains a blocked verb, and an argument belonging
to a later command in the same line.

`-c` is expanded only when its value contains whitespace. `bash -c "git push"`
carries a command; `git -c core.pager=cat` carries a config setting.

The alternative the issue offered — recognise `--delete` explicitly — was
refused. It fixes one row of the table above and leaves three, and a control
patched per-symptom is how the table got that long.

## Consequences

**The guard is still not a security control.** ADR-0001 stands: an agent can run
with hooks off, and branch protection is the half that cannot be routed around.
This ADR is about the local half being honest about what it reads.

**Two new fail-closed behaviours.** Unbalanced quotes fall back to a naive split
rather than to allowing the call, and a push whose remote cannot be identified
resolves to the checked-out branch.

**One deliberate loosening, and it is a correctness fix.** `git checkout -b x &&
git commit` no longer trips the protected-branch rule: the command makes its own
branch before it writes. The old refusal also named the wrong verb —
`cmd.split()[1]` reported `'checkout' on master is blocked` — which is how a
correct-looking control teaches people to ignore it.

**What is still not covered, named rather than left implicit:** a command
written to a file and executed, an editor invocation, and any flag value the
list above does not know about. The list is a judgement, so it will be wrong
eventually; the test names each case it covers, and a new case is a row in that
parametrize list rather than a new regex.
