---
status: accepted
revisit-after: 2026-10-01
---

# The pipeline's cron runs on the local desktop, with a pre-committed fork

Scheduled tasks run on the owner's Windows desktop rather than on an always-on
host. Chosen for cost (zero) and for credential locality — the pipeline's Etsy,
Gelato and Replicate keys never leave the machine.

**The fork was named before it was needed, not after:** if the desktop fails on
wake/sleep or reliability, move to a cheap always-on host identified in advance.
Signed off 2026-08-05 on that basis; the fallback stays live rather than closed
out.

**Short `revisit-after` on purpose.** This is the ADR most likely to be wrong: it
is the only one whose failure mode is silent (a machine asleep produces no error,
just no run).
