---
date: 2026-07-16
event: Platform migration scoping
attendees: marcus@northstar.example, tomas@northstar.example, ana@northstar.example
---

# Platform migration scoping

## Scope agreed

In scope: the ingestion pipeline, the event store, and the fan-out workers.
Out of scope for this phase: the reporting stack and anything customer-facing.

## Cutover approach

Dual-write for a minimum of two weeks, with reads still served from the legacy
store. Ana raised that our TTL is seven days, so a two-week dual-write window
covers a full expiry cycle with margin. Agreed to make that the rollback
criterion: if we have not seen a clean cycle, we do not cut over.

## Risks raised

- **Tomas:** the legacy store has no schema enforcement, so we will discover
  malformed records during the migration rather than before it. Mitigation is a
  dry-run validation pass first.
- **Ana:** on-call load during the parallel run. We have not decided who carries
  the pager for the new stack during the overlap. Still open.

## Owner

Marcus owns the cutover runbook. Kickoff scheduled for the following week.
