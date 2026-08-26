"""Model registry service.

Owns registry business rules: capability filtering, enabled filtering, workload
compatibility and seed registration.

The governing rule for every filter here:

    **Unknown is not a match.**

Metadata columns are nullable because the documents forbid assuming price,
context window, quality or latency (ARCHITECTURE.md section 8). A NULL therefore
means "nobody has told us", and a requirement is only satisfied by a value that
demonstrably meets it. Routing an image to a model whose vision support is
unknown is a guess, and guessing here spends money and can produce a wrong
answer on a safety-relevant inspection.

Selection is *not* decided here. This narrows the candidate set; the routing
policy picks from it (ARCHITECTURE.md section 6, DATABASE_SCHEMA.md section 13).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from app.core.logging import get_logger
from app.db.models.registry import Capability, Modality, ModelRegistryEntry
from app.repositories.model_repository import ModelQuery, ModelRepository

logger = get_logger(__name__)


def _read_seed_file(seed_path: Path) -> str | None:
    """Read the seed file, or None when it is absent. Runs in a worker thread."""
    if not seed_path.is_file():
        return None
    return seed_path.read_text(encoding="utf-8")


class WorkloadType(StrEnum):
    """Workload types from DATABASE_SCHEMA.md section 9."""

    QUALITY_CHECK = "quality_check"
    PREDICTIVE_MAINTENANCE = "predictive_maintenance"
    SUPPLY_CHAIN = "supply_chain"


@dataclass(frozen=True, slots=True)
class WorkloadCapabilityProfile:
    """Capability requirements implied by a workload type.

    Taken from the technology table in AI_WORKFLOWS.md section 1 and the use
    cases in ARCHITECTURE.md section 2:

        Quality inspection       -> Multimodal AI / Vision
        Predictive maintenance   -> ML + LLM reasoning
        Supply-chain reasoning   -> LLM / agent

    These are *capability requirements*, not a routing decision. Which of the
    surviving candidates actually runs is the routing policy's call.
    """

    capabilities: tuple[Capability, ...]
    requires_vision: bool = False


#: Workload compatibility rules. Deterministic and configurable — no LLM is
#: consulted to decide something this simple (AI_DEVELOPMENT_RULES.md section 7).
WORKLOAD_PROFILES: dict[str, WorkloadCapabilityProfile] = {
    WorkloadType.QUALITY_CHECK: WorkloadCapabilityProfile(
        capabilities=(Capability.VISION, Capability.MULTIMODAL),
        requires_vision=True,
    ),
    WorkloadType.PREDICTIVE_MAINTENANCE: WorkloadCapabilityProfile(
        capabilities=(Capability.REASONING, Capability.TEXT),
    ),
    WorkloadType.SUPPLY_CHAIN: WorkloadCapabilityProfile(
        capabilities=(Capability.REASONING, Capability.TEXT),
    ),
}


@dataclass(frozen=True, slots=True)
class ModelPage:
    items: list[ModelRegistryEntry]
    total: int
    limit: int
    offset: int


class ModelRegistryService:
    """Registry queries and seed registration."""

    def __init__(self, repository: ModelRepository) -> None:
        self._repository = repository

    # ============================================================== lookups
    async def get(self, model_id: str) -> ModelRegistryEntry | None:
        return await self._repository.get_by_id(model_id)

    async def get_by_name(self, model_name: str) -> ModelRegistryEntry | None:
        return await self._repository.get_by_name(model_name)

    async def list_models(
        self,
        *,
        capability: Capability | None = None,
        modality: Modality | None = None,
        enabled: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> ModelPage:
        """List the registry with optional filters.

        ``enabled=None`` returns both enabled and disabled models, which is what
        an operator managing the registry needs to see. Execution paths must
        pass ``enabled=True`` — or better, use ``find_candidates``.
        """
        items, total = await self._repository.list(
            ModelQuery(
                capability=capability,
                modality=modality,
                enabled=enabled,
                limit=limit,
                offset=offset,
            )
        )
        return ModelPage(items=items, total=total, limit=limit, offset=offset)

    # ======================================================== compatibility
    async def find_candidates(
        self,
        *,
        capability: Capability | None = None,
        modality: Modality | None = None,
        requires_vision: bool = False,
        requires_tools: bool = False,
        requires_structured_output: bool = False,
        requires_embeddings: bool = False,
        min_context_tokens: int | None = None,
        min_quality_score: float | None = None,
        require_known_pricing: bool = False,
        limit: int = 50,
    ) -> list[ModelRegistryEntry]:
        """Models that can serve a request.

        Always constrained to enabled models. Every unmet-or-unknown requirement
        removes a candidate.
        """
        items, _ = await self._repository.list(
            ModelQuery(
                capability=capability,
                modality=modality,
                enabled=True,
                requires_vision=requires_vision,
                requires_tools=requires_tools,
                requires_structured_output=requires_structured_output,
                requires_embeddings=requires_embeddings,
                min_context_tokens=min_context_tokens,
                min_quality_score=min_quality_score,
                require_known_pricing=require_known_pricing,
                limit=limit,
            )
        )
        return items

    async def find_for_workload(
        self,
        workload_type: str,
        *,
        modality: Modality | None = None,
        min_context_tokens: int | None = None,
        min_quality_score: float | None = None,
        require_known_pricing: bool = False,
        limit: int = 50,
    ) -> list[ModelRegistryEntry]:
        """Enabled models compatible with a workload type.

        Returns an empty list for an unknown workload type rather than falling
        back to "everything". A typo must not silently widen the candidate set.
        """
        profile = WORKLOAD_PROFILES.get(workload_type)
        if profile is None:
            logger.warning(
                "model_registry_unknown_workload_type",
                extra={"workload_type": workload_type},
            )
            return []

        # One query per acceptable capability, merged. The alternative — an IN
        # clause — is possible, but this keeps ModelQuery a simple flat filter
        # and the registry is small.
        seen: dict[str, ModelRegistryEntry] = {}
        for capability in profile.capabilities:
            for entry in await self.find_candidates(
                capability=capability,
                modality=modality,
                requires_vision=profile.requires_vision,
                min_context_tokens=min_context_tokens,
                min_quality_score=min_quality_score,
                require_known_pricing=require_known_pricing,
                limit=limit,
            ):
                seen[entry.id] = entry

        return sorted(seen.values(), key=lambda entry: entry.model_name)[:limit]

    def is_compatible(
        self,
        entry: ModelRegistryEntry,
        workload_type: str,
        *,
        modality: Modality | None = None,
    ) -> bool:
        """In-memory compatibility check, mirroring ``find_for_workload``."""
        profile = WORKLOAD_PROFILES.get(workload_type)
        if profile is None or not entry.enabled:
            return False
        if entry.capability not in {str(c) for c in profile.capabilities}:
            return False
        if profile.requires_vision and entry.supports_vision is not True:
            return False
        return not (modality is not None and entry.modality != str(modality))

    # ================================================================= seed
    async def register_from_seed(self, seed_path: Path) -> int:
        """Insert seeded models that are not already registered.

        Existing rows are left untouched. An operator who has filled in pricing
        or a quality score must not have it reset by a redeploy, and the seed
        file cannot know those values anyway.

        Returns:
            The number of models inserted.
        """
        # Read off the event loop. Small file, but blocking I/O in an async
        # function stalls every other coroutine
        # (AI_DEVELOPMENT_RULES.md section 43).
        raw_text = await asyncio.to_thread(_read_seed_file, seed_path)
        if raw_text is None:
            logger.warning("model_registry_seed_missing", extra={"path": str(seed_path)})
            return 0

        raw = json.loads(raw_text)
        entries = raw.get("models", []) if isinstance(raw, dict) else raw

        inserted = 0
        for item in entries:
            model_name = item.get("model_name")
            if not model_name:
                logger.warning("model_registry_seed_entry_without_name")
                continue
            if await self._repository.exists(model_name):
                continue

            await self._repository.add(self._entry_from_seed(item))
            inserted += 1
            logger.info("model_registry_seeded", extra={"model_name": model_name})

        if inserted:
            logger.info("model_registry_seed_complete", extra={"inserted": inserted})
        return inserted

    @staticmethod
    def _entry_from_seed(item: dict[str, Any]) -> ModelRegistryEntry:
        """Build an entry from one seed record.

        Absent keys stay None. ``.get`` with no default is deliberate: there is
        no sensible default for a price or a context window, and inventing one
        is exactly what the documents forbid.
        """
        return ModelRegistryEntry(
            id=item.get("id") or str(uuid.uuid4()),
            model_name=item["model_name"],
            provider=item.get("provider"),
            capability=item["capability"],
            modality=item.get("modality"),
            input_cost=item.get("input_cost"),
            output_cost=item.get("output_cost"),
            cost_unit=item.get("cost_unit"),
            max_context_tokens=item.get("max_context_tokens"),
            supports_vision=item.get("supports_vision"),
            supports_tools=item.get("supports_tools"),
            supports_structured_output=item.get("supports_structured_output"),
            supports_embeddings=item.get("supports_embeddings"),
            quality_score=item.get("quality_score"),
            latency_score=item.get("latency_score"),
            risk_level=item.get("risk_level"),
            # The only field with a default: a seeded model is registered
            # enabled unless the seed says otherwise.
            enabled=item.get("enabled", True),
        )
