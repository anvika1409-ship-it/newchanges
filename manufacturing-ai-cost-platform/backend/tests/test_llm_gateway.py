"""Model gateway tests: interface, capabilities, mock, GenAILab decoding.

No live provider call is made anywhere in this module
(AI_DEVELOPMENT_RULES.md section 25).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.core.config import AppEnv, AuthMode, ModelGatewayProvider, Settings
from app.integrations.llm.client import (
    MockModelGateway,
    ResilientGateway,
    build_model_gateway,
)
from app.integrations.llm.errors import (
    GatewayBadRequestError,
    GatewayResponseError,
    ModelCapabilityError,
)
from app.integrations.llm.genailab import GenAILabAdapter
from app.integrations.llm.interface import (
    Capability,
    EmbeddingRequest,
    ImagePart,
    Message,
    ModelGatewayInterface,
    MultimodalGenerationRequest,
    Role,
    SpeechTranscriptionRequest,
    TextGenerationRequest,
    TextPart,
    TokenUsage,
    UsageProvenance,
)
from app.integrations.llm.telemetry import CollectingTelemetrySink

FAKE_CREDENTIAL = "unit-test-sentinel-value-padded-to-length"
MODEL = "registry-supplied-model-id"

# A 1x1 PNG. Content is irrelevant; only that image bytes are carried.
PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
)


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "app_env": AppEnv.DEVELOPMENT,
        "database_url": "sqlite+aiosqlite:///:memory:",
        "redis_enabled": False,
        "auth_mode": AuthMode.DEVELOPMENT,
        "jwt_secret": FAKE_CREDENTIAL,
        "model_gateway_provider": ModelGatewayProvider.MOCK,
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


def _genailab_settings(**overrides: object) -> Settings:
    return _settings(
        model_gateway_provider=ModelGatewayProvider.GENAILAB,
        genai_api_key=FAKE_CREDENTIAL,
        **overrides,
    )


def _text_request(**overrides: Any) -> TextGenerationRequest:
    return TextGenerationRequest(
        model=MODEL,
        messages=(Message(role=Role.USER, content="hello"),),
        **overrides,
    )


# ===========================================================================
# Factory and interface
# ===========================================================================
def test_factory_returns_the_mock_wrapped_in_resilience() -> None:
    gateway = build_model_gateway(_settings())
    assert isinstance(gateway, ResilientGateway)
    assert isinstance(gateway.inner, MockModelGateway)
    assert gateway.provider_name == "mock"


def test_factory_returns_the_genailab_adapter_wrapped_in_resilience() -> None:
    gateway = build_model_gateway(_genailab_settings())
    assert isinstance(gateway, ResilientGateway)
    assert isinstance(gateway.inner, GenAILabAdapter)
    assert gateway.provider_name == "genailab"


def test_every_gateway_implements_the_interface() -> None:
    """Business logic depends on the port, never on a concrete adapter."""
    for gateway in (
        build_model_gateway(_settings()),
        build_model_gateway(_genailab_settings()),
        MockModelGateway(),
        GenAILabAdapter(_genailab_settings()),
    ):
        assert isinstance(gateway, ModelGatewayInterface)


def test_all_four_capabilities_are_declared() -> None:
    gateway = build_model_gateway(_genailab_settings())
    for capability in (
        Capability.TEXT,
        Capability.MULTIMODAL,
        Capability.EMBEDDING,
        Capability.SPEECH,
    ):
        assert gateway.supports(capability), capability


async def test_unsupported_capability_is_refused() -> None:
    """A gateway must refuse rather than assume a model supports a modality."""

    class TextOnly(MockModelGateway):
        capabilities = frozenset({Capability.TEXT})

    gateway = ResilientGateway(TextOnly())
    with pytest.raises(ModelCapabilityError):
        await gateway.embed(EmbeddingRequest(model=MODEL, inputs=("a",)))


# ===========================================================================
# Adapter construction — no network, no leaks
# ===========================================================================
def test_genailab_adapter_constructs_without_network_access() -> None:
    adapter = GenAILabAdapter(_genailab_settings())
    assert adapter._client is None  # noqa: SLF001 - asserting lazy construction


async def test_genailab_healthcheck_makes_no_model_call() -> None:
    adapter = GenAILabAdapter(_genailab_settings())
    assert await adapter.healthcheck() is True
    assert adapter._client is None  # noqa: SLF001
    await adapter.close()


def test_adapter_does_not_leak_the_api_key() -> None:
    adapter = GenAILabAdapter(_genailab_settings())
    assert FAKE_CREDENTIAL not in repr(adapter)
    assert FAKE_CREDENTIAL not in repr(adapter._settings)  # noqa: SLF001


# ===========================================================================
# Mock behaviour — all four capabilities
# ===========================================================================
async def test_mock_text_generation_is_deterministic() -> None:
    gateway = MockModelGateway()
    first = await gateway.generate_text(_text_request())
    second = await gateway.generate_text(_text_request())

    assert first.content == second.content
    assert first.model == MODEL
    assert first.provider == "mock"
    assert gateway.call_count == 2


async def test_mock_multimodal_generation() -> None:
    gateway = MockModelGateway()
    request = MultimodalGenerationRequest(
        model=MODEL,
        messages=(
            Message(
                role=Role.USER,
                content=(
                    TextPart(text="Inspect this component"),
                    ImagePart(data=PNG_BYTES, media_type="image/png"),
                ),
            ),
        ),
    )
    response = await gateway.generate_multimodal(request)
    assert response.content
    assert len(gateway.calls_for("generate_multimodal")) == 1


async def test_mock_embeddings_return_one_vector_per_input() -> None:
    gateway = MockModelGateway(embedding_dimensions=6)
    response = await gateway.embed(
        EmbeddingRequest(model=MODEL, inputs=("alpha", "beta", "gamma"))
    )
    assert len(response.embeddings) == 3
    assert all(len(vector) == 6 for vector in response.embeddings)


async def test_mock_transcription() -> None:
    gateway = MockModelGateway(transcript="spindle vibration detected")
    response = await gateway.transcribe(
        SpeechTranscriptionRequest(model=MODEL, audio=b"\x00\x01", filename="clip.wav")
    )
    assert response.text == "spindle vibration detected"


async def test_mock_can_simulate_unreported_usage() -> None:
    """A provider reporting no usage must not yield fabricated token counts."""
    gateway = MockModelGateway(report_usage=False)
    response = await gateway.generate_text(_text_request())

    assert response.usage == TokenUsage()
    assert response.usage.input_tokens is None
    assert response.usage.provenance is UsageProvenance.UNAVAILABLE
    assert response.usage.is_reported is False


async def test_mock_closes() -> None:
    gateway = MockModelGateway()
    assert await gateway.healthcheck() is True
    await gateway.close()
    assert await gateway.healthcheck() is False


# ===========================================================================
# Request typing
# ===========================================================================
def test_model_is_required_and_never_defaulted() -> None:
    """Model identity comes from the registry, not a gateway default."""
    with pytest.raises(ValueError):
        TextGenerationRequest(messages=(Message(role=Role.USER, content="hi"),))  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        TextGenerationRequest(model="", messages=(Message(role=Role.USER, content="hi"),))


def test_requests_reject_empty_collections() -> None:
    with pytest.raises(ValueError):
        TextGenerationRequest(model=MODEL, messages=())
    with pytest.raises(ValueError):
        EmbeddingRequest(model=MODEL, inputs=())


def test_image_part_requires_an_image_media_type() -> None:
    with pytest.raises(ValueError):
        ImagePart(data=PNG_BYTES, media_type="application/pdf")


def test_message_detects_image_content() -> None:
    text_only = Message(role=Role.USER, content="hello")
    with_image = Message(
        role=Role.USER,
        content=(TextPart(text="look"), ImagePart(data=PNG_BYTES, media_type="image/png")),
    )
    assert text_only.has_image is False
    assert with_image.has_image is True


# ===========================================================================
# GenAILab encoding and decoding
# ===========================================================================
def test_multimodal_encoding_uses_a_data_uri() -> None:
    """Images travel as inline data, never as a URL for the provider to fetch."""
    adapter = GenAILabAdapter(_genailab_settings())
    encoded = adapter._encode_message(  # noqa: SLF001
        Message(
            role=Role.USER,
            content=(
                TextPart(text="describe"),
                ImagePart(data=PNG_BYTES, media_type="image/png"),
            ),
        )
    )
    parts = encoded["content"]
    assert parts[0] == {"type": "text", "text": "describe"}
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_plain_text_message_encodes_as_a_string() -> None:
    adapter = GenAILabAdapter(_genailab_settings())
    encoded = adapter._encode_message(Message(role=Role.SYSTEM, content="rules"))  # noqa: SLF001
    assert encoded == {"role": "system", "content": "rules"}


async def test_multimodal_request_without_an_image_is_refused() -> None:
    """A caller defect is surfaced, not silently downgraded to a text call."""
    adapter = GenAILabAdapter(_genailab_settings())
    request = MultimodalGenerationRequest(
        model=MODEL, messages=(Message(role=Role.USER, content="no image here"),)
    )
    with pytest.raises(GatewayBadRequestError):
        await adapter.generate_multimodal(request)


# --- usage extraction ------------------------------------------------------
def _usage_from(raw: Any) -> TokenUsage:
    adapter = GenAILabAdapter(_genailab_settings())
    return adapter._extract_usage(SimpleNamespace(usage=raw))  # noqa: SLF001


def test_usage_is_extracted_when_reported() -> None:
    usage = _usage_from(
        SimpleNamespace(prompt_tokens=11, completion_tokens=5, total_tokens=16)
    )
    assert usage.input_tokens == 11
    assert usage.output_tokens == 5
    assert usage.total_tokens == 16
    assert usage.provenance is UsageProvenance.ACTUAL


def test_absent_usage_is_unavailable_not_zero() -> None:
    """Absence is not a measurement. Zero would be a fabricated actual."""
    usage = _usage_from(None)
    assert usage.input_tokens is None
    assert usage.total_tokens is None
    assert usage.provenance is UsageProvenance.UNAVAILABLE


def test_usage_object_with_no_usable_fields_is_unavailable() -> None:
    usage = _usage_from(SimpleNamespace(something_else=1))
    assert usage.provenance is UsageProvenance.UNAVAILABLE


def test_partial_usage_is_reported_as_far_as_it_goes() -> None:
    """Missing sub-fields stay None rather than being derived."""
    usage = _usage_from(SimpleNamespace(prompt_tokens=7))
    assert usage.input_tokens == 7
    assert usage.output_tokens is None
    assert usage.total_tokens is None
    assert usage.provenance is UsageProvenance.ACTUAL


def test_non_numeric_usage_values_are_ignored() -> None:
    usage = _usage_from(SimpleNamespace(prompt_tokens="lots", completion_tokens=None))
    assert usage.provenance is UsageProvenance.UNAVAILABLE


# --- response decoding -----------------------------------------------------
class _StubCompletions:
    def __init__(self, result: Any) -> None:
        self._result = result

    async def create(self, **_: Any) -> Any:
        return self._result


def _adapter_with_chat(result: Any) -> GenAILabAdapter:
    adapter = GenAILabAdapter(_genailab_settings())
    adapter._client = SimpleNamespace(  # noqa: SLF001
        chat=SimpleNamespace(completions=_StubCompletions(result))
    )
    return adapter


async def test_response_with_no_choices_raises_rather_than_guessing() -> None:
    adapter = _adapter_with_chat(SimpleNamespace(choices=[], usage=None, model=MODEL))
    with pytest.raises(GatewayResponseError):
        await adapter.generate_text(_text_request())


async def test_response_with_null_content_raises() -> None:
    """A null content could be a tool call or a filter. Never coerced to ''."""
    adapter = _adapter_with_chat(
        SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content=None), finish_reason="length")
            ],
            usage=None,
            model=MODEL,
        )
    )
    with pytest.raises(GatewayResponseError):
        await adapter.generate_text(_text_request())


async def test_successful_decode_carries_usage_and_finish_reason() -> None:
    adapter = _adapter_with_chat(
        SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content="ok"), finish_reason="stop")
            ],
            usage=SimpleNamespace(prompt_tokens=3, completion_tokens=1, total_tokens=4),
            model="server-reported-model",
        )
    )
    response = await adapter.generate_text(_text_request())

    assert response.content == "ok"
    # The server's model id wins: it is what actually served the request.
    assert response.model == "server-reported-model"
    assert response.finish_reason == "stop"
    assert response.usage.provenance is UsageProvenance.ACTUAL
    assert response.latency_ms >= 0


# ===========================================================================
# Telemetry
# ===========================================================================
async def test_every_call_emits_telemetry() -> None:
    """AI_DEVELOPMENT_RULES.md section 8: executions must produce telemetry."""
    sink = CollectingTelemetrySink()
    gateway = ResilientGateway(MockModelGateway(), telemetry_sink=sink)

    await gateway.generate_text(_text_request(request_id="req-1", trace_id="trace-1"))

    record = sink.last
    assert record is not None
    assert record.outcome == "success"
    assert record.operation == "generate_text"
    assert record.model == MODEL
    assert record.request_id == "req-1"
    assert record.trace_id == "trace-1"
    assert record.attempts == 1
    assert record.retry_count == 0
    assert record.model_latency_ms is not None


async def test_telemetry_records_unavailable_usage_without_inventing_zeros() -> None:
    sink = CollectingTelemetrySink()
    gateway = ResilientGateway(MockModelGateway(report_usage=False), telemetry_sink=sink)

    await gateway.generate_text(_text_request())

    record = sink.last
    assert record is not None
    assert record.input_tokens is None
    assert record.total_tokens is None
    assert record.usage_provenance == UsageProvenance.UNAVAILABLE.value


async def test_telemetry_carries_no_prompt_content() -> None:
    """Prompts may hold sensitive data (SECURITY.md section 17)."""
    sink = CollectingTelemetrySink()
    gateway = ResilientGateway(MockModelGateway(), telemetry_sink=sink)
    secret_prompt = "serial number 12345 belongs to customer Contoso"

    await gateway.generate_text(
        TextGenerationRequest(
            model=MODEL, messages=(Message(role=Role.USER, content=secret_prompt),)
        )
    )

    record = sink.last
    assert record is not None
    serialized = str(record.as_dict())
    assert secret_prompt not in serialized
    assert "Contoso" not in serialized
    # Shape is recorded instead.
    assert record.request_shape["message_count"] == 1
    assert record.request_shape["approx_text_chars"] == len(secret_prompt)


async def test_telemetry_sink_failure_does_not_fail_the_call() -> None:
    class BrokenSink:
        async def record(self, telemetry: Any) -> None:
            raise RuntimeError("sink is down")

    gateway = ResilientGateway(MockModelGateway(), telemetry_sink=BrokenSink())
    response = await gateway.generate_text(_text_request())
    assert response.content
