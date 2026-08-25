"""Mock model gateway.

Tests must never depend on a live LLM API (AI_DEVELOPMENT_RULES.md section 25).
This implementation is deterministic, records the calls it receives, and makes
no network connection.
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.integrations.model_gateway.base import (
    ModelGatewayInterface,
    ModelRequest,
    ModelResponse,
    TokenUsage,
)

logger = get_logger(__name__)


class MockModelGateway(ModelGatewayInterface):
    """Deterministic in-memory gateway."""

    provider_name = "mock"

    def __init__(
        self,
        *,
        canned_content: str = "",
        report_usage: bool = True,
    ) -> None:
        self._canned_content = canned_content
        self._report_usage = report_usage
        self.calls: list[ModelRequest] = []
        self._closed = False

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)

        content = self._canned_content or f"mock-response:{request.model}"

        # `report_usage=False` simulates a provider that returns no usage, so
        # the cost layer can be tested against provenance ESTIMATED/UNAVAILABLE
        # without fabricating token counts.
        usage = (
            TokenUsage(input_tokens=0, output_tokens=0, total_tokens=0)
            if self._report_usage
            else TokenUsage()
        )

        logger.debug("mock_gateway_generate", extra={"model": request.model})
        return ModelResponse(
            content=content,
            model=request.model,
            usage=usage,
            finish_reason="stop",
            latency_ms=0.0,
            provider=self.provider_name,
        )

    async def healthcheck(self) -> bool:
        return not self._closed

    async def close(self) -> None:
        self._closed = True

    @property
    def call_count(self) -> int:
        return len(self.calls)
