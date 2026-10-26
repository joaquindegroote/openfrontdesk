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
   pass `fee_acknowledged=true`.
2. **Judge** (an LLM call, runs only if every rule passed). Given a one-line summary of the
   action and the recent transcript, it decides whether the customer explicitly agreed to *this
   exact* action after its details (date, time, price, fee) were stated.

If any check fails, the action is not executed and the model receives a `blocked` result with
the reason and what to do next. Every decision, passed or failed, is stored with the tool event.

The judge fails closed: an exception or an unparseable answer blocks the action.

## Consequences

- Wrong writes need two independent failures (the agent and the judge) instead of one.
- One extra model call per write. Reads are never judged, and rules filter out most bad
  proposals before the judge runs.
- The judge can be wrong in the other direction (blocking a real confirmation). The cost is one
  extra "Shall I book it?" message, which is acceptable. The planned eval suite will measure
  how often it happens.
- Guards are attached per tool, so new write tools must declare theirs explicitly.
