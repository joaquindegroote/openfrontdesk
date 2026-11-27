from pathlib import Path

import pytest
from openfrontdesk.settings import Settings

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """The reference clinic, with no .env file and no API key: only scripted models answer."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        business_profile=ROOT / "businesses" / "clinica-los-canelos" / "profile.yaml",
    )
