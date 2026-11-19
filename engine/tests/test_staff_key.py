"""The inbox's endpoints read every customer's conversation and act as the front desk, so they
need the staff key. A channel only starts conversations and sends the customer's messages."""

import pytest
from openfrontdesk.llm.scripted import reply
from support import CARLOS, STAFF_KEY, Harness, HarnessFactory


def staff_calls(harness: Harness, conversation: str, headers: dict) -> list[int]:
    """Status codes of the five staff endpoints, in the order the inbox uses them."""
    url = f"/v1/conversations/{conversation}"
    client = harness.client
    return [
        client.get("/v1/conversations", headers=headers).status_code,
        client.get(url, headers=headers).status_code,
        client.post(f"{url}/takeover", json={}, headers=headers).status_code,
        client.post(f"{url}/staff-messages", json={"text": "Hi"}, headers=headers).status_code,
        client.post(f"{url}/resume", headers=headers).status_code,
    ]


@pytest.mark.parametrize(
    "authorization",
    [
        "",
        "Bearer wrong-key",
        f"Basic {STAFF_KEY}",
        f"Bearer {STAFF_KEY.upper()}",
        # Outside ASCII, which a plain string comparison would fail on with an error.
        "Bearer clé".encode(),
    ],
)
def test_the_staff_endpoints_need_the_key(harness: Harness, authorization: str | bytes) -> None:
    conversation = harness.start(CARLOS)

    assert staff_calls(harness, conversation, {"Authorization": authorization}) == [401] * 5
    refused = harness.client.get("/v1/conversations", headers={"Authorization": authorization})
    assert refused.headers["WWW-Authenticate"] == "Bearer"


def test_with_the_key_they_work(harness: Harness) -> None:
    conversation = harness.start(CARLOS)
    # The harness's client sends the key, as the console's server does.
    assert staff_calls(harness, conversation, {}) == [200, 200, 200, 201, 200]


def test_a_channel_needs_no_key(harness: Harness) -> None:
    no_key = {"Authorization": ""}
    harness.llm.extend([reply("Hi Carlos! How can I help?")])

    started = harness.client.post("/v1/conversations", json={}, headers=no_key)
    conversation = started.json()["id"]
    answered = harness.client.post(
        f"/v1/conversations/{conversation}/messages", json={"text": "Hi"}, headers=no_key
    )

    assert harness.client.get("/v1/business", headers=no_key).status_code == 200
    assert (started.status_code, answered.status_code) == (201, 200)
    assert answered.json()["messages"][-1]["text"] == "Hi Carlos! How can I help?"


def test_without_a_staff_key_the_staff_endpoints_are_off(make_harness: HarnessFactory) -> None:
    harness = make_harness(staff_key=None)
    conversation = harness.start(CARLOS)

    assert staff_calls(harness, conversation, {"Authorization": "Bearer anything"}) == [503] * 5
    detail = harness.client.get("/v1/conversations").json()["detail"]
    assert "OFD_STAFF_KEY" in detail


def test_the_api_docs_say_which_endpoints_need_it(harness: Harness) -> None:
    paths = harness.client.get("/openapi.json").json()["paths"]
    needs_key = {
        (path, method)
        for path, methods in paths.items()
        for method, operation in methods.items()
        if operation.get("security")
    }
    assert needs_key == {
        ("/v1/conversations", "get"),
        ("/v1/guardrails", "get"),
        ("/v1/conversations/{conversation_id}", "get"),
        ("/v1/conversations/{conversation_id}/staff-messages", "post"),
        ("/v1/conversations/{conversation_id}/takeover", "post"),
        ("/v1/conversations/{conversation_id}/resume", "post"),
    }
