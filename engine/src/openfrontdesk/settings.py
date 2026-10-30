from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OFD_", env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./openfrontdesk.db"
    business_profile: Path = Path("businesses/brightsmile-dental/profile.yaml")

    # 127.0.0.1 rather than localhost: on some systems localhost tries IPv6 first and stalls.
    backend_url: str = "http://127.0.0.1:8001"
    backend_timeout_s: float = 10.0

    openai_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("OFD_OPENAI_API_KEY", "OPENAI_API_KEY")
    )
    anthropic_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("OFD_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
    )
    # A model named "claude-..." runs on Anthropic; any other on OpenAI.
    llm_model: str = "gpt-5-mini"
    judge_model: str = "gpt-5-mini"
    # For reasoning models (gpt-5 family). Lower is faster; unset it for models without it.
    llm_reasoning_effort: Literal["minimal", "low", "medium", "high"] | None = "low"
    judge_reasoning_effort: Literal["minimal", "low", "medium", "high"] | None = "low"
    max_agent_steps: int = 8
    tool_timeout_s: float = 20.0
    # A customer waiting longer than this gets a handoff instead of a reply.
    turn_timeout_s: float = 45.0

    # Integrations that open conversations on a verified channel (WhatsApp, SMS, voice) send
    # this in X-Integration-Key. Unset, any client can: fine for a local demo only.
    integration_key: SecretStr | None = None

    otlp_endpoint: str | None = None
    cors_origins: list[str] = ["http://localhost:3000"]
