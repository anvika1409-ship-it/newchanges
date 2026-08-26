"""Live GenAILab smoke test.

Excluded from the default test run. AI_DEVELOPMENT_RULES.md section 25 permits
live provider calls only in explicitly configured smoke tests, and every call
here costs real money.

Two independent switches must both be on before anything is sent:

1. the ``smoke`` pytest marker must be selected explicitly, and
2. ``GENAI_SMOKE_TEST_ENABLED=true`` plus ``GENAI_SMOKE_TEST_MODEL`` must be set.

Run it with:

    GENAI_SMOKE_TEST_ENABLED=true \\
    GENAI_SMOKE_TEST_MODEL=<model-id-from-the-registry> \\
    pytest -m smoke tests/test_genailab_smoke.py

The model id is deliberately not defaulted. ARCHITECTURE.md section 8 lists
example model names but the available set is registry and configuration driven,
so hard-coding one here would be an assumption.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import ModelGatewayProvider, get_settings
from app.integrations.llm.client import build_model_gateway
from app.integrations.llm.interface import (
    Message,
    Role,
    TextGenerationRequest,
    UsageProvenance,
)

pytestmark = pytest.mark.smoke


def _settings_or_skip():  # noqa: ANN202 - pytest.skip is NoReturn on one path
    """Load real settings, skipping unless the smoke test is switched on."""
    get_settings.cache_clear()
    try:
        settings = get_settings()
    except ValidationError as exc:
        # The environment is not configured for a live run at all. Reported as
        # "not configured" rather than a failure, so selecting -m smoke on a
        # developer machine does not look like a GenAILab outage. The
        # configuration guards themselves are not bypassed.
        pytest.skip(
            "environment is not fully configured for a live run "
            f"({exc.error_count()} configuration issue(s))"
        )

    if not settings.genai_smoke_test_enabled:
        pytest.skip("GENAI_SMOKE_TEST_ENABLED is not true")
    if not settings.genai_smoke_test_model:
        pytest.skip("GENAI_SMOKE_TEST_MODEL is not set")
    if settings.model_gateway_provider is not ModelGatewayProvider.GENAILAB:
        pytest.skip("MODEL_GATEWAY_PROVIDER is not genailab")
    if not settings.genai_api_key.get_secret_value():
        pytest.skip("GENAI_API_KEY is not set")

    return settings


async def test_text_generation_against_live_genailab() -> None:
    """One small real call, end to end through the gateway."""
    settings = _settings_or_skip()
    gateway = build_model_gateway(settings)

    try:
        response = await gateway.generate_text(
            TextGenerationRequest(
                model=settings.genai_smoke_test_model,
                messages=(
                    Message(role=Role.SYSTEM, content="Reply with exactly one word."),
                    Message(role=Role.USER, content="Say: ready"),
                ),
                # Kept tiny; this is a connectivity check, not a capability test.
                max_output_tokens=16,
                temperature=0.0,
                request_id="smoke-test",
            )
        )
    finally:
        await gateway.close()

    assert response.content
    assert response.provider == "genailab"
    assert response.latency_ms > 0

    # Usage is asserted only if the gateway actually returned it. Requiring it
    # would bake in an assumption about a response field the documents do not
    # promise.
    if response.usage.provenance is UsageProvenance.ACTUAL:
        assert response.usage.input_tokens is not None or (
            response.usage.total_tokens is not None
        )


async def test_healthcheck_against_live_configuration() -> None:
    """Configuration check only — makes no billable call."""
    settings = _settings_or_skip()
    gateway = build_model_gateway(settings)
    try:
        assert await gateway.healthcheck() is True
    finally:
        await gateway.close()
