"""Model gateway interface.

    Application -> ModelGatewayInterface -> GenAILabAdapter -> AsyncOpenAI -> GenAILab

Every LLM call in the platform goes through this interface
(ARCHITECTURE.md section 7, AI_DEVELOPMENT_RULES.md sections 4.4 and 5).
Business logic must never construct a provider SDK client directly.

Two rules shape the types below:

* Model identity is always supplied by the caller from the model registry. No
  model name, price, context length or capability is defaulted or inferred here
  (ARCHITECTURE.md section 8).
* Token usage is optional. When a provider does not return usage, the field
  stays ``None`` so the cost layer can record provenance as ESTIMATED or
  UNAVAILABLE rather than fabricating actuals
  (AI_DEVELOPMENT_RULES.md section 10, DATABASE_SCHEMA.md section 15).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class Message(BaseModel):
    """A single chat message.

    Untrusted retrieved content must be passed as ``user`` content that is
    clearly separated from system instructions, never merged into the system
    prompt (SECURITY.md section 9).
    """

    model_config = ConfigDict(frozen=True)

    role: Role
    content: str


class TokenUsage(BaseModel):
    """Reported usage. ``None`` means the provider did not report it."""

    model_config = ConfigDict(frozen=True)

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None

    @property
    def is_complete(self) -> bool:
        return self.input_tokens is not None and self.output_tokens is not None


class ModelRequest(BaseModel):
    """A request to a model, already selected by the orchestrator."""

    model_config = ConfigDict(frozen=True)

    # Required. Comes from the model registry, never from a hard-coded default.
    model: str = Field(min_length=1)
    messages: list[Message] = Field(min_length=1)

    max_output_tokens: int | None = Field(default=None, gt=0)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)

    # Structured output is preferred wherever a result is consumed
    # programmatically (AI_DEVELOPMENT_RULES.md section 37).
    response_format: Literal["text", "json_object"] = "text"

    timeout_seconds: float | None = Field(default=None, gt=0)

    # Correlation only; never used for authorization.
    request_id: str | None = None
    trace_id: str | None = None


class ModelResponse(BaseModel):
    """Normalised provider response.

    ``latency_ms`` and ``usage`` are the inputs the telemetry layer needs.
    Recording them is the caller's responsibility; no execution path exists in
    this scaffold, so nothing is recorded yet.
    """

    model_config = ConfigDict(frozen=True)

    content: str
    model: str
    usage: TokenUsage = Field(default_factory=TokenUsage)
    finish_reason: str | None = None
    latency_ms: float
    provider: str
    raw: dict[str, Any] | None = None


class ModelGatewayInterface(ABC):
    """The only supported entry point for model invocation."""

    provider_name: str

    @abstractmethod
    async def generate(self, request: ModelRequest) -> ModelResponse:
        """Invoke a model.

        Raises:
            GatewayError: for any provider failure, already normalised.
        """

    @abstractmethod
    async def healthcheck(self) -> bool:
        """Report whether the gateway is usable.

        Implementations must not make a billable model call here.
        """

    @abstractmethod
    async def close(self) -> None:
        """Release underlying network resources."""
