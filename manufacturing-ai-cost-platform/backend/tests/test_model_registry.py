"""Model registry tests: repository, service, filtering, seed.

The central rule under test throughout: **unknown metadata never satisfies a
requirement**. Nullable columns exist because the documents forbid assuming
price, context window, quality or latency, so a NULL means "nobody has told us"
and must not be read as a passing value.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.base import Base
from app.db.models.registry import Capability, Modality, ModelRegistryEntry
from app.db.session import Database
from app.repositories.model_repository import ModelQuery, ModelRepository
from app.services.model_registry import (
    WORKLOAD_PROFILES,
    ModelRegistryService,
    WorkloadType,
)

SEED_PATH = Path("app/db/seed/genailab_models.json")


@pytest_asyncio.fixture
async def session(settings: Settings) -> AsyncIterator[AsyncSession]:
    """A session against a fresh in-memory schema."""
    database = Database(settings)
    await database.connect()
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with database.session() as db_session:
        yield db_session

    await database.disconnect()


@pytest_asyncio.fixture
async def service(session: AsyncSession) -> ModelRegistryService:
    return ModelRegistryService(ModelRepository(session))


@pytest_asyncio.fixture
async def repository(session: AsyncSession) -> ModelRepository:
    return ModelRepository(session)


def make_entry(**overrides: object) -> ModelRegistryEntry:
    """An entry with everything unknown unless a test says otherwise.

    Defaults mirror reality: a freshly registered model has no pricing, no
    known context window and no measured quality.
    """
    defaults: dict[str, object] = {
        "id": str(uuid.uuid4()),
        "model_name": f"model-{uuid.uuid4().hex[:8]}",
        "provider": "genailab",
        "capability": Capability.TEXT.value,
        "modality": None,
        "input_cost": None,
        "output_cost": None,
        "cost_unit": None,
        "max_context_tokens": None,
        "supports_vision": None,
        "supports_tools": None,
        "supports_structured_output": None,
        "supports_embeddings": None,
        "quality_score": None,
        "latency_score": None,
        "risk_level": None,
        "enabled": True,
    }
    defaults.update(overrides)
    return ModelRegistryEntry(**defaults)  # type: ignore[arg-type]


# ===========================================================================
# Capability filtering
# ===========================================================================
async def test_capability_filter_returns_only_that_capability(
    repository: ModelRepository, service: ModelRegistryService
) -> None:
    await repository.add(make_entry(model_name="a-vision", capability="vision"))
    await repository.add(make_entry(model_name="b-text", capability="text"))
    await repository.add(make_entry(model_name="c-embedding", capability="embedding"))

    page = await service.list_models(capability=Capability.VISION)

    assert page.total == 1
    assert [m.model_name for m in page.items] == ["a-vision"]


async def test_no_capability_filter_returns_everything(
    repository: ModelRepository, service: ModelRegistryService
) -> None:
    await repository.add(make_entry(capability="vision"))
    await repository.add(make_entry(capability="text"))

    page = await service.list_models()
    assert page.total == 2


@pytest.mark.parametrize(
    "capability",
    [
        Capability.TEXT,
        Capability.VISION,
        Capability.MULTIMODAL,
        Capability.EMBEDDING,
        Capability.SPEECH,
        Capability.CODING,
        Capability.REASONING,
    ],
)
async def test_every_documented_capability_is_storable_and_filterable(
    repository: ModelRepository, service: ModelRegistryService, capability: Capability
) -> None:
    """All seven documented capability values round-trip."""
    await repository.add(make_entry(capability=capability.value))

    page = await service.list_models(capability=capability)
    assert page.total == 1
    assert page.items[0].capability == capability.value


# ===========================================================================
# Enabled filtering
# ===========================================================================
async def test_disabled_model_is_excluded_when_filtering_enabled(
    repository: ModelRepository, service: ModelRegistryService
) -> None:
    await repository.add(make_entry(model_name="live", enabled=True))
    await repository.add(make_entry(model_name="retired", enabled=False))

    page = await service.list_models(enabled=True)

    assert [m.model_name for m in page.items] == ["live"]


async def test_disabled_model_is_still_listed_without_the_filter(
    repository: ModelRepository, service: ModelRegistryService
) -> None:
    """A disabled model stays in the registry for historical telemetry joins."""
    await repository.add(make_entry(model_name="retired", enabled=False))

    page = await service.list_models()

    assert page.total == 1
    assert page.items[0].enabled is False


async def test_disabled_model_is_never_a_candidate(
    repository: ModelRepository, service: ModelRegistryService
) -> None:
    """Candidate search is for execution, so disabled models cannot appear."""
    await repository.add(
        make_entry(model_name="retired", capability="vision", supports_vision=True, enabled=False)
    )

    candidates = await service.find_candidates(capability=Capability.VISION)
    assert candidates == []


async def test_disabled_model_is_incompatible_with_every_workload(
    repository: ModelRepository, service: ModelRegistryService
) -> None:
    entry = make_entry(capability="vision", supports_vision=True, enabled=False)
    await repository.add(entry)

    assert service.is_compatible(entry, WorkloadType.QUALITY_CHECK) is False
    assert await service.find_for_workload(WorkloadType.QUALITY_CHECK) == []


# ===========================================================================
# Incompatible modality
# ===========================================================================
async def test_modality_filter_excludes_a_different_modality(
    repository: ModelRepository, service: ModelRegistryService
) -> None:
    await repository.add(make_entry(model_name="text-in", modality="text"))
    await repository.add(make_entry(model_name="image-in", modality="image"))

    page = await service.list_models(modality=Modality.IMAGE)

    assert [m.model_name for m in page.items] == ["image-in"]


async def test_unknown_modality_does_not_satisfy_a_modality_requirement(
    repository: ModelRepository, service: ModelRegistryService
) -> None:
    """A NULL modality is unknown, not a wildcard."""
    await repository.add(make_entry(model_name="unknown-modality", modality=None))

    candidates = await service.find_candidates(modality=Modality.IMAGE)
    assert candidates == []


async def test_image_workload_rejects_a_model_without_vision_support(
    repository: ModelRepository, service: ModelRegistryService
) -> None:
    """The core incompatible-modality case: text model, image workload."""
    text_model = make_entry(
        model_name="text-only", capability="text", modality="text", supports_vision=False
    )
    await repository.add(text_model)

    assert service.is_compatible(text_model, WorkloadType.QUALITY_CHECK) is False
    assert await service.find_for_workload(WorkloadType.QUALITY_CHECK) == []


async def test_unknown_vision_support_does_not_satisfy_a_vision_requirement(
    repository: ModelRepository, service: ModelRegistryService
) -> None:
    """Fail closed.

    Routing an image to a model whose vision support is unknown is a guess. It
    costs money and can produce a wrong answer on a safety-relevant inspection.
    """
    await repository.add(
        make_entry(model_name="maybe-vision", capability="vision", supports_vision=None)
    )

    assert await service.find_candidates(requires_vision=True) == []
    assert await service.find_for_workload(WorkloadType.QUALITY_CHECK) == []


async def test_documented_vision_model_is_compatible_with_quality_check(
    repository: ModelRepository, service: ModelRegistryService
) -> None:
    entry = make_entry(model_name="real-vision", capability="vision", supports_vision=True)
    await repository.add(entry)

    assert service.is_compatible(entry, WorkloadType.QUALITY_CHECK) is True
    found = await service.find_for_workload(WorkloadType.QUALITY_CHECK)
    assert [m.model_name for m in found] == ["real-vision"]


async def test_unknown_workload_type_returns_nothing_not_everything(
    repository: ModelRepository, service: ModelRegistryService
) -> None:
    """A typo must not silently widen the candidate set."""
    await repository.add(make_entry(capability="text"))
    await repository.add(make_entry(capability="vision", supports_vision=True))

    assert await service.find_for_workload("not_a_workload") == []


async def test_reasoning_workloads_accept_text_and_reasoning_models(
    repository: ModelRepository, service: ModelRegistryService
) -> None:
    await repository.add(make_entry(model_name="a-reasoner", capability="reasoning"))
    await repository.add(make_entry(model_name="b-text", capability="text"))
    await repository.add(make_entry(model_name="c-embed", capability="embedding"))

    for workload in (WorkloadType.PREDICTIVE_MAINTENANCE, WorkloadType.SUPPLY_CHAIN):
        found = await service.find_for_workload(workload)
        assert [m.model_name for m in found] == ["a-reasoner", "b-text"], workload


def test_every_workload_type_has_a_profile() -> None:
    """No workload may fall through to an undefined capability requirement."""
    for workload in WorkloadType:
        assert workload in WORKLOAD_PROFILES


# ===========================================================================
# Unknown pricing
# ===========================================================================
async def test_unknown_pricing_is_null_not_zero(
    repository: ModelRepository, service: ModelRegistryService
) -> None:
    """Zero would be a fabricated price and would read as 'free'."""
    await repository.add(make_entry(model_name="unpriced"))

    entry = await service.get_by_name("unpriced")
    assert entry is not None
    assert entry.input_cost is None
    assert entry.output_cost is None
    assert entry.cost_unit is None
    assert entry.has_known_pricing is False


async def test_partial_pricing_is_not_known_pricing(
    repository: ModelRepository, service: ModelRegistryService
) -> None:
    """A rate without its unit is not a price, and neither is half a pair."""
    await repository.add(
        make_entry(model_name="half-priced", input_cost=0.5, output_cost=None, cost_unit="1K")
    )
    await repository.add(
        make_entry(model_name="no-unit", input_cost=0.5, output_cost=1.0, cost_unit=None)
    )

    for name in ("half-priced", "no-unit"):
        entry = await service.get_by_name(name)
        assert entry is not None
        assert entry.has_known_pricing is False, name


async def test_complete_pricing_is_known(
    repository: ModelRepository, service: ModelRegistryService
) -> None:
    await repository.add(
        make_entry(
            model_name="priced", input_cost=0.15, output_cost=0.6, cost_unit="per_1k_tokens"
        )
    )
    entry = await service.get_by_name("priced")
    assert entry is not None
    assert entry.has_known_pricing is True


async def test_unpriced_models_are_excluded_when_pricing_is_required(
    repository: ModelRepository, service: ModelRegistryService
) -> None:
    """A cost-aware decision cannot be made against an unknown price."""
    await repository.add(make_entry(model_name="unpriced"))
    await repository.add(
        make_entry(
            model_name="priced", input_cost=0.1, output_cost=0.2, cost_unit="per_1k_tokens"
        )
    )

    candidates = await service.find_candidates(require_known_pricing=True)
    assert [m.model_name for m in candidates] == ["priced"]


async def test_unknown_context_window_does_not_satisfy_a_minimum(
    repository: ModelRepository, service: ModelRegistryService
) -> None:
    await repository.add(make_entry(model_name="unknown-ctx", max_context_tokens=None))
    await repository.add(make_entry(model_name="small-ctx", max_context_tokens=4096))
    await repository.add(make_entry(model_name="large-ctx", max_context_tokens=128000))

    candidates = await service.find_candidates(min_context_tokens=8000)
    assert [m.model_name for m in candidates] == ["large-ctx"]


async def test_unknown_quality_does_not_satisfy_a_minimum(
    repository: ModelRepository, service: ModelRegistryService
) -> None:
    await repository.add(make_entry(model_name="unmeasured", quality_score=None))
    await repository.add(make_entry(model_name="measured", quality_score=0.9))

    candidates = await service.find_candidates(min_quality_score=0.8)
    assert [m.model_name for m in candidates] == ["measured"]


# ===========================================================================
# Lookup
# ===========================================================================
async def test_lookup_by_id(
    repository: ModelRepository, service: ModelRegistryService
) -> None:
    entry = make_entry(model_name="findable")
    await repository.add(entry)

    found = await service.get(entry.id)
    assert found is not None
    assert found.model_name == "findable"


async def test_lookup_by_name(
    repository: ModelRepository, service: ModelRegistryService
) -> None:
    await repository.add(make_entry(model_name="by-name"))

    found = await service.get_by_name("by-name")
    assert found is not None
    assert found.model_name == "by-name"


async def test_lookup_of_a_missing_model_returns_none(
    service: ModelRegistryService,
) -> None:
    assert await service.get("does-not-exist") is None
    assert await service.get_by_name("does-not-exist") is None


# ===========================================================================
# Pagination
# ===========================================================================
async def test_pagination_reports_the_full_total(
    repository: ModelRepository, service: ModelRegistryService
) -> None:
    for index in range(7):
        await repository.add(make_entry(model_name=f"model-{index:02d}"))

    page = await service.list_models(limit=3, offset=3)

    assert page.total == 7
    assert len(page.items) == 3
    assert [m.model_name for m in page.items] == ["model-03", "model-04", "model-05"]


async def test_paging_is_stably_ordered(repository: ModelRepository) -> None:
    """Ordering must be deterministic or pages repeat and skip rows."""
    for name in ("zeta", "alpha", "mid"):
        await repository.add(make_entry(model_name=name))

    rows, _ = await repository.list(ModelQuery(limit=10))
    assert [r.model_name for r in rows] == ["alpha", "mid", "zeta"]


# ===========================================================================
# Seed registration
# ===========================================================================
async def test_seed_registers_the_documented_genailab_models(
    service: ModelRegistryService,
) -> None:
    inserted = await service.register_from_seed(SEED_PATH)

    assert inserted == 4
    page = await service.list_models(limit=100)
    names = {m.model_name for m in page.items}
    assert names == {
        "azure_ai/genailab-maas-Llama-3.2-90B-Vision-Instruct",
        "azure_ai/genailab-maas-Phi-3.5-vision-instruct",
        "azure/genailab-maas-text-embedding-3-large",
        "azure/genailab-maas-whisper",
    }


async def test_seeded_models_have_no_invented_metadata(
    service: ModelRegistryService,
) -> None:
    """Nothing undocumented is asserted about the supplied models."""
    await service.register_from_seed(SEED_PATH)
    page = await service.list_models(limit=100)

    for entry in page.items:
        assert entry.input_cost is None, entry.model_name
        assert entry.output_cost is None, entry.model_name
        assert entry.cost_unit is None, entry.model_name
        assert entry.max_context_tokens is None, entry.model_name
        assert entry.quality_score is None, entry.model_name
        assert entry.latency_score is None, entry.model_name
        assert entry.has_known_pricing is False, entry.model_name


async def test_seeded_capabilities_match_the_architecture_document(
    service: ModelRegistryService,
) -> None:
    await service.register_from_seed(SEED_PATH)

    by_name = {m.model_name: m for m in (await service.list_models(limit=100)).items}

    assert by_name["azure_ai/genailab-maas-Llama-3.2-90B-Vision-Instruct"].capability == "vision"
    assert by_name["azure_ai/genailab-maas-Phi-3.5-vision-instruct"].capability == "vision"
    assert by_name["azure/genailab-maas-text-embedding-3-large"].capability == "embedding"
    assert by_name["azure/genailab-maas-whisper"].capability == "speech"

    # Directly documented: listed under "Example vision models" / "Embedding model".
    assert by_name["azure_ai/genailab-maas-Phi-3.5-vision-instruct"].supports_vision is True
    assert by_name["azure/genailab-maas-text-embedding-3-large"].supports_embeddings is True


async def test_seeding_is_idempotent(service: ModelRegistryService) -> None:
    assert await service.register_from_seed(SEED_PATH) == 4
    assert await service.register_from_seed(SEED_PATH) == 0

    page = await service.list_models(limit=100)
    assert page.total == 4


async def test_seeding_never_overwrites_operator_supplied_metadata(
    repository: ModelRepository, service: ModelRegistryService
) -> None:
    """An operator's pricing must survive a redeploy."""
    await service.register_from_seed(SEED_PATH)

    entry = await service.get_by_name("azure/genailab-maas-whisper")
    assert entry is not None
    entry.input_cost = 0.006
    entry.output_cost = 0.0
    entry.cost_unit = "per_minute"
    await repository.session.flush()

    await service.register_from_seed(SEED_PATH)

    reloaded = await service.get_by_name("azure/genailab-maas-whisper")
    assert reloaded is not None
    assert reloaded.input_cost == 0.006
    assert reloaded.cost_unit == "per_minute"


async def test_missing_seed_file_is_reported_not_fatal(
    service: ModelRegistryService,
) -> None:
    assert await service.register_from_seed(Path("does/not/exist.json")) == 0


async def test_seed_entry_without_a_name_is_skipped(
    service: ModelRegistryService, tmp_path: Path
) -> None:
    seed = tmp_path / "seed.json"
    seed.write_text(
        json.dumps(
            {
                "models": [
                    {"capability": "text"},
                    {"model_name": "valid-one", "capability": "text"},
                ]
            }
        ),
        encoding="utf-8",
    )

    assert await service.register_from_seed(seed) == 1
    assert await service.get_by_name("valid-one") is not None
