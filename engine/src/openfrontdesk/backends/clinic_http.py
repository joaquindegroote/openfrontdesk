"""BookingBackend for the mock clinic API (clinic-api/). Patients map to customers."""

import asyncio
from datetime import date, datetime
from typing import Any, Self

import httpx

from openfrontdesk.backends.booking import (
    Appointment,
    BackendError,
    CancellationQuote,
    CancellationResult,
    Customer,
    Service,
    Slot,
)


class ClinicHttpBackend:
    def __init__(
        self, client: httpx.AsyncClient, *, retries: int = 2, backoff_s: float = 0.25
    ) -> None:
        self._client = client
        self._retries = retries
        self._backoff_s = backoff_s

    @classmethod
    def from_url(cls, base_url: str, *, timeout_s: float = 10.0) -> Self:
        return cls(httpx.AsyncClient(base_url=base_url, timeout=timeout_s))

    async def aclose(self) -> None:
        await self._client.aclose()

    async def list_services(self) -> list[Service]:
        return [Service.model_validate(s) for s in await self._request("GET", "/services")]

    async def find_slots(
        self,
        service_id: str,
        date_from: date,
        date_to: date,
        practitioner_id: str | None = None,
    ) -> list[Slot]:
        params: dict[str, Any] = {
            "service_id": service_id,
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "limit": 500,
        }
        if practitioner_id:
            params["practitioner_id"] = practitioner_id
        return [Slot.model_validate(s) for s in await self._request("GET", "/availability", params)]

    async def find_customer(self, phone: str) -> Customer | None:
        found = await self._request("GET", "/patients", {"phone": phone})
        return Customer.model_validate(found[0]) if found else None

    async def create_customer(self, name: str, phone: str, email: str | None = None) -> Customer:
        body = {"name": name, "phone": phone, "email": email}
        return Customer.model_validate(await self._request("POST", "/patients", json=body))

    async def send_verification_code(self, phone: str) -> None:
        await self._request("POST", "/verifications", json={"phone": phone})

    async def check_verification_code(self, phone: str, code: str) -> bool:
        body = {"phone": phone, "code": code}
        return bool((await self._request("POST", "/verifications/check", json=body))["verified"])

    async def list_appointments(self, customer_id: str) -> list[Appointment]:
        found = await self._request("GET", f"/patients/{customer_id}/appointments")
        return [_appointment(a) for a in found]

    async def get_appointment(self, appointment_id: str) -> Appointment:
        return _appointment(await self._request("GET", f"/appointments/{appointment_id}"))

    async def book(
        self,
        customer_id: str,
        service_id: str,
        start: datetime,
        practitioner_id: str | None = None,
        *,
        idempotency_key: str,
    ) -> Appointment:
        body = {
            "patient_id": customer_id,
            "service_id": service_id,
            "start": start.isoformat(),
            "practitioner_id": practitioner_id,
        }
        created = await self._request(
            "POST", "/appointments", json=body, idempotency_key=idempotency_key
        )
        return _appointment(created)

    async def cancellation_quote(self, appointment_id: str) -> CancellationQuote:
        quote = await self._request("GET", f"/appointments/{appointment_id}/cancellation-quote")
        return CancellationQuote.model_validate(quote)

    async def cancel(
        self, appointment_id: str, reason: str | None, *, idempotency_key: str
    ) -> CancellationResult:
        result = await self._request(
            "POST",
            f"/appointments/{appointment_id}/cancel",
            json={"reason": reason},
            idempotency_key=idempotency_key,
        )
        return CancellationResult(
            appointment=_appointment(result["appointment"]), fee_charged=result["fee_charged"]
        )

    async def reschedule(
        self,
        appointment_id: str,
        start: datetime,
        practitioner_id: str | None = None,
        *,
        idempotency_key: str,
    ) -> Appointment:
        moved = await self._request(
            "POST",
            f"/appointments/{appointment_id}/reschedule",
            json={"start": start.isoformat(), "practitioner_id": practitioner_id},
            idempotency_key=idempotency_key,
        )
        return _appointment(moved)

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        json: Any = None,
        idempotency_key: str | None = None,
    ) -> Any:
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
        # Only repeat requests that are safe to repeat: reads, and writes the server can dedupe.
        safe_to_retry = method == "GET" or idempotency_key is not None
        attempts = 1 + (self._retries if safe_to_retry else 0)
        error = BackendError("unreachable", "Could not reach the booking system.", retryable=True)
        for attempt in range(attempts):
            if attempt:
                await asyncio.sleep(self._backoff_s * 2 ** (attempt - 1))
            try:
                response = await self._client.request(
                    method, path, params=params, json=json, headers=headers
                )
            except httpx.TimeoutException:
                error = BackendError(
                    "timeout", "The booking system did not respond in time.", retryable=True
                )
                continue
            except httpx.TransportError:
                error = BackendError(
                    "unreachable", "Could not reach the booking system.", retryable=True
                )
                continue
            if response.status_code < 400:
                return response.json()
            error = _error_from(response)
            if not error.retryable:
                raise error
        raise error


def _appointment(data: dict[str, Any]) -> Appointment:
    return Appointment.model_validate({**data, "customer_id": data["patient_id"]})


def _error_from(response: httpx.Response) -> BackendError:
    try:
        body = response.json().get("error", {})
    except (ValueError, AttributeError):
        body = {}
    retryable = response.status_code >= 500 or response.status_code == 429
    return BackendError(
        body.get("code", f"http_{response.status_code}"),
        body.get("message", "The booking system returned an error."),
        status=response.status_code,
        retryable=retryable,
    )
