"""Model registry ORM mapping.

Implements the ``models`` table from DATABASE_SCHEMA.md section 11 exactly. No
column is added or renamed here; a schema change would go to that document
first (AI_DEVELOPMENT_RULES.md section 35).

Almost every metadata column is nullable, and deliberately so. Price, context
window, quality and latency are configuration supplied by an operator
(ARCHITECTURE.md section 8). When a value is unknown it stays NULL — it is never
defaulted to zero or guessed from the model name. Downstream filtering treats
NULL as "unknown", which never satisfies a requirement.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Capability(StrEnum):
    """The model's primary role (DATABASE_SCHEMA.md section 11)."""

    TEXT = "text"
    VISION = "vision"
    MULTIMODAL = "multimodal"
    EMBEDDING = "embedding"
    SPEECH = "speech"
    CODING = "coding"
    REASONING = "reasoning"


class Modality(StrEnum):
    """Input types the model accepts.

    A different vocabulary from ``Capability`` on purpose: ``vision`` is a role,
    ``image`` is an input type. They are not interchangeable.
    """

    TEXT = "text"
    IMAGE = "image"
    MULTIMODAL = "multimodal"
    STRUCTURED = "structured"


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ModelRegistryEntry(Base):
    """One registered model.

    Named ``ModelRegistryEntry`` rather than ``Model`` because "model" is
    hopelessly overloaded here — SQLAlchemy models, Pydantic models and ML
    models would all collide.
    """

    __tablename__ = "models"
    __table_args__ = (
        CheckConstraint(
            "capability IN ('text','vision','multimodal','embedding','speech',"
            "'coding','reasoning')",
            name="capability_valid",
        ),
        CheckConstraint(
            "modality IS NULL OR modality IN ('text','image','multimodal','structured')",
            name="modality_valid",
        ),
        CheckConstraint(
            "risk_level IS NULL OR risk_level IN ('LOW','MEDIUM','HIGH','CRITICAL')",
            name="risk_level_valid",
        ),
        # Cost is a rate, never negative. Guards a data-entry slip that would
        # otherwise flow straight into cost reporting.
        CheckConstraint("input_cost IS NULL OR input_cost >= 0", name="input_cost_non_negative"),
        CheckConstraint("output_cost IS NULL OR output_cost >= 0", name="output_cost_non_negative"),
        CheckConstraint(
            "max_context_tokens IS NULL OR max_context_tokens > 0",
            name="max_context_tokens_positive",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    model_name: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    provider: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    capability: Mapped[str] = mapped_column(String, nullable=False, index=True)
    modality: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    # --- cost metadata: configuration, never inferred ----------------------
    input_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    output_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: The unit the costs are expressed in, e.g. per 1K tokens. Meaningless
    #: without it, which is why cost is only usable when both are present.
    cost_unit: Mapped[str | None] = mapped_column(String, nullable=True)

    # --- context metadata --------------------------------------------------
    max_context_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # --- feature support: NULL means unknown, not False --------------------
    supports_vision: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    supports_tools: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    supports_structured_output: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    supports_embeddings: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # --- measured performance: populated from telemetry, not assumed -------
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    risk_level: Mapped[str | None] = mapped_column(String, nullable=True)

    #: Disabled models stay in the registry for historical telemetry joins.
    #: They are never selected for execution.
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    @property
    def has_known_pricing(self) -> bool:
        """Whether cost can be computed for this model at all.

        Both rates and the unit must be present. A rate without its unit is not
        pricing, and treating a missing rate as zero would report fabricated
        spend (AI_DEVELOPMENT_RULES.md section 10).
        """
        return (
            self.input_cost is not None
            and self.output_cost is not None
            and bool(self.cost_unit)
        )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"ModelRegistryEntry(id={self.id!r}, model_name={self.model_name!r}, "
            f"capability={self.capability!r}, enabled={self.enabled!r})"
        )
