"""What a turn cost: the tokens of every model call it made, by model, with what the provider
read from and wrote to its prompt cache."""

import os
from pathlib import Path

import pytest
from openai.types import CompletionUsage
from openfrontdesk.llm import Completion, Usage
from openfrontdesk.llm.openai_client import _usage
from openfrontdesk.llm.scripted import reply, tool_call
from openfrontdesk.storage.db import Database
from openfrontdesk.storage.models import Base
from sqlalchemy import Connection, inspect, text
from support import CAMILA, Harness, verdict


def used(completion: Completion, model: str, **tokens: int) -> Completion:
    return completion.model_copy(update={"model": model, "usage": Usage(**tokens)})


def test_a_turn_keeps_what_every_model_call_used(harness: Harness) -> None:
    conversation = harness.start(CAMILA)
    harness.llm.extend(
        [reply("To confirm: a check-up ($95) on Tue Nov 3 at 9:00 AM with Dr. Fuentes. Book it?")]
    )
    harness.say(conversation, "A check-up tomorrow at 9:00 with Dr. Fuentes, please")

    booking = tool_call(
        "book_appointment",
        customer_id="pat_0003",
        service_id="checkup",
        start="2026-11-03T09:00:00-03:00",
        practitioner_id="dr-fuentes",
    )
    harness.llm.extend(
        [
            used(
                booking, "assistant", input_tokens=5000, output_tokens=40, cache_write_tokens=4800
            ),
            used(
                reply("Booked!"),
                "assistant",
                input_tokens=5200,
                output_tokens=30,
                cache_read_tokens=4800,
            ),
        ]
    )
    harness.judge.extend([used(verdict(True, "Yes"), "judge", input_tokens=900, output_tokens=25)])
    turn = harness.say(conversation, "Yes, book it")["turn"]

    assert turn["usage"] == {
        "assistant": {
            "input_tokens": 10200,
            "output_tokens": 70,
            "cache_read_tokens": 4800,
            "cache_write_tokens": 4800,
        },
        # The judge's call was never counted before.
        "judge": {
            "input_tokens": 900,
            "output_tokens": 25,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        },
    }
    assert (turn["input_tokens"], turn["output_tokens"]) == (11100, 95)


def test_a_turn_that_fails_still_counts_what_it_used(harness: Harness) -> None:
    conversation = harness.start(CAMILA)
    # The script ends after this step, so the model call that follows it fails.
    harness.llm.extend(
        [used(tool_call("find_customer", phone=CAMILA), "assistant", input_tokens=5000)]
    )
    turn = harness.say(conversation, "Hi")["turn"]

    assert turn["outcome"] == "failed"
    assert turn["usage"]["assistant"]["input_tokens"] == 5000


def test_openai_reports_what_it_read_from_its_cache() -> None:
    usage = CompletionUsage.model_validate(
        {
            "prompt_tokens": 6000,
            "completion_tokens": 80,
            "total_tokens": 6080,
            "prompt_tokens_details": {"cached_tokens": 5120},
        }
    )

    assert _usage(usage) == Usage(input_tokens=6000, output_tokens=80, cache_read_tokens=5120)
    assert _usage(None) == Usage()


def _columns(connection: Connection) -> list[str]:
    return [column["name"] for column in inspect(connection).get_columns("turns")]


@pytest.mark.parametrize("kind", ["sqlite", "postgres"])
async def test_an_older_database_gets_the_columns_added_since(kind: str, tmp_path: Path) -> None:
    url = f"sqlite+aiosqlite:///{(tmp_path / 'engine.db').as_posix()}"
    if kind == "postgres":
        url = os.environ.get("OFD_TEST_POSTGRES_URL") or pytest.skip("needs OFD_TEST_POSTGRES_URL")
    db = Database(url)
    try:
        async with db.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            await connection.execute(text("ALTER TABLE turns DROP COLUMN usage"))

        await db.create_all()
        await db.create_all()

        async with db.engine.connect() as connection:
            assert "usage" in await connection.run_sync(_columns)
    finally:
        await db.dispose()
