from collections.abc import Iterator

import httpx
import pytest
from clinic_api.chaos import ChaosConfig
from clinic_api.clock import FixedClock
from clinic_api.main import create_app as create_clinic_app
from fastapi.testclient import TestClient
from openfrontdesk.api.app import create_app
from openfrontdesk.backends.clinic_http import ClinicHttpBackend
from openfrontdesk.llm.scripted import ScriptedLLM
from openfrontdesk.settings import Settings
from support import NOW, PROFILE, Harness, HarnessFactory


@pytest.fixture
def make_harness(monkeypatch: pytest.MonkeyPatch) -> Iterator[HarnessFactory]:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    clients: list[TestClient] = []

    def make(
        *, chaos: ChaosConfig | None = None, max_steps: int = 8, turn_timeout_s: float = 45.0
    ) -> Harness:
        clinic = create_clinic_app(FixedClock(NOW), chaos=chaos or ChaosConfig(), filler=False)
        backend = ClinicHttpBackend(
            httpx.AsyncClient(transport=httpx.ASGITransport(app=clinic), base_url="http://clinic"),
            backoff_s=0,
        )
        llm, judge = ScriptedLLM(), ScriptedLLM()
        settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            database_url="sqlite+aiosqlite:///:memory:",
            business_profile=PROFILE,
            max_agent_steps=max_steps,
            turn_timeout_s=turn_timeout_s,
        )
        app = create_app(settings, llm=llm, judge_llm=judge, backend=backend, clock=lambda: NOW)
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        return Harness(client=client, llm=llm, judge=judge, clinic=clinic)

    yield make
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def harness(make_harness: HarnessFactory) -> Harness:
    return make_harness()
