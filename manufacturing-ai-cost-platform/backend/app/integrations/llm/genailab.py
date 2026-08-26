"""GenAILab adapter.

The only module in the codebase that imports a model provider SDK.

GenAILab is reached through an OpenAI-compatible ``AsyncOpenAI`` client pointed
at ``GENAI_BASE_URL`` (ARCHITECTURE.md section 7). This module translates the
platform's request and response types onto that protocol and normalizes its
errors — nothing more. Retry, backoff, circuit breaking and telemetry live in
``client.py`` so they are shared by every provider.

Two constraints are enforced strictly here:

* **No assumed response fields.** Every value is read defensively. When the
  response lacks something an operation genuinely requires, the adapter raises
  ``GatewayResponseError`` rather than substituting a plausible value.
* **No assumed usage.** Token counts are extracted only when the provider
  actually returns them. Otherwise usage stays unreported with provenance
  UNAVAILABLE (AI_DEVELOPMENT_RULES.md section 10).

Nothing here assumes a model name, price, context length or modality. Those come
from the model registry (ARCHITECTURE.md section 8).
"""

from __future__ import annotations

import base64
import time
from typing import Any, Literal

import httpx

from app.core.config import Settings
from app.core.logging import get_logger
from app.integrations.llm.errors import (
    GatewayAuthenticationError,
    GatewayBadRequestError,
    GatewayRateLimitError,
    GatewayResponseError,
    GatewayTimeoutError,
    GatewayUnavailableError,
    ModelGatewayError,
)
from app.integrations.llm.interface import (
    Capability,
    EmbeddingRequest,
    EmbeddingResponse,
    ImagePart,
    Message,
    ModelGatewayInterface,
    MultimodalGenerationRequest,
    SpeechTranscriptionRequest,
    SpeechTranscriptionResponse,
    TextGenerationRequest,
    TextGenerationResponse,
    TextPart,
    TokenUsage,
    UsageProvenance,
)

logger = get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"
TRACE_ID_HEADER = "X-Trace-ID"


class GenAILabAdapter(ModelGatewayInterface):
    """Protocol translation for the GenAILab OpenAI-compatible endpoint."""

    provider_name = "genailab"
    capabilities = frozenset(
        {Capability.TEXT, Capability.MULTIMODAL, Capability.EMBEDDING, Capability.SPEECH}
    )

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: Any | None = None
        self._http_client: httpx.AsyncClient | None = None

    # ================================================================= setup
    def _ensure_client(self) -> Any:
        """Construct the SDK client lazily.

        Lazy so importing this module — or wiring the app on the mock provider —
        never opens a connection or requires credentials.
        """
        if self._client is not None:
            return self._client

        from openai import AsyncOpenAI

        # TLS verification is configuration. SSL_VERIFY=false is permitted for
        # the internal development environment; Settings refuses it in
        # production unless a documented exception is recorded.
        self._http_client = httpx.AsyncClient(
            verify=self._settings.ssl_verify,
            timeout=httpx.Timeout(self._settings.genai_timeout_seconds),
        )
        if not self._settings.ssl_verify:
            logger.warning(
                "tls_verification_disabled",
                extra={
                    "provider": self.provider_name,
                    "app_env": str(self._settings.app_env),
                },
            )

        self._client = AsyncOpenAI(
            base_url=self._settings.genai_base_url,
            api_key=self._settings.genai_api_key.get_secret_value(),
            http_client=self._http_client,
            # Retrying is owned by ResilientGateway. Leaving the SDK's own
            # retries enabled would multiply attempts and defeat the bounded
            # retry budget.
            max_retries=0,
        )
        # Base URL only. The key and Authorization header are never logged
        # (AI_DEVELOPMENT_RULES.md section 27).
        logger.info(
            "model_gateway_client_initialised",
            extra={
                "provider": self.provider_name,
                "base_url": self._settings.genai_base_url,
                "tls_verify": self._settings.ssl_verify,
            },
        )
        return self._client

    def _correlation_headers(self, request_id: str | None, trace_id: str | None) -> dict[str, str]:
        """Propagate correlation ids to the provider.

        Sent as ordinary headers. A gateway that does not recognise them ignores
        them; nothing in the response is assumed to come back.
        """
        headers: dict[str, str] = {}
        if request_id:
            headers[REQUEST_ID_HEADER] = request_id
        if trace_id:
            headers[TRACE_ID_HEADER] = trace_id
        return headers

    # ============================================================ operations
    async def generate_text(self, request: TextGenerationRequest) -> TextGenerationResponse:
        return await self._chat_completion(request, operation="generate_text")

    async def generate_multimodal(
        self, request: MultimodalGenerationRequest
    ) -> TextGenerationResponse:
        # Verified rather than trusted: a "multimodal" request with no image is
        # a caller defect, and silently sending it as text would hide that.
        if not any(message.has_image for message in request.messages):
            raise GatewayBadRequestError(
                "A multimodal request must contain at least one image part",
                provider=self.provider_name,
                operation="generate_multimodal",
                model=request.model,
            )
        return await self._chat_completion(request, operation="generate_multimodal")

    async def _chat_completion(
        self, request: TextGenerationRequest, *, operation: str
    ) -> TextGenerationResponse:
        client = self._ensure_client()

        payload: dict[str, Any] = {
            "model": request.model,
            "messages": [self._encode_message(m) for m in request.messages],
        }
        # Optional parameters are omitted entirely when unset, rather than sent
        # as nulls: a strict gateway may reject an explicit null.
        if request.max_output_tokens is not None:
            payload["max_tokens"] = request.max_output_tokens
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.response_format == "json_object":
            payload["response_format"] = {"type": "json_object"}

        started = time.perf_counter()
        with self._normalized_errors(operation, request.model):
            completion = await client.chat.completions.create(
                **payload,
                timeout=request.timeout_seconds or self._settings.genai_timeout_seconds,
                extra_headers=self._correlation_headers(request.request_id, request.trace_id),
            )
        latency_ms = round((time.perf_counter() - started) * 1000, 2)

        choices = getattr(completion, "choices", None) or []
        if not choices:
            raise GatewayResponseError(
                "Response contained no choices",
                provider=self.provider_name,
                operation=operation,
                model=request.model,
            )

        first = choices[0]
        message = getattr(first, "message", None)
        content = getattr(message, "content", None)
        if content is None:
            # Could be a tool call or a content filter. Either way there is no
            # text to return, and inventing an empty string would hide it.
            raise GatewayResponseError(
                "Response choice contained no message content",
                provider=self.provider_name,
                operation=operation,
                model=request.model,
                details={"finish_reason": getattr(first, "finish_reason", None)},
            )

        return TextGenerationResponse(
            content=str(content),
            model=str(getattr(completion, "model", request.model)),
            provider=self.provider_name,
            usage=self._extract_usage(completion),
            finish_reason=getattr(first, "finish_reason", None),
            latency_ms=latency_ms,
        )

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        client = self._ensure_client()

        started = time.perf_counter()
        with self._normalized_errors("embed", request.model):
            response = await client.embeddings.create(
                model=request.model,
                input=list(request.inputs),
                timeout=request.timeout_seconds or self._settings.genai_timeout_seconds,
                extra_headers=self._correlation_headers(request.request_id, request.trace_id),
            )
        latency_ms = round((time.perf_counter() - started) * 1000, 2)

        items = getattr(response, "data", None) or []
        if len(items) != len(request.inputs):
            # A partial result would silently misalign vectors with inputs.
            raise GatewayResponseError(
                "Embedding count did not match the number of inputs",
                provider=self.provider_name,
                operation="embed",
                model=request.model,
                details={"expected": len(request.inputs), "received": len(items)},
            )

        vectors: list[tuple[float, ...]] = []
        # Order by `index` when present; the protocol does not guarantee the
        # array order matches the input order.
        ordered = sorted(items, key=lambda item: getattr(item, "index", 0))
        for item in ordered:
            embedding = getattr(item, "embedding", None)
            if not embedding:
                raise GatewayResponseError(
                    "Embedding item contained no vector",
                    provider=self.provider_name,
                    operation="embed",
                    model=request.model,
                )
            vectors.append(tuple(float(value) for value in embedding))

        return EmbeddingResponse(
            embeddings=tuple(vectors),
            model=str(getattr(response, "model", request.model)),
            provider=self.provider_name,
            usage=self._extract_usage(response),
            latency_ms=latency_ms,
        )

    async def transcribe(
        self, request: SpeechTranscriptionRequest
    ) -> SpeechTranscriptionResponse:
        client = self._ensure_client()

        payload: dict[str, Any] = {
            "model": request.model,
            "file": (request.filename, request.audio),
        }
        if request.language:
            payload["language"] = request.language

        started = time.perf_counter()
        with self._normalized_errors("transcribe", request.model):
            response = await client.audio.transcriptions.create(
                **payload,
                timeout=request.timeout_seconds or self._settings.genai_timeout_seconds,
                extra_headers=self._correlation_headers(request.request_id, request.trace_id),
            )
        latency_ms = round((time.perf_counter() - started) * 1000, 2)

        text = getattr(response, "text", None)
        if text is None:
            raise GatewayResponseError(
                "Transcription response contained no text",
                provider=self.provider_name,
                operation="transcribe",
                model=request.model,
            )

        return SpeechTranscriptionResponse(
            text=str(text),
            model=request.model,
            provider=self.provider_name,
            # Transcription responses commonly carry no usage. Extracted the
            # same way as everywhere else: present or absent, never invented.
            usage=self._extract_usage(response),
            latency_ms=latency_ms,
        )

    # =============================================================== encoding
    def _encode_message(self, message: Message) -> dict[str, Any]:
        if isinstance(message.content, str):
            return {"role": str(message.role), "content": message.content}

        parts: list[dict[str, Any]] = []
        for part in message.content:
            if isinstance(part, TextPart):
                parts.append({"type": "text", "text": part.text})
            elif isinstance(part, ImagePart):
                encoded = base64.b64encode(part.data).decode("ascii")
                parts.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{part.media_type};base64,{encoded}"},
                    }
                )
        return {"role": str(message.role), "content": parts}

    # =============================================================== decoding
    def _extract_usage(self, response: Any) -> TokenUsage:
        """Read token usage only when the provider actually reported it.

        Absence is recorded as UNAVAILABLE rather than zero. Zero is a
        measurement; absence is not, and conflating them would produce a
        fabricated actual in the cost tables.
        """
        raw = getattr(response, "usage", None)
        if raw is None:
            return TokenUsage(provenance=UsageProvenance.UNAVAILABLE)

        input_tokens = _int_or_none(getattr(raw, "prompt_tokens", None))
        output_tokens = _int_or_none(getattr(raw, "completion_tokens", None))
        total_tokens = _int_or_none(getattr(raw, "total_tokens", None))

        if input_tokens is None and output_tokens is None and total_tokens is None:
            # A usage object with nothing usable in it is still no usage.
            return TokenUsage(provenance=UsageProvenance.UNAVAILABLE)

        return TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            provenance=UsageProvenance.ACTUAL,
        )

    # ========================================================= normalization
    def _normalized_errors(self, operation: str, model: str) -> _ErrorNormalizer:
        return _ErrorNormalizer(self.provider_name, operation, model)

    # ================================================================ health
    async def healthcheck(self) -> bool:
        """Report whether the adapter is configured.

        Deliberately no network call: probing the provider on every readiness
        check would add latency and, on a billable endpoint, cost. The documents
        do not describe a free health endpoint for GenAILab, and inventing one
        is not permitted.
        """
        return bool(self._settings.genai_api_key.get_secret_value()) and bool(
            self._settings.genai_base_url
        )

    async def close(self) -> None:
        if self._http_client is not None:
            await self._http_client.aclose()
        self._http_client = None
        self._client = None


def _int_or_none(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class _ErrorNormalizer:
    """Context manager translating provider exceptions into platform errors.

    Provider exception detail goes to the log; the raised error carries only
    what is safe to surface. Nothing that could contain a credential or prompt
    content is copied into the message.
    """

    def __init__(self, provider: str, operation: str, model: str) -> None:
        self._provider = provider
        self._operation = operation
        self._model = model

    def __enter__(self) -> _ErrorNormalizer:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> Literal[False]:
        """Never suppresses. Either lets the exception through or replaces it."""
        if exc is None:
            return False
        if isinstance(exc, ModelGatewayError):
            return False  # already normalized

        raise self._translate(exc) from exc

    def _build(
        self,
        error_cls: type[ModelGatewayError],
        message: str | None = None,
    ) -> ModelGatewayError:
        """Construct a normalized error carrying this call's context."""
        return error_cls(
            message,
            provider=self._provider,
            operation=self._operation,
            model=self._model,
        )

    def _translate(self, exc: BaseException) -> ModelGatewayError:
        import openai

        logger.warning(
            "model_gateway_provider_error",
            extra={
                "provider": self._provider,
                "operation": self._operation,
                "model": self._model,
                "exception_type": type(exc).__name__,
            },
        )

        if isinstance(exc, openai.APITimeoutError):
            return self._build(GatewayTimeoutError)
        if isinstance(exc, openai.AuthenticationError | openai.PermissionDeniedError):
            return self._build(GatewayAuthenticationError)
        if isinstance(exc, openai.RateLimitError):
            return GatewayRateLimitError(
                retry_after_seconds=_retry_after(exc),
                provider=self._provider,
                operation=self._operation,
                model=self._model,
            )
        if isinstance(exc, openai.BadRequestError | openai.UnprocessableEntityError):
            return self._build(GatewayBadRequestError)
        if isinstance(exc, openai.NotFoundError):
            # Usually an unknown model id. A caller-side problem; retrying it
            # never helps.
            return self._build(
                GatewayBadRequestError,
                "Model gateway does not recognise the requested model",
            )
        if isinstance(exc, openai.APIConnectionError | openai.InternalServerError):
            return self._build(GatewayUnavailableError)
        if isinstance(exc, openai.APIStatusError):
            status = getattr(exc, "status_code", None)
            if status is not None and 500 <= int(status) < 600:
                return self._build(GatewayUnavailableError)
            return self._build(GatewayBadRequestError)
        if isinstance(exc, TimeoutError):
            return self._build(GatewayTimeoutError)

        # Unrecognised failure: treated as not retryable. Retrying something we
        # do not understand risks repeating a billable call for no reason.
        return self._build(ModelGatewayError)


def _retry_after(exc: Any) -> float | None:
    """Read Retry-After when the provider supplied it."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    raw = headers.get("retry-after") or headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None
