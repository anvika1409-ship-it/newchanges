"""Demo dataset command line.

One command to put the platform into a known state before a demonstration, and
one to return it there afterwards.

    python -m app.db.seed.demo_cli seed      # create schema + demo dataset
    python -m app.db.seed.demo_cli reset     # wipe, recreate, reseed
    python -m app.db.seed.demo_cli status    # what is currently loaded

Everything the demo dataset contains is SIMULATED. It describes a fictional
enterprise ("ACME Manufacturing (DEMO)") and must never be presented as real
operational data (AI_DEVELOPMENT_RULES.md sections 41 and 42).

``reset`` is destructive by design — that is the point of a demo reset — so it
refuses to run when ``APP_ENV=production``. A judge demo runs against a
throwaway database; a production database must never be wiped by a convenience
command.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import func, select

from app.core.config import AppEnv, get_settings
from app.core.logging import configure_logging, get_logger
from app.db.base import Base
from app.db.models.governance import RoutingPolicy
from app.db.models.intelligence import Anomaly, Forecast, OptimizationRecommendation
from app.db.models.registry import ModelRegistryEntry
from app.db.models.telemetry import CostEvent, UsageEvent
from app.db.seed.demo_data import DEMO_TENANT_ID, run_seed
from app.db.session import Database

logger = get_logger(__name__)

#: Reported by `status`, in the order the demo story visits them.
_COUNTED = [
    ("models", ModelRegistryEntry),
    ("routing_policies", RoutingPolicy),
    ("usage_events", UsageEvent),
    ("cost_events", CostEvent),
    ("anomalies", Anomaly),
    ("forecasts", Forecast),
    ("recommendations", OptimizationRecommendation),
]


def _refuse_in_production() -> None:
    settings = get_settings()
    if settings.app_env is AppEnv.PRODUCTION:
        raise SystemExit(
            "Refusing to modify the demo dataset while APP_ENV=production. "
            "This command drops tables."
        )


async def _seed(database: Database, *, recreate: bool) -> None:
    if recreate:
        async with database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)
        logger.info("demo_schema_recreated")
    else:
        async with database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async with database.session() as session:
        await run_seed(session)


async def _status(database: Database) -> None:
    async with database.session() as session:
        print(f"  tenant: {DEMO_TENANT_ID}")
        for label, model in _COUNTED:
            count = (
                await session.execute(select(func.count()).select_from(model))
            ).scalar_one()
            print(f"  {label:<20} {count}")


async def _run(command: str) -> None:
    settings = get_settings()
    configure_logging(settings)
    database = Database(settings)
    await database.connect()
    try:
        if command == "seed":
            await _seed(database, recreate=False)
            print("Demo dataset seeded. All values are SIMULATED.")
            await _status(database)
        elif command == "reset":
            _refuse_in_production()
            await _seed(database, recreate=True)
            print("Demo dataset reset. All values are SIMULATED.")
            await _status(database)
        elif command == "status":
            await _status(database)
    finally:
        await database.disconnect()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.db.seed.demo_cli",
        description="Manage the SIMULATED demo dataset.",
    )
    parser.add_argument(
        "command",
        choices=["seed", "reset", "status"],
        help="seed: create if absent. reset: drop and recreate. status: counts.",
    )
    args = parser.parse_args(argv)

    if args.command == "reset":
        _refuse_in_production()

    asyncio.run(_run(args.command))
    return 0


if __name__ == "__main__":  # pragma: no cover - entry point
    sys.exit(main())
