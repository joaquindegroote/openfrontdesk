"""Provider-neutral chat types. Adapters translate these to each provider's wire format."""

from typing import Any, Literal, Self

from pydantic import BaseModel, Field

Role = Literal["system", "user", "assistant", "tool"]


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ChatMessage(BaseModel):
    role: Role
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None
    # The provider's own content blocks for an assistant message, replayed on the next step of
    # the same turn (Claude's thinking blocks). Never serialized, so never stored.
    provider_content: list[dict[str, Any]] | None = Field(default=None, exclude=True)

    @classmethod
    def system(cls, content: str) -> Self:
        return cls(role="system", content=content)

    @classmethod
    def user(cls, content: str) -> Self:
        return cls(role="user", content=content)

    @classmethod
    def assistant(cls, content: str) -> Self:
        return cls(role="assistant", content=content)


class ToolSpec(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]


class Usage(BaseModel):
    """Tokens that one or more model calls used. Input counts everything that was sent. The
    cache fields say how much of it the provider read from its prompt cache and how much it
    wrote there, which it bills at other rates."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
        )


class Completion(BaseModel):
    text: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    model: str | None = None
    provider_content: list[dict[str, Any]] | None = None
