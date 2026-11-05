"""Which models the engine runs on when the configuration doesn't say."""

import pytest
from openfrontdesk.settings import Settings


@pytest.fixture(autouse=True)
def no_keys_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "OFD_OPENAI_API_KEY",
        "OFD_ANTHROPIC_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    for name in ("OFD_LLM_MODEL", "OFD_JUDGE_MODEL"):
        monkeypatch.delenv(name, raising=False)


def settings(**values: str) -> Settings:
    return Settings(_env_file=None, **values)  # type: ignore[call-arg, arg-type]


def test_with_an_anthropic_key_both_run_on_haiku() -> None:
    configured = settings(anthropic_api_key="test-key", openai_api_key="test-key")
    assert (configured.llm_model, configured.judge_model) == (
        "claude-haiku-5-5",
        "claude-haiku-5-5",
    )


def test_with_only_an_openai_key_both_run_on_gpt_5_mini() -> None:
    configured = settings(openai_api_key="test-key")
    assert (configured.llm_model, configured.judge_model) == ("gpt-5-mini", "gpt-5-mini")


def test_a_configured_model_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OFD_LLM_MODEL", "claude-sonnet-5-5")
    configured = settings(anthropic_api_key="test-key")
    assert configured.llm_model == "claude-sonnet-5-5"
    assert configured.judge_model == "claude-haiku-5-5"
