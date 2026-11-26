# ADR 0002: Propose, verify, execute

## Context

Booking, cancelling and rescheduling have consequences the customer pays for: a slot taken from
someone else, a late fee, a lost appointment. Prompting the model to "always confirm first"
works most of the time, which isn't enough for actions that can't be undone.

## Decision

The model can only *propose* a write. The tool executor verifies it before it reaches the
booking system:

1. **Rules** (deterministic, free, run first). Examples: the start time is in the future; a
   cancellation needs a quote from this conversation, and if the quote has a fee, the model must
   pass `fee_acknowledged=true`; booking a service the customer already has coming up is blocked
   (it's usually a change of time gone wrong) unless they want both, with `keep_existing=true`.
2. **Judge** (an LLM call, runs only if every rule passed). Given a one-line summary of the
   action and the recent transcript, it decides whether the customer explicitly agreed to *this
   exact* action after its details (date, time, price, fee) were stated.

The summary is written only after the rules pass. It reads the booking system (which service,
what time), and one of the rules is that the customer owns the appointment, so a try on someone
else's is blocked before anything about it is read. Until November 16, 2026 it was written
first, and a web visitor who got the model to try cancelling another patient's appointment
found the service and time in the blocked event that came back.

If any check fails, the action is not executed and the model receives a `blocked` result with
the reason and what to do next. Every decision, passed or failed, is stored with the tool event.

The judge fails closed: an exception or an unparseable answer blocks the action.

## Consequences

- Wrong writes need two failures (the agent and the judge) instead of one. They aren't fully
  independent: since November 2026 both run on the same model by default, and the eval results
  record one case where the judge missed what the assistant had missed.
- One extra model call per write. Reads are never judged, and rules filter out most bad
  proposals before the judge runs.
- The judge can be wrong in the other direction (blocking a real confirmation). The cost is one
  extra "Shall I book it?" message, which is acceptable. The eval suite, planned when this
  was written, now measures how often it happens:
  [the judges on their own](../eval-results.md#the-judges-on-their-own).
- Guards are attached per tool, so new write tools must declare theirs explicitly.
