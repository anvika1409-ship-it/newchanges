"""Model registry response schemas.

Mirrors the ``Model`` and ``ModelList`` schemas in API_CONTRACT.yaml field for
field. SQLAlchemy entities are never returned directly through the API
(AI_DEVELOPMENT_RULES.md section 16).

Unknown metadata is serialized as ``null``, not omitted and not zero. A consumer
must be able to tell "we do not know this model's price" from "this model is
free" (AI_DEVELOPMENT_RULES.md sections 41 and 42).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.db.models.registry import ModelRegistryEntry


class PageInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    total: int
    limit: int
    offset: int


class ModelResponse(BaseModel):
    """One registry entry."""

    model_config = ConfigDict(frozen=True)

    id: str
    model_name: str
    provider: str | None = None
    capability: str
    modality: str | None = None

    # --- cost metadata -----------------------------------------------------
    input_cost: float | None = None
    output_cost: float | None = None
    cost_unit: str | None = None

    #: True only when both rates and the unit are present. Lets a client show
    #: "pricing unknown" without re-deriving the rule.
    has_known_pricing: bool = False

    # --- context metadata --------------------------------------------------
    max_context_tokens: int | None = None

    # --- feature support: null means unknown, not unsupported --------------
    supports_vision: bool | None = None
    supports_tools: bool | None = None
    supports_structured_output: bool | None = None
    supports_embeddings: bool | None = None

    # --- measured performance ----------------------------------------------
    quality_score: float | None = None
    latency_score: float | None = None

    risk_level: str | None = None
    enabled: bool

    @classmethod
    def from_entry(cls, entry: ModelRegistryEntry) -> ModelResponse:
        return cls(
            id=entry.id,
            model_name=entry.model_name,
            provider=entry.provider,
            capability=entry.capability,
            modality=entry.modality,
            input_cost=entry.input_cost,
            output_cost=entry.output_cost,
            cost_unit=entry.cost_unit,
            has_known_pricing=entry.has_known_pricing,
            max_context_tokens=entry.max_context_tokens,
            supports_vision=entry.supports_vision,
            supports_tools=entry.supports_tools,
            supports_structured_output=entry.supports_structured_output,
            supports_embeddings=entry.supports_embeddings,
            quality_score=entry.quality_score,
            latency_score=entry.latency_score,
            risk_level=entry.risk_level,
            enabled=entry.enabled,
        )


class ModelListResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[ModelResponse] = Field(default_factory=list)
    page: PageInfo
