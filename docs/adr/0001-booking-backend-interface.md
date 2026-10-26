# ADR 0001: The agent talks to a BookingBackend, not to a specific system

## Context

Every appointment-based business already has a system of record: a practice-management
system, a salon booking tool, Cal.com, a Google Calendar. An assistant that only works with
one of them, or that keeps its own copy of the calendar, is either useless or wrong.

## Decision

The engine depends on a small `BookingBackend` protocol (`engine/src/openfrontdesk/backends/booking.py`):
services, slots, customers, appointments, cancellation quotes, and the three writes
(book, cancel, reschedule). Tools only use this protocol. Each system gets an adapter.

The reference adapter targets `clinic-api`, a mock practice-management system that enforces
real rules (opening hours, minimum notice, double-booking, late fees, deposits) and can inject
latency and failures.

Availability, prices and fees always come from the backend. The business profile only holds
what a front-desk coordinator knows by heart: hours, policies in words, tone, FAQ and when to
escalate.

## Consequences

- Adding a real system means writing one adapter, without touching the agent.
- The mock is a separate service on purpose: the agent is developed and tested against HTTP,
  timeouts and 5xx responses, not against an in-memory object.
- The protocol has to stay small. Anything system-specific (insurance checks, resources,
  rooms) belongs in additional tools, not in the core protocol.
