# ADR 0003: Idempotency keys derived from the conversation

## Context

A booking request can reach the booking system and succeed while the response is lost
(timeout, dropped connection). Retrying blindly books twice. Not retrying leaves the agent
unsure whether the appointment exists.

## Decision

Every write carries an `Idempotency-Key`. The executor derives it from:

- the conversation id,
- the tool name,
- the validated arguments (canonical JSON),
- the number of times this exact action already **succeeded** in the conversation.

So a retry after an error or timeout reuses the key and the booking system returns the original
result, while repeating an action that already succeeded (book, cancel, then book the same slot
again) is treated as a new action with a new key.

The HTTP adapter only retries requests that are safe to repeat: reads, and writes that carry a
key. A write without a key (registering a customer) is never retried automatically.

## Consequences

- The booking system must support idempotency keys. The mock does (and rejects a reused key
  with a different body); real adapters without it need a lookup-before-retry strategy.
- Keys are deterministic, which makes the behavior testable: see
  `test_retried_booking_is_deduplicated_by_the_clinic`.
