from datetime import datetime

import pytest
from clinic_api.catalog import TIMEZONE
from clinic_api.chaos import ChaosConfig
from clinic_api.clock import FixedClock
from clinic_api.main import create_app
from fastapi.testclient import TestClient

# Monday 8:00, before opening.
NOW = datetime(2026, 11, 2, 8, 0, tzinfo=TIMEZONE)


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(FixedClock(NOW), filler=False))


def at(day: int, hour: int, minute: int = 0) -> str:
    return datetime(2026, 11, day, hour, minute, tzinfo=TIMEZONE).isoformat()


def patient_id(client: TestClient, phone: str) -> str:
    return client.get("/patients", params={"phone": phone}).json()[0]["id"]


def first_appointment(client: TestClient, phone: str) -> dict:
    return client.get(f"/patients/{patient_id(client, phone)}/appointments").json()[0]


def starts(slots: list[dict]) -> set[str]:
    return {s["start"] for s in slots}


def test_lists_services_with_prices(client: TestClient) -> None:
    services = {s["id"]: s for s in client.get("/services").json()}
    assert services["whitening"]["deposit"] == 100
    assert services["cleaning"]["duration_minutes"] == 45


def test_availability_respects_notice_and_opening_hours(client: TestClient) -> None:
    slots = client.get(
        "/availability",
        params={
            "service_id": "checkup",
            "date_from": "2026-11-02",
            "date_to": "2026-11-02",
            "practitioner_id": "dr-chen",
        },
    ).json()
    found = starts(slots)
    assert slots[0]["start"] == at(2, 10)  # two hours' notice from 8:00
    assert at(2, 12, 30) in found  # ends exactly at the lunch break
    assert at(2, 12, 45) not in found  # would run into lunch
    assert at(2, 14) in found


def test_availability_skips_booked_slots(client: TestClient) -> None:
    # Emily's seeded cleaning is Monday 11:00-11:45 with the hygienist.
    slots = client.get(
        "/availability",
        params={"service_id": "cleaning", "date_from": "2026-11-02", "date_to": "2026-11-02"},
    ).json()
    found = starts(slots)
    assert at(2, 10, 15) in found
    assert at(2, 10, 30) not in found
    assert at(2, 11, 45) in found


def test_booking_is_idempotent_and_prevents_double_booking(client: TestClient) -> None:
    body = {
        "patient_id": patient_id(client, "+15550100103"),
        "service_id": "checkup",
        "start": at(3, 10),
        "practitioner_id": "dr-ortega",
    }
    first = client.post("/appointments", json=body, headers={"Idempotency-Key": "k1"})
    replay = client.post("/appointments", json=body, headers={"Idempotency-Key": "k1"})
    assert first.status_code == 201
    assert first.headers["Idempotent-Replayed"] == "false"
    assert replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json()["id"] == first.json()["id"]

    other = {**body, "patient_id": patient_id(client, "+15550100102")}
    conflict = client.post("/appointments", json=other)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "slot_unavailable"


def test_reusing_an_idempotency_key_for_another_request_is_rejected(client: TestClient) -> None:
    body = {
        "patient_id": patient_id(client, "+15550100103"),
        "service_id": "checkup",
        "start": at(3, 10),
    }
    client.post("/appointments", json=body, headers={"Idempotency-Key": "k2"})
    response = client.post(
        "/appointments", json={**body, "start": at(3, 11)}, headers={"Idempotency-Key": "k2"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "idempotency_key_reused"


def test_late_cancellation_charges_a_fee(client: TestClient) -> None:
    appointment = first_appointment(client, "+15550100101")
    quote = client.get(f"/appointments/{appointment['id']}/cancellation-quote").json()
    assert quote == {
        "appointment_id": appointment["id"],
        "fee": 50,
        "late": True,
        "free_cancellation_until": at(1, 11),
    }

    result = client.post(f"/appointments/{appointment['id']}/cancel", json={"reason": "sick"})
    assert result.json()["fee_charged"] == 50
    assert result.json()["appointment"]["status"] == "cancelled"

    again = client.post(f"/appointments/{appointment['id']}/cancel", json={})
    assert again.json()["error"]["code"] == "already_cancelled"


def test_cancellation_is_free_more_than_24_hours_ahead(client: TestClient) -> None:
    appointment = first_appointment(client, "+15550100102")
    quote = client.get(f"/appointments/{appointment['id']}/cancellation-quote").json()
    assert quote["fee"] == 0
    assert quote["late"] is False


def test_reschedule_rules(client: TestClient) -> None:
    late = first_appointment(client, "+15550100101")
    response = client.post(f"/appointments/{late['id']}/reschedule", json={"start": at(4, 10)})
    assert response.json()["error"]["code"] == "late_change_not_allowed"

    early = first_appointment(client, "+15550100102")
    moved = client.post(f"/appointments/{early['id']}/reschedule", json={"start": at(3, 15)})
    assert moved.status_code == 200
    assert moved.json()["start"] == at(3, 15)
    assert moved.json()["practitioner_id"] == early["practitioner_id"]


def test_rejects_times_outside_opening_hours(client: TestClient) -> None:
    response = client.post(
        "/appointments",
        json={
            "patient_id": patient_id(client, "+15550100103"),
            "service_id": "checkup",
            "start": at(8, 10),  # Sunday
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "outside_opening_hours"


def test_whitening_requires_a_deposit(client: TestClient) -> None:
    booked = client.post(
        "/appointments",
        json={
            "patient_id": patient_id(client, "+15550100103"),
            "service_id": "whitening",
            "start": at(4, 9),
        },
    ).json()
    assert booked["status"] == "pending_deposit"
    assert booked["payment_url"].endswith(booked["id"])

    paid = client.post(f"/appointments/{booked['id']}/deposit-payment").json()
    assert paid["status"] == "confirmed"


def test_new_patients_are_registered_with_normalized_phones(client: TestClient) -> None:
    created = client.post("/patients", json={"name": "Ana Lopez", "phone": "(555) 010-0199"})
    assert created.status_code == 201
    assert created.json()["phone"] == "+15550100199"
    duplicate = client.post("/patients", json={"name": "Ana L.", "phone": "+1 555 010 0199"})
    assert duplicate.json()["error"]["code"] == "patient_exists"


def test_chaos_can_be_forced_per_request() -> None:
    client = TestClient(create_app(FixedClock(NOW), chaos=ChaosConfig(), filler=False))
    assert client.get("/services", headers={"X-Chaos-Fail": "1"}).status_code == 503
    assert client.get("/healthz", headers={"X-Chaos-Fail": "1"}).status_code == 200


def test_seeded_calendar_is_busy_but_bookable() -> None:
    client = TestClient(create_app(FixedClock(NOW)))
    slots = client.get(
        "/availability",
        params={
            "service_id": "checkup",
            "date_from": "2026-11-03",
            "date_to": "2026-11-03",
            "limit": 500,
        },
    ).json()
    # Two dentists with 30 possible check-up starts each: a busy day leaves some but not all free.
    assert 0 < len(slots) < 60


def test_phone_verification_codes() -> None:
    client = TestClient(create_app(FixedClock(NOW), filler=False, verification_code="246810"))
    phone = "+15550100103"
    assert client.post("/verifications", json={"phone": phone}).status_code == 202

    def check(code: str) -> object:
        return client.post("/verifications/check", json={"phone": phone, "code": code}).json()

    assert check("111111") == {"verified": False}
    assert check("246810") == {"verified": True}
    # Codes are single use.
    assert check("246810")["error"]["code"] == "verification_expired"


def test_too_many_wrong_codes_void_the_code() -> None:
    client = TestClient(create_app(FixedClock(NOW), filler=False, verification_code="246810"))
    phone = "+15550100103"
    client.post("/verifications", json={"phone": phone})
    for _ in range(5):
        client.post("/verifications/check", json={"phone": phone, "code": "000000"})
    voided = client.post("/verifications/check", json={"phone": phone, "code": "246810"})
    assert voided.status_code == 410
