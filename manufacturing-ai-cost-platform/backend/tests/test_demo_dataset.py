"""The demo dataset must be deterministic, and honest about what it is.

Every judge demonstration runs off `run_seed`. Two things can quietly break it:

* the numbers drift, so the runbook's "expected output" column stops matching
  what a judge sees on screen; or
* a SIMULATED value gets promoted into a field that reads as measured spend.

The first wastes a demo. The second is a rule violation
(AI_DEVELOPMENT_RULES.md sections 10 and 41-42: never present simulated or
estimated numbers as actual). These tests pin both.

The literals below are duplicated from `demo_data` on purpose. Importing the
constants and recomputing would make the test agree with any drift; the point
is to notice the drift.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.governance import RoutingPolicy
from app.db.models.intelligence import Anomaly, OptimizationRecommendation
from app.db.models.registry import ModelRegistryEntry
from app.db.models.telemetry import CostEvent, UsageEvent
from app.db.seed.demo_data import (
    DEMO_TENANT_ID,
    MODEL_VISION_LLAMA,
    MODEL_VISION_PHI,
    run_seed,
)

pytestmark = pytest.mark.asyncio

#: 7 days x (40 quality_check + 12 predictive_maintenance + 8 supply_chain),
#: plus the final-day quality_check spike (40 -> 120, so +80).
EXPECTED_USAGE_EVENTS = 7 * (40 + 12 + 8) + 80


@pytest.fixture
async def seeded(db_session: AsyncSession) -> AsyncSession:
    await run_seed(db_session)
    await db_session.flush()
    return db_session


async def _count(session: AsyncSession, model: type) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


async def test_seed_volumes_are_deterministic(seeded: AsyncSession) -> None:
    """The runbook quotes these counts. They must not drift silently."""
    assert await _count(seeded, UsageEvent) == EXPECTED_USAGE_EVENTS
    assert await _count(seeded, CostEvent) == EXPECTED_USAGE_EVENTS
    assert await _count(seeded, Anomaly) == 1
    assert await _count(seeded, OptimizationRecommendation) == 1


async def test_seed_is_idempotent(seeded: AsyncSession) -> None:
    """`demo_cli seed` may be run twice before a demo without duplicating rows."""
    await run_seed(seeded)
    await seeded.flush()
    assert await _count(seeded, UsageEvent) == EXPECTED_USAGE_EVENTS


async def test_cost_spike_is_present_and_attributable(seeded: AsyncSession) -> None:
    """Step 5 of the demo story needs a spike, and step 6 needs a cause.

    The cause is visible in the data itself: on the spike day every request
    routed to the expensive vision model, including simple inspections.
    """
    by_model = dict(
        (
            await seeded.execute(
                select(UsageEvent.model_id, func.count())
                .where(UsageEvent.workload_id == "wl-plant-pune-quality_check")
                .group_by(UsageEvent.model_id)
            )
        ).all()
    )
    assert by_model[MODEL_VISION_PHI] == 6 * 40, "six baseline days on the cheap model"
    assert by_model[MODEL_VISION_LLAMA] == 120, "spike day on the expensive model"

    anomaly = (await seeded.execute(select(Anomaly))).scalar_one()
    assert anomaly.anomaly_type == "cost_spike"
    assert anomaly.actual_value > anomaly.expected_value
    assert anomaly.deviation_percent is not None and anomaly.deviation_percent > 0


async def test_no_simulated_value_is_presented_as_actual_spend(
    seeded: AsyncSession,
) -> None:
    """The hard rule: the demo must not fabricate realised cost.

    Every seeded cost event is an ESTIMATE. `actual_cost` stays NULL because no
    billing record exists for a run that never happened. A future change that
    populates it would make the dashboard report money that was never spent.
    """
    actuals = (await seeded.execute(select(CostEvent.actual_cost))).scalars().all()
    assert actuals, "no cost events seeded"
    assert all(value is None for value in actuals)

    provenances = (await seeded.execute(select(CostEvent.provenance))).scalars().all()
    assert set(provenances) == {"ESTIMATED"}


async def test_demo_narrative_rows_are_labelled_simulated(seeded: AsyncSession) -> None:
    """A judge reading the screen must be able to tell this is not real data."""
    anomaly = (await seeded.execute(select(Anomaly))).scalar_one()
    assert anomaly.reason.startswith("SIMULATED DEMO:")

    rec = (await seeded.execute(select(OptimizationRecommendation))).scalar_one()
    assert "SIMULATED DEMO:" in rec.recommendation_reason
    assert rec.status == "DRAFT", "the demo starts before human approval"


async def test_routing_policies_reference_registered_models(
    seeded: AsyncSession,
) -> None:
    """Policies point at models by id.

    Model ids used to be generated per seed, which left every policy pointing at
    a model that no longer existed and made the demo impossible to run.
    """
    known = set(
        (await seeded.execute(select(ModelRegistryEntry.id))).scalars().all()
    )
    assert MODEL_VISION_LLAMA in known and MODEL_VISION_PHI in known

    referenced = (
        (
            await seeded.execute(
                select(RoutingPolicy.selected_model_id).where(
                    RoutingPolicy.selected_model_id.is_not(None)
                )
            )
        )
        .scalars()
        .all()
    )
    assert referenced, "no policy selects a model"
    assert set(referenced) <= known


async def test_one_active_policy_per_routing_key(seeded: AsyncSession) -> None:
    """The orchestrator resolves a single active policy per (workload, complexity).

    Two ACTIVE rows for one key make that lookup return nothing, and the
    execution plan reports a null policy version.
    """
    duplicates = (
        await seeded.execute(
            select(
                RoutingPolicy.workload_type,
                RoutingPolicy.complexity,
                func.count(),
            )
            .where(
                RoutingPolicy.tenant_id == DEMO_TENANT_ID,
                RoutingPolicy.status.in_(["ACTIVE", "CANARY"]),
            )
            .group_by(RoutingPolicy.workload_type, RoutingPolicy.complexity)
            .having(func.count() > 1)
        )
    ).all()
    assert duplicates == []
