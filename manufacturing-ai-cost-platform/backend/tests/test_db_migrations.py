"""Tests: Alembic migration chain.

Verifies:
  1. alembic upgrade head succeeds on a fresh in-memory database.
  2. All required tables exist after upgrade.
  3. alembic downgrade base succeeds (clean teardown).
  4. A second upgrade head succeeds (idempotency of schema on empty DB).

These tests use a synchronous SQLite connection because alembic's own
``MigrationContext`` is synchronous. The async engine wrapper is not used here;
we test the migration artefact itself, not the runtime session.

Environment variables are set before any import that reaches get_settings()
so that the test does not depend on a .env file.
"""

from __future__ import annotations

import os

# JWT_SECRET and GENAI_API_KEY must be set before any import of get_settings().
# DATABASE_URL is intentionally NOT set here — each fixture that runs alembic
# sets it to its own temp file, then restores the original on teardown.
os.environ.setdefault("JWT_SECRET", "test-migration-secret-not-a-credential")
os.environ.setdefault("GENAI_API_KEY", "test-placeholder")

import pytest
from alembic.config import Config

from alembic import command

REQUIRED_TABLES = {
    "models",
    "tenants",
    "users",
    "roles",
    "user_roles",
    "plants",
    "departments",
    "workloads",
    "agents",
    "tools",
    "budgets",
    "routing_policies",
    "usage_events",
    "cost_events",
    "forecasts",
    "anomalies",
    "optimization_recommendations",
    "approvals",
    "audit_events",
    "model_registry_history",
}


@pytest.fixture()
def alembic_cfg(tmp_path) -> Config:
    """Alembic config pointing at a fresh file-backed SQLite database.

    alembic/env.py reads DATABASE_URL from settings and overwrites
    sqlalchemy.url. So we set the DATABASE_URL env var to the aiosqlite test
    URL, then clear it on fixture teardown.

    We use a file DB (not :memory:) because aiosqlite creates a new thread
    per connection; :memory: connections would each get an empty database.
    """
    from pathlib import Path

    backend_dir = Path(__file__).parent.parent
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    cfg.set_main_option("path_separator", "os")

    db_path = tmp_path / "test_migration.db"
    async_url = f"sqlite+aiosqlite:///{db_path}"
    sync_url = f"sqlite:///{db_path}"

    # env.py reads this to set sqlalchemy.url; must be aiosqlite because
    # env.py uses async_engine_from_config.
    old_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = async_url
    # Also clear the settings cache so get_settings() re-reads the new URL.
    from app.core.config import get_settings
    get_settings.cache_clear()

    yield cfg, sync_url

    # Restore original environment.
    if old_url is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = old_url
    get_settings.cache_clear()


def test_upgrade_head_creates_all_tables(alembic_cfg) -> None:
    """alembic upgrade head must succeed and create every required table."""
    cfg, sync_url = alembic_cfg
    command.upgrade(cfg, "head")

    # Inspect with sync sqlite3 — no connection overhead, no async required.
    import sqlite3
    conn = sqlite3.connect(sync_url.replace("sqlite:///", ""))
    actual_tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    conn.close()

    assert REQUIRED_TABLES.issubset(actual_tables), (
        f"Missing tables after upgrade head: {REQUIRED_TABLES - actual_tables}"
    )


def test_downgrade_base_drops_all_tables(alembic_cfg) -> None:
    """alembic downgrade base must succeed cleanly, leaving no managed tables."""
    cfg, sync_url = alembic_cfg
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")

    import sqlite3
    conn = sqlite3.connect(sync_url.replace("sqlite:///", ""))
    remaining = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    conn.close()

    remaining -= {"alembic_version"}
    assert not remaining.intersection(REQUIRED_TABLES), (
        f"Tables still present after downgrade base: {remaining.intersection(REQUIRED_TABLES)}"
    )


def test_upgrade_head_after_downgrade_succeeds(alembic_cfg) -> None:
    """A full upgrade → downgrade → upgrade cycle must succeed without error."""
    cfg, sync_url = alembic_cfg
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")

    import sqlite3
    conn = sqlite3.connect(sync_url.replace("sqlite:///", ""))
    actual_tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    conn.close()

    assert REQUIRED_TABLES.issubset(actual_tables)


def test_usage_events_indexes_exist(alembic_cfg) -> None:
    """Verify the required usage_events indexes are created (DATABASE_SCHEMA.md §14)."""
    cfg, sync_url = alembic_cfg
    command.upgrade(cfg, "head")

    required_index_columns = {
        "timestamp", "tenant_id", "user_id", "plant_id",
        "workload_id", "agent_id", "model_id", "request_id",
    }

    import sqlite3
    conn = sqlite3.connect(sync_url.replace("sqlite:///", ""))
    index_info = conn.execute("PRAGMA index_list(usage_events)").fetchall()
    indexed_columns: set[str] = set()
    for idx in index_info:
        idx_name = idx[1]
        cols = conn.execute(f"PRAGMA index_info({idx_name})").fetchall()  # noqa: S608
        for col in cols:
            indexed_columns.add(col[2])
    conn.close()

    assert required_index_columns.issubset(indexed_columns), (
        f"Missing indexed columns on usage_events: {required_index_columns - indexed_columns}"
    )

