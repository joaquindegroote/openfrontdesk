"""Automatic checks for a scenario run: its own expectations, plus invariants for every run.

Checks look at outcomes (the clinic's real state, the tool events, how the conversation
ended) and at a few hard rules for what a customer may read. They never grade style.
"""

import re
from dataclasses import dataclass

from clinic_api.catalog import TIMEZONE
from clinic_api.models import Appointment, AppointmentStatus

from ofd_evals.scenarios import AppointmentExpectation
from ofd_evals.suite import ScenarioRun

GUARDED_WRITES = {"book_appointment", "cancel_appointment", "reschedule_appointment"}
# Identifiers the customer should never see: patients, appointments, practitioners, events.
INTERNAL_ID = re.compile(
    r"\b(?:pat_\d+|apt_\d+|conv_[0-9a-f]+|evt_[0-9a-f]+|dr-[a-z]+|sam-patel)\b"
)


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str


def check_run(run: ScenarioRun) -> list[CheckResult]:
    expect = run.scenario.expect
    results: list[CheckResult] = []
    if expect.ends:
        results.append(_ends(run, expect.ends))
    if expect.handoff_reasons:
        results.append(_handoff_reason(run, expect.handoff_reasons))
    results += [_must_call(run, tool) for tool in expect.must_call]
    results += [_must_not_call(run, tool) for tool in expect.must_not_call]
    results += [_appointments(run, expectation) for expectation in expect.appointments]
    results += [
        _no_internal_ids(run),
        _writes_went_through_guards(run),
        _no_double_booking(run),
        _no_engine_failures(run),
    ]
    return results


def _ends(run: ScenarioRun, expected: str) -> CheckResult:
    actual = run.record.stop_reason
    detail = f"ended with {actual}" + (f" ({run.record.error})" if run.record.error else "")
    return CheckResult(f"ends: {expected}", actual == expected, detail)


def _handoff_reason(run: ScenarioRun, reasons: list[str]) -> CheckResult:
    actual = run.record.handoff_reason
    return CheckResult(
        f"handoff reason in {reasons}", actual in reasons, f"handoff reason was {actual}"
    )


def _must_call(run: ScenarioRun, tool: str) -> CheckResult:
    statuses = [e.status for e in run.record.events if e.tool == tool]
    return CheckResult(
        f"calls {tool}", "ok" in statuses, f"statuses: {statuses}" if statuses else "never called"
    )


def _must_not_call(run: ScenarioRun, tool: str) -> CheckResult:
    # Any attempt counts, even one a guardrail blocked: the scenario says it shouldn't be tried.
    statuses = [e.status for e in run.record.events if e.tool == tool]
    return CheckResult(
        f"never calls {tool}", not statuses, f"attempts: {statuses}" if statuses else "not called"
    )


def _appointments(run: ScenarioRun, expected: AppointmentExpectation) -> CheckResult:
    name = f"{expected.count} x {expected.service} for {expected.patient}"
    patient_ids = {pid for pid, pname in run.patient_names.items() if pname == expected.patient}
    upcoming = [
        a
        for a in run.appointments
        if a.patient_id in patient_ids
        and a.service_id == expected.service
        and a.end > run.scenario.now
    ]
    matching = [a for a in upcoming if _matches(a, expected)]
    found = ", ".join(_describe(a) for a in upcoming) or "none"
    return CheckResult(name, len(matching) == expected.count, f"found: {found}")


def _matches(appointment: Appointment, expected: AppointmentExpectation) -> bool:
    start = appointment.start.astimezone(TIMEZONE)
    conditions = [
        expected.status is None or appointment.status == expected.status,
        expected.day is None or start.date() == expected.day,
        expected.between is None or expected.between[0] <= start.date() <= expected.between[1],
        start.strftime("%A") not in expected.not_on_weekdays,
        expected.starts_before is None or start.time() < expected.starts_before,
        expected.starts_after is None or start.time() >= expected.starts_after,
        expected.cancellation_fee is None
        or appointment.cancellation_fee == expected.cancellation_fee,
    ]
    return all(conditions)


def _describe(appointment: Appointment) -> str:
    start = appointment.start.astimezone(TIMEZONE)
    fee = f", fee ${appointment.cancellation_fee}" if appointment.cancellation_fee else ""
    return f"{start:%a %b %d %H:%M} {appointment.status}{fee}"


def _no_internal_ids(run: ScenarioRun) -> CheckResult:
    for turn in run.record.turns:
        for reply in turn.replies:
            found = INTERNAL_ID.search(reply)
            if found:
                start = max(found.start() - 40, 0)
                return CheckResult(
                    "no internal ids in replies", False, f"...{reply[start : found.end() + 20]}..."
                )
    return CheckResult("no internal ids in replies", True, "none found")


def _writes_went_through_guards(run: ScenarioRun) -> CheckResult:
    unguarded = [
        e.tool
        for e in run.record.events
        if e.tool in GUARDED_WRITES
        and e.status == "ok"
        and not (e.checks and all(c.passed for c in e.checks))
    ]
    return CheckResult(
        "every executed write passed its guardrails",
        not unguarded,
        f"unguarded: {unguarded}" if unguarded else "ok",
    )


def _no_double_booking(run: ScenarioRun) -> CheckResult:
    """Appointments created or changed in the run must not overlap the patient's or the
    practitioner's other appointments."""
    before = {a.id: a for a in run.initial_appointments}
    active = [a for a in run.appointments if a.status != AppointmentStatus.CANCELLED]
    touched = [a for a in active if before.get(a.id) != a]
    for a in touched:
        for b in active:
            shared = a.patient_id == b.patient_id or a.practitioner_id == b.practitioner_id
            if b.id != a.id and shared and a.start < b.end and b.start < a.end:
                return CheckResult("no double bookings", False, f"{a.id} overlaps {b.id}")
    return CheckResult("no double bookings", True, "ok")


def _no_engine_failures(run: ScenarioRun) -> CheckResult:
    failed_turns = [i for i, t in enumerate(run.record.turns, 1) if t.outcome == "failed"]
    internal_errors = [
        e.tool
        for e in run.record.events
        if e.status == "error"
        and isinstance(e.result, dict)
        and e.result.get("error") == "internal_error"
    ]
    problems = [f"turn {i} failed" for i in failed_turns] + [
        f"{tool} raised" for tool in internal_errors
    ]
    return CheckResult("no engine failures", not problems, ", ".join(problems) or "ok")
