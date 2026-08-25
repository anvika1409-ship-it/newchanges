"""Model gateway tests.

No live LLM call is made anywhere in this module
(AI_DEVELOPMENT_RULES.md section 25).
"""

from __future__ import annotations

import pytest

from app.core.config import AppEnv, AuthMode, ModelGatewayProvider, Settings
from app.integrations.model_gateway.base import (
    Message,
    ModelGatewayInterface,
    ModelRequest,
    Role,
    TokenUsage,
)
from app.integrations.model_gateway.factory import build_model_gateway
from app.integrations.model_gateway.genailab import GenAILabAdapter
from app.integrations.model_gateway.mock import MockModelGateway

# Not a credential. Only proves the adapter is constructed and that the value
# never reaches a log line or a repr.
FAKE_CREDENTIAL = "unit-test-sentinel-value"


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "app_env": AppEnv.DEVELOPMENT,
        "database_url": "sqlite+aiosqlite:///:memory:",
        "redis_enabled": False,
        "auth_mode": AuthMode.DEVELOPMENT,
        "model_gateway_provider": ModelGatewayProvider.MOCK,
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------- the factory
def test_factory_returns_the_mock_gateway_by_configuration() -> None:
    gateway = build_model_gateway(_settings())
    assert isinstance(gateway, MockModelGateway)
    assert gateway.provider_name == "mock"


def test_factory_returns_the_genailab_adapter_by_configuration() -> None:
    gateway = build_model_gateway(
        _settings(
            model_gateway_provider=ModelGatewayProvider.GENAILAB,
            genai_api_key=FAKE_CREDENTIAL,
        )
    )
    assert isinstance(gateway, GenAILabAdapter)
    assert gateway.provider_name == "genailab"


def test_every_gateway_implements_the_interface() -> None:
    """Business logic depends on the interface, never on a concrete adapter."""
    for gateway in (
        build_model_gateway(_settings()),
        build_model_gateway(
            _settings(
                model_gateway_provider=ModelGatewayProvider.GENAILAB,
                genai_api_key=FAKE_CREDENTIAL,
            )
        ),
    ):
        assert isinstance(gateway, ModelGatewayInterface)


# ------------------------------------------------------ adapter initialisation
def test_genailab_adapter_constructs_without_network_access() -> None:
    """Building the adapter must not open a connection.

    Client construction is lazy, so importing or wiring the app never reaches
    the network.
    """
    adapter = GenAILabAdapter(
        _settings(
            model_gateway_provider=ModelGatewayProvider.GENAILAB,
            genai_api_key=FAKE_CREDENTIAL,
        )
    )
    assert adapter._client is None  # noqa: SLF001 - asserting lazy construction


async def test_genailab_adapter_healthcheck_makes_no_model_call() -> None:
    adapter = GenAILabAdapter(
        _settings(
            model_gateway_provider=ModelGatewayProvider.GENAILAB,
            genai_api_key=FAKE_CREDENTIAL,
        )
    )
    assert await adapter.healthcheck() is True
    assert adapter._client is None  # noqa: SLF001
    await adapter.close()


def test_adapter_does_not_leak_the_api_key_in_its_representation() -> None:
    adapter = GenAILabAdapter(
        _settings(
            model_gateway_provider=ModelGatewayProvider.GENAILAB,
            genai_api_key=FAKE_CREDENTIAL,
        )
    )
    assert FAKE_CREDENTIAL not in repr(adapter)
    assert FAKE_CREDENTIAL not in repr(adapter._settings)  # noqa: SLF001


# ------------------------------------------------------------ mock behaviour
async def test_mock_gateway_returns_a_deterministic_response() -> None:
    gateway = MockModelGateway()
    request = ModelRequest(
        model="registry-supplied-model-id",
        messages=[Message(role=Role.USER, content="hello")],
    )

    first = await gateway.generate(request)
    second = await gateway.generate(request)

    assert first.content == second.content
    assert first.model == "registry-supplied-model-id"
    assert first.provider == "mock"
    assert gateway.call_count == 2


async def test_mock_gateway_can_simulate_unreported_usage() -> None:
    """A provider that reports no usage must not produce fabricated tokens.

    The cost layer relies on this to record provenance ESTIMATED or UNAVAILABLE
    rather than inventing actuals.
    """
    gateway = MockModelGateway(report_usage=False)
    response = await gateway.generate(
        ModelRequest(
            model="registry-supplied-model-id",
            messages=[Message(role=Role.USER, content="hello")],
        )
    )

    assert response.usage == TokenUsage()
    assert response.usage.input_tokens is None
    assert response.usage.is_complete is False


async def test_mock_gateway_closes() -> None:
    gateway = MockModelGateway()
    assert await gateway.healthcheck() is True
    await gateway.close()
    assert await gateway.healthcheck() is False


# ------------------------------------------------------------- request typing
def test_model_is_required_and_never_defaulted() -> None:
    """Model identity comes from the registry, not from a gateway default."""
    with pytest.raises(ValueError):
        ModelRequest(messages=[Message(role=Role.USER, content="hi")])  # type: ignore[call-arg]

    with pytest.raises(ValueError):
        ModelRequest(model="", messages=[Message(role=Role.USER, content="hi")])


def test_request_requires_at_least_one_message() -> None:
    with pytest.raises(ValueError):
        ModelRequest(model="registry-supplied-model-id", messages=[])


def test_token_usage_defaults_to_unreported() -> None:
    usage = TokenUsage()
    assert usage.input_tokens is None
    assert usage.output_tokens is None
    assert usage.total_tokens is None
    assert usage.is_complete is False
