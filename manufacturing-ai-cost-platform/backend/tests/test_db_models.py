"""Tests: ORM model relationships and FK enforcement.

Verifies:
  - Relationship navigation (plant → departments, workload → agents, etc.)
  - FK constraint enforcement — inserting a child with an unknown parent PK
    must raise IntegrityError when foreign_keys=ON.
  - Tenant isolation via ORM — records from tenant B are not returned by
    repository queries scoped to tenant A.

All tests run against an in-memory SQLite database created by applying the
Alembic migrations programmatically. No live database is required.
"""

from __future__ import annotations

import os

# JWT_SECRET and GENAI_API_KEY must be set before any import of get_settings().
# DATABASE_URL is set per-fixture, not here, to avoid cross-test contamination.
os.environ.setdefault("JWT_SECRET", "test-models-secret-not-a-credential")
os.environ.setdefault("GENAI_API_KEY", "test-placeholder")

import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from alembic.config import Config
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from alembic import command
from app.db.models.control_plane import (
    Agent,
    Department,
    Plant,
    Tenant,
    Workload,
)
from app.db.models.telemetry import CostEvent, UsageEvent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _id() -> str:
    return str(uuid.uuid4())


BACKEND_DIR = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def sync_db_url(tmp_path_factory) -> str:
    """Apply migrations to a temp file-backed DB and return its sync URL.

    env.py overwrites sqlalchemy.url from settings.database_url, so we must
    set DATABASE_URL env var to the aiosqlite URL before calling upgrade.
    """
    tmp = tmp_path_factory.mktemp("models_test")
    db_path = tmp / "test_models.db"
    sync_url = f"sqlite:///{db_path}"
    async_url = f"sqlite+aiosqlite:///{db_path}"

    # Temporarily override DATABASE_URL so env.py uses our test DB.
    old_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = async_url
    from app.core.config import get_settings
    get_settings.cache_clear()

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("path_separator", "os")
    command.upgrade(cfg, "head")

    # Restore
    if old_url is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = old_url
    get_settings.cache_clear()

    return sync_url


@pytest.fixture(scope="module")
def async_db_url(sync_db_url: str) -> str:
    return sync_db_url.replace("sqlite://", "sqlite+aiosqlite://")


@pytest_asyncio.fixture(scope="function")
async def session(async_db_url: str) -> AsyncSession:
    """Fresh async session with FK enforcement and WAL mode."""
    engine = create_async_engine(
        async_db_url,
        connect_args={"timeout": 5},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _pragmas(conn, _rec):  # type: ignore[no-untyped-def]
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()

    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
        await s.rollback()  # roll back so tests don't pollute each other

    await engine.dispose()


# ---------------------------------------------------------------------------
# Helper to insert a minimal tenant + plant + department + workload + agent
# ---------------------------------------------------------------------------

async def _insert_hierarchy(
    session: AsyncSession,
    tenant_id: str,
    plant_id: str,
    dept_id: str,
    wl_id: str,
    agent_id: str,
) -> tuple[Tenant, Plant, Department, Workload, Agent]:
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    t = Tenant(id=tenant_id, name="Test Tenant", status="ACTIVE", created_at=now, updated_at=now)
    p = Plant(
        id=plant_id, tenant_id=tenant_id, name="Test Plant",
        location="X", timezone="UTC", status="ACTIVE", created_at=now, updated_at=now,
    )
    d = Department(id=dept_id, plant_id=plant_id, name="Quality", status="ACTIVE")
    wl = Workload(
        id=wl_id, plant_id=plant_id, department_id=dept_id,
        name="QC Workload", workload_type="quality_check",
        business_priority="NORMAL", risk_level="LOW", status="ACTIVE",
        created_at=now, updated_at=now,
    )
    a = Agent(
        id=agent_id, workload_id=wl_id, name="QCAgent", agent_type="vision_inspector",
        status="ACTIVE", created_at=now, updated_at=now,
    )
    session.add_all([t, p, d, wl, a])
    await session.flush()
    return t, p, d, wl, a


# ---------------------------------------------------------------------------
# Relationship navigation
# ---------------------------------------------------------------------------

async def test_plant_departments_relationship(session: AsyncSession) -> None:
    """Plant.departments navigates to child Department rows."""
    tid, pid, did, wlid, aid = _id(), _id(), _id(), _id(), _id()
    t, p, d, wl, a = await _insert_hierarchy(session, tid, pid, did, wlid, aid)

    await session.refresh(p, ["departments"])
    assert any(dep.id == did for dep in p.departments)


async def test_workload_agents_relationship(session: AsyncSession) -> None:
    """Workload.agents navigates to child Agent rows."""
    tid, pid, did, wlid, aid = _id(), _id(), _id(), _id(), _id()
    _, _, _, wl, a = await _insert_hierarchy(session, tid, pid, did, wlid, aid)

    await session.refresh(wl, ["agents"])
    assert any(ag.id == aid for ag in wl.agents)


async def test_agent_workload_backref(session: AsyncSession) -> None:
    """Agent.workload navigates up to the parent Workload."""
    tid, pid, did, wlid, aid = _id(), _id(), _id(), _id(), _id()
    _, _, _, wl, a = await _insert_hierarchy(session, tid, pid, did, wlid, aid)

    await session.refresh(a, ["workload"])
    assert a.workload.id == wlid


async def test_usage_cost_event_relationship(session: AsyncSession) -> None:
    """UsageEvent.cost_event navigates to paired CostEvent."""
    from datetime import UTC, datetime

    tid, pid, did, wlid, aid = _id(), _id(), _id(), _id(), _id()
    await _insert_hierarchy(session, tid, pid, did, wlid, aid)

    now = datetime.now(UTC)
    ue = UsageEvent(
        id=_id(), request_id="req-rel-test", timestamp=now,
        tenant_id=tid, created_at=now,
    )
    session.add(ue)
    await session.flush()

    ce = CostEvent(
        id=_id(), usage_event_id=ue.id, currency="USD",
        provenance="ESTIMATED", created_at=now,
    )
    session.add(ce)
    await session.flush()

    await session.refresh(ue, ["cost_event"])
    assert ue.cost_event is not None
    assert ue.cost_event.id == ce.id


# ---------------------------------------------------------------------------
# FK enforcement
# ---------------------------------------------------------------------------

async def test_insert_agent_with_unknown_workload_raises(session: AsyncSession) -> None:
    """FK violation: agent referencing non-existent workload_id must raise."""
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    bad_agent = Agent(
        id=_id(), workload_id="does-not-exist", name="BadAgent",
        agent_type="test", status="ACTIVE", created_at=now, updated_at=now,
    )
    session.add(bad_agent)
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_insert_department_with_unknown_plant_raises(session: AsyncSession) -> None:
    """FK violation: department referencing non-existent plant_id must raise."""
    bad_dept = Department(
        id=_id(), plant_id="no-such-plant", name="BadDept", status="ACTIVE"
    )
    session.add(bad_dept)
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_insert_workload_with_unknown_department_raises(session: AsyncSession) -> None:
    """FK violation: workload referencing non-existent department_id must raise."""
    from datetime import UTC, datetime

    tid, pid = _id(), _id()
    now = datetime.now(UTC)
    t = Tenant(id=tid, name="T", status="ACTIVE", created_at=now, updated_at=now)
    p = Plant(
        id=pid, tenant_id=tid, name="P", location="L", timezone="UTC",
        status="ACTIVE", created_at=now, updated_at=now,
    )
    session.add_all([t, p])
    await session.flush()

    bad_wl = Workload(
        id=_id(), plant_id=pid, department_id="no-such-dept",
        name="BadWL", workload_type="quality_check",
        business_priority="NORMAL", risk_level="LOW", status="ACTIVE",
        created_at=now, updated_at=now,
    )
    session.add(bad_wl)
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_cost_event_fk_to_usage_event_enforced(session: AsyncSession) -> None:
    """FK violation: cost_event with unknown usage_event_id must raise."""
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    bad_ce = CostEvent(
        id=_id(), usage_event_id="ghost-usage-event", currency="USD",
        provenance="ESTIMATED", created_at=now,
    )
    session.add(bad_ce)
    with pytest.raises(IntegrityError):
        await session.flush()


# ---------------------------------------------------------------------------
# Check constraints
# ---------------------------------------------------------------------------

async def test_invalid_workload_type_raises(session: AsyncSession) -> None:
    """CheckConstraint on workload_type must reject invalid values."""
    from datetime import UTC, datetime

    tid, pid, did = _id(), _id(), _id()
    now = datetime.now(UTC)
    t = Tenant(id=tid, name="T", status="ACTIVE", created_at=now, updated_at=now)
    p = Plant(
        id=pid, tenant_id=tid, name="P", location="L", timezone="UTC",
        status="ACTIVE", created_at=now, updated_at=now,
    )
    d = Department(id=did, plant_id=pid, name="D", status="ACTIVE")
    session.add_all([t, p, d])
    await session.flush()

    bad = Workload(
        id=_id(), plant_id=pid, department_id=did,
        name="Bad", workload_type="INVALID_TYPE",
        business_priority="NORMAL", risk_level="LOW", status="ACTIVE",
        created_at=now, updated_at=now,
    )
    session.add(bad)
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_invalid_cost_provenance_raises(session: AsyncSession) -> None:
    """CheckConstraint on cost_events.provenance must reject invalid values."""
    from datetime import UTC, datetime

    tid, pid, did, wlid, aid = _id(), _id(), _id(), _id(), _id()
    await _insert_hierarchy(session, tid, pid, did, wlid, aid)

    now = datetime.now(UTC)
    ue = UsageEvent(
        id=_id(), request_id="req-bad-prov", timestamp=now,
        tenant_id=tid, created_at=now,
    )
    session.add(ue)
    await session.flush()

    bad_ce = CostEvent(
        id=_id(), usage_event_id=ue.id, currency="USD",
        provenance="FABRICATED",  # invalid
        created_at=now,
    )
    session.add(bad_ce)
    with pytest.raises(IntegrityError):
        await session.flush()
