"""Seed package.

Exposes ``run_seed`` so callers do not need to import the internal module.

Usage (CLI):
    python -m app.db.seed

The seed is idempotent: running it twice is safe.
"""

from __future__ import annotations

import asyncio
import logging

from app.db.seed.demo_data import run_seed

logger = logging.getLogger(__name__)

__all__ = ["run_seed"]


async def _main() -> None:
    """CLI entry point — seed the database configured via DATABASE_URL."""
    import app.db.models  # noqa: F401 — register all ORM mappings
    from app.core.config import get_settings
    from app.db.session import Database

    settings = get_settings()
    db = Database(settings)
    await db.connect()
    async with db.session() as session:
        await run_seed(session)
    await db.disconnect()
    logger.info("seed_done")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_main())
