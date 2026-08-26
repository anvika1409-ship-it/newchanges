"""Model registry persistence.

Keeps SQLite behind SQLAlchemy: callers pass filter criteria, never SQL
(AI_DEVELOPMENT_RULES.md section 16). Filtering happens in the query rather than
in Python so a large registry does not have to be loaded to answer a question.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Select, func, select

from app.db.models.registry import Capability, Modality, ModelRegistryEntry
from app.repositories.base import AsyncRepository


@dataclass(frozen=True, slots=True)
class ModelQuery:
    """Filter criteria for a registry lookup.

    ``None`` means "do not filter on this", which is distinct from filtering on
    a NULL column value.
    """

    capability: Capability | None = None
    modality: Modality | None = None
    enabled: bool | None = None

    #: Feature requirements. A model whose support is unknown (NULL) does not
    #: satisfy a requirement — see the service layer for why.
    requires_vision: bool = False
    requires_tools: bool = False
    requires_structured_output: bool = False
    requires_embeddings: bool = False

    #: Minimum usable context. Models with an unknown window are excluded,
    #: because "unknown" cannot be shown to be large enough.
    min_context_tokens: int | None = None

    #: Minimum measured quality. Unknown quality is excluded for the same reason.
    min_quality_score: float | None = None

    #: Only models whose pricing is fully known.
    require_known_pricing: bool = False

    limit: int = 50
    offset: int = 0


class ModelRepository(AsyncRepository[ModelRegistryEntry]):
    """Read and write access to the ``models`` table."""

    async def get_by_id(self, model_id: str) -> ModelRegistryEntry | None:
        return await self.session.get(ModelRegistryEntry, model_id)

    async def get_by_name(self, model_name: str) -> ModelRegistryEntry | None:
        result = await self.session.execute(
            select(ModelRegistryEntry).where(ModelRegistryEntry.model_name == model_name)
        )
        return result.scalar_one_or_none()

    async def list(self, query: ModelQuery) -> tuple[list[ModelRegistryEntry], int]:
        """Return a page of matching models and the total match count.

        The count is computed with the same predicates but without the page
        window, so pagination reports the full result size.
        """
        statement = self._apply_filters(select(ModelRegistryEntry), query)

        count_statement = self._apply_filters(
            select(func.count()).select_from(ModelRegistryEntry), query
        )
        total = (await self.session.execute(count_statement)).scalar_one()

        # Stable ordering so pagination does not repeat or skip rows.
        statement = (
            statement.order_by(ModelRegistryEntry.model_name)
            .limit(query.limit)
            .offset(query.offset)
        )
        rows = (await self.session.execute(statement)).scalars().all()
        return list(rows), int(total)

    async def add(self, entry: ModelRegistryEntry) -> ModelRegistryEntry:
        self.session.add(entry)
        await self.session.flush()
        return entry

    async def exists(self, model_name: str) -> bool:
        result = await self.session.execute(
            select(func.count())
            .select_from(ModelRegistryEntry)
            .where(ModelRegistryEntry.model_name == model_name)
        )
        return bool(result.scalar_one())

    # ------------------------------------------------------------- internals
    @staticmethod
    def _apply_filters(statement: Select, query: ModelQuery) -> Select:
        model = ModelRegistryEntry

        if query.capability is not None:
            statement = statement.where(model.capability == str(query.capability))
        if query.modality is not None:
            statement = statement.where(model.modality == str(query.modality))
        if query.enabled is not None:
            statement = statement.where(model.enabled.is_(query.enabled))

        # `is_(True)` rather than a truthiness test: a NULL column must not
        # satisfy a requirement, and `== True` would not match NULL either but
        # reads ambiguously.
        if query.requires_vision:
            statement = statement.where(model.supports_vision.is_(True))
        if query.requires_tools:
            statement = statement.where(model.supports_tools.is_(True))
        if query.requires_structured_output:
            statement = statement.where(model.supports_structured_output.is_(True))
        if query.requires_embeddings:
            statement = statement.where(model.supports_embeddings.is_(True))

        if query.min_context_tokens is not None:
            statement = statement.where(
                model.max_context_tokens.is_not(None),
                model.max_context_tokens >= query.min_context_tokens,
            )
        if query.min_quality_score is not None:
            statement = statement.where(
                model.quality_score.is_not(None),
                model.quality_score >= query.min_quality_score,
            )
        if query.require_known_pricing:
            statement = statement.where(
                model.input_cost.is_not(None),
                model.output_cost.is_not(None),
                model.cost_unit.is_not(None),
            )
        return statement
