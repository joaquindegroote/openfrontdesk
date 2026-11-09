# ADR 0004: Our own agent loop, not a framework (yet)

## Context

The engine needs tool calling on two providers, every write checked before it runs, a step
budget and a turn deadline, a handoff that gives the conversation to staff, and a record of
every tool call. Agent frameworks such as LangGraph offer graphs of steps, checkpointed state
and pauses that wait for a person.

## Decision

One loop per customer turn (`agent/loop.py`, with the executor in `agent/executor.py`, about 375
lines together): call the model with the tools, run what it asks for through the executor
(validation, guardrails, timeout, idempotency key), give it the results, and stop on a reply, a
handoff, the step budget or the deadline. Between turns, the state lives in the database: the
messages, the tool events and the conversation's status. Nothing is held in the loop.

Why not a framework now:

- A turn is short and linear: read, propose, check, act, reply. There's no graph to draw.
- What matters most here is what a framework doesn't bring: guards that fail closed, idempotency
  keys, handoff texts from the business profile. They would be custom nodes anyway.
- Each provider's quirks (Claude's thinking blocks replayed within a turn, refusals) live in one
  small client per provider, tested with scripted models.
- When something fails, the stack trace is in our code and the span is in Phoenix.

## When to revisit

Phase 4 brings flows that last days: a reminder the day before, waiting for a deposit, a staff
member answering hours later. Those need state that survives restarts, and pauses that resume
on an outside event. There are two ways to get that:

- **A graph framework.** LangGraph's checkpointer and interrupts are made for it: the flow is
  a graph, and a paused run resumes where it stopped.
- **A queue with the state in the database.** Each flow is a row with a status and a time it's
  due. A worker picks up what's due, and the model only decides the next step, as it does in a
  turn today. Everything that's waiting is a row that can be listed, retried and fixed by
  hand, and the model never owns the schedule.

The second is the one I've seen hold up in production for flows that wait for days, and it's
close to what's already here: the database holds the conversation, the tool events and the
idempotency keys. So the plan is to build the first flow, the reminder, on a queue, keep the
turn loop as it is, and bring in LangGraph only if the flows grow branches that a status
column can't describe. The same evals decide.

## Consequences

- We own and test the loop: no framework upgrades to follow, and nothing hidden between the
  model and the booking system.
- Multi-day flows will need a scheduler and a small state machine of our own before any
  framework is tried.
