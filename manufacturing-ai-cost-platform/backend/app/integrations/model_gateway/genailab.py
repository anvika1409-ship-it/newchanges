"""GenAILab adapter.

GenAILab is reached through an OpenAI-compatible ``AsyncOpenAI`` client pointed
at ``GENAI_BASE_URL`` (ARCHITECTURE.md section 7). All GenAILab-specific
behaviour stays inside this module.

Nothing here assumes a model name, price, context length, modality or response
field beyond the OpenAI-compatible chat completion shape the documents describe.
Capability comes from the model registry, not from this adapter
(AI_DEVELOPMENT_RULES.md section 5).
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from app.core.config import Settings
from app.core.errors import GatewayError
from app.core.logging import get_logger
from app.integrations.model_gateway.base import (
    ModelGatewayInterface,
    ModelRequest,
    ModelResponse,
    TokenUsage,
)

logger = get_logger(__name__)


class GenAILabAdapter(ModelGatewayInterface):
    """Adapter over the GenAILab OpenAI-compatible endpoint."""

    provider_name = "genailab"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: Any | None = None
        self._http_client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------ setup
    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client

        from openai import AsyncOpenAI

        # TLS verification is configuration. `SSL_VERIFY=false` is permitted for
        # the internal development environment; Settings refuses it in
        # production unless a documented exception is recorded.
        self._http_client = httpx.AsyncClient(
            verify=self._settings.ssl_verify,
            timeout=httpx.Timeout(self._settings.genai_timeout_seconds),
        )
        if not self._settings.ssl_verify:
            logger.warning(
                "tls_verification_disabled",
                extra={"provider": self.provider_name, "app_env": str(self._settings.app_env)},
            )

        self._client = AsyncOpenAI(
            base_url=self._settings.genai_base_url,
            api_key=self._settings.genai_api_key.get_secret_value(),
            http_client=self._http_client,
            max_retries=self._settings.genai_max_retries,
        )
        # Base URL only. The key and authorization headers are never logged
        # (AI_DEVELOPMENT_RULES.md section 27).
        logger.info(
            "model_gateway_client_initialised",
            extra={"provider": self.provider_name, "base_url": self._settings.genai_base_url},
        )
        return self._client

    # ------------------------------------------------------------------ usage
    async def generate(self, request: ModelRequest) -> ModelResponse:
        client = self._ensure_client()

        payload: dict[str, Any] = {
            "model": request.model,
            "messages": [
                {"role": str(message.role), "content": message.content}
                for message in request.messages
            ],
        }
        if request.max_output_tokens is not None:
            payload["max_tokens"] = request.max_output_tokens
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.response_format == "json_object":
            payload["response_format"] = {"type": "json_object"}
        if request.timeout_seconds is not None:
            payload["timeout"] = request.timeout_seconds

        started = time.perf_counter()
        try:
            completion = await client.chat.completions.create(**payload)
        except Exception as exc:
            # Normalised so business logic never handles a vendor exception type.
            # The message is deliberately generic; detail goes to the log only.
            logger.exception(
                "model_gateway_call_failed",
                extra={"provider": self.provider_name, "model": request.model},
            )
            raise GatewayError(
                "Model gateway request failed",
                details={"provider": self.provider_name},
            ) from exc

        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        return self._to_response(completion, request, latency_ms)

    def _to_response(
        self, completion: Any, request: ModelRequest, latency_ms: float
    ) -> ModelResponse:
        """Map an OpenAI-compatible completion onto the normalised response.

        Fields that are absent stay absent. Usage is not estimated here.
        """
        choices = getattr(completion, "choices", None) or []
        if not choices:
            raise GatewayError(
                "Model gateway returned no choices",
                details={"provider": self.provider_name},
            )

        first = choices[0]
        content = getattr(getattr(first, "message", None), "content", None) or ""
        finish_reason = getattr(first, "finish_reason", None)

        raw_usage = getattr(completion, "usage", None)
        usage = TokenUsage(
            input_tokens=getattr(raw_usage, "prompt_tokens", None),
            output_tokens=getattr(raw_usage, "completion_tokens", None),
            total_tokens=getattr(raw_usage, "total_tokens", None),
        )
        if not usage.is_complete:
            logger.info(
                "model_usage_unreported",
                extra={"provider": self.provider_name, "model": request.model},
            )

        return ModelResponse(
            content=content,
            model=getattr(completion, "model", request.model),
            usage=usage,
            finish_reason=finish_reason,
            latency_ms=latency_ms,
            provider=self.provider_name,
        )

    async def healthcheck(self) -> bool:
        """Report whether the adapter is configured.

        This intentionally performs no network call: probing GenAILab on every
        readiness check would add latency and cost, and the documents do not
        define a free health endpoint for it.
        """
        return bool(self._settings.genai_api_key.get_secret_value()) and bool(
            self._settings.genai_base_url
        )

    async def close(self) -> None:
        if self._http_client is not None:
            await self._http_client.aclose()
        self._http_client = None
        self._client = None
