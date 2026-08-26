"""Model gateway interface.

    Application -> ModelGatewayInterface -> GenAILabAdapter -> AsyncOpenAI -> GenAILab

This is the only supported route from application code to any model provider
(ARCHITECTURE.md section 7). Business logic depends on the types in this module
and never imports a provider SDK.

Three rules shape every type here:

* **Model identity always comes from the caller.** No model name is defaulted or
  inferred. Capability, pricing, context length and latency are registry
  metadata (ARCHITECTURE.md section 8), not assumptions this layer may make.
* **Usage is only reported when the provider actually returns it.** Absent usage
  stays absent, with provenance UNAVAILABLE, so the cost layer never records a
  fabricated actual (AI_DEVELOPMENT_RULES.md section 10).
* **Images are passed as bytes, never as a URL.** Handing a provider an
  arbitrary URL to fetch turns the model into a request forwarder. The caller
  resolves object-storage references to bytes before reaching this layer.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Capability(StrEnum):
    """Operations a gateway may support."""

    TEXT = "text"
    MULTIMODAL = "multimodal"
    EMBEDDING = "embedding"
    SPEECH = "speech"


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class UsageProvenance(StrEnum):
    """Where the reported token counts came from.

    The gateway only ever reports ACTUAL or UNAVAILABLE. It never estimates —
    estimation is the cost layer's job, using registry pricing
    (DATABASE_SCHEMA.md section 15).
    """

    ACTUAL = "ACTUAL"
    UNAVAILABLE = "UNAVAILABLE"


# --------------------------------------------------------------- content parts
class TextPart(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: Literal["text"] = "text"
    text: str


class ImagePart(BaseModel):
    """Inline image bytes with an explicit media type.

    Deliberately no URL variant. See the module docstring.
    """

    model_config = ConfigDict(frozen=True)

    type: Literal["image"] = "image"
    data: bytes
    media_type: str = Field(pattern=r"^image/[a-zA-Z0-9.+-]+$")


ContentPart = Annotated[TextPart | ImagePart, Field(discriminator="type")]


class Message(BaseModel):
    """A chat message.

    Untrusted retrieved content belongs in a ``user`` message, clearly separated
    from system instructions, never concatenated into the system prompt
    (SECURITY.md section 9).
    """

    model_config = ConfigDict(frozen=True)

    role: Role
    content: str | tuple[ContentPart, ...]

    @property
    def has_image(self) -> bool:
        if isinstance(self.content, str):
            return False
        return any(isinstance(part, ImagePart) for part in self.content)


# --------------------------------------------------------------------- usage
class TokenUsage(BaseModel):
    """Token counts, when the provider reported them."""

    model_config = ConfigDict(frozen=True)

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    provenance: UsageProvenance = UsageProvenance.UNAVAILABLE

    @property
    def is_reported(self) -> bool:
        return self.provenance is UsageProvenance.ACTUAL


# ------------------------------------------------------------------ requests
class _BaseRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    # Required. Supplied by the orchestrator from the model registry.
    model: str = Field(min_length=1)

    #: Per-call override. Falls back to the configured gateway timeout.
    timeout_seconds: float | None = Field(default=None, gt=0)

    #: Correlation only — never used for authorization.
    request_id: str | None = None
    trace_id: str | None = None


class TextGenerationRequest(_BaseRequest):
    messages: tuple[Message, ...] = Field(min_length=1)
    max_output_tokens: int | None = Field(default=None, gt=0)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)

    #: Structured output is preferred wherever a result is consumed
    #: programmatically (AI_DEVELOPMENT_RULES.md section 37).
    response_format: Literal["text", "json_object"] = "text"

    #: Hard ceiling on provider-side tool calls (SECURITY.md section 19).
    max_tool_calls: int | None = Field(default=None, ge=0)


class MultimodalGenerationRequest(TextGenerationRequest):
    """Text plus image input.

    Structurally identical to a text request; the distinction is that at least
    one message carries an ``ImagePart``, and the gateway checks that rather
    than trusting the caller's word.
    """


class EmbeddingRequest(_BaseRequest):
    inputs: tuple[str, ...] = Field(min_length=1)


class SpeechTranscriptionRequest(_BaseRequest):
    """Audio transcription."""

    audio: bytes
    filename: str = Field(min_length=1)
    #: BCP-47 hint. Omitted from the call entirely when None.
    language: str | None = None


# ----------------------------------------------------------------- responses
class _BaseResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    model: str
    provider: str
    usage: TokenUsage = Field(default_factory=TokenUsage)

    #: Wall-clock duration of the successful provider call.
    latency_ms: float

    #: Attempts made, including the successful one. 1 means no retry occurred.
    attempts: int = 1


class TextGenerationResponse(_BaseResponse):
    content: str
    finish_reason: str | None = None


class EmbeddingResponse(_BaseResponse):
    """One vector per input, in the order the inputs were supplied."""

    embeddings: tuple[tuple[float, ...], ...]


class SpeechTranscriptionResponse(_BaseResponse):
    text: str


# ----------------------------------------------------------------- the port
class ModelGatewayInterface(ABC):
    """The only supported entry point for model invocation."""

    provider_name: str

    #: Operations this implementation supports. Callers should check before
    #: dispatching; every method also enforces it.
    capabilities: frozenset[Capability] = frozenset()

    def supports(self, capability: Capability) -> bool:
        return capability in self.capabilities

    @abstractmethod
    async def generate_text(self, request: TextGenerationRequest) -> TextGenerationResponse:
        """Text generation.

        Raises:
            ModelGatewayError: normalized provider failure.
        """

    @abstractmethod
    async def generate_multimodal(
        self, request: MultimodalGenerationRequest
    ) -> TextGenerationResponse:
        """Generation over text and image input."""

    @abstractmethod
    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        """Embedding generation."""

    @abstractmethod
    async def transcribe(
        self, request: SpeechTranscriptionRequest
    ) -> SpeechTranscriptionResponse:
        """Speech transcription."""

    @abstractmethod
    async def healthcheck(self) -> bool:
        """Report usability.

        Implementations must not make a billable model call here.
        """

    @abstractmethod
    async def close(self) -> None:
        """Release network resources."""

    # ------------------------------------------------------------- helpers
    @staticmethod
    def _describe_messages(messages: tuple[Message, ...]) -> dict[str, Any]:
        """Non-sensitive shape summary for logs.

        Message *content* is never logged: prompts can carry sensitive
        manufacturing or personal data (SECURITY.md section 17).
        """
        return {
            "message_count": len(messages),
            "image_part_count": sum(
                1
                for m in messages
                if not isinstance(m.content, str)
                for p in m.content
                if isinstance(p, ImagePart)
            ),
            "approx_text_chars": sum(
                len(m.content)
                if isinstance(m.content, str)
                else sum(len(p.text) for p in m.content if isinstance(p, TextPart))
                for m in messages
            ),
        }
