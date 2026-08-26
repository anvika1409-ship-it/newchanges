"""Shared test fixtures.

Tests never touch a live LLM API or a real Redis server
(AI_DEVELOPMENT_RULES.md section 25). The model gateway is the mock
implementation and the cache is a stub.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.cache.redis_client import NullCache
from app.core.config import AppEnv, AuthMode, ModelGatewayProvider, Settings
from app.main import create_app

# Test-only signing material. Not a credential: it signs nothing outside this
# suite, and the development adapter it drives is refused in production.
TEST_JWT_SECRET = "test-suite-signing-material-not-a-credential"
TEST_ISSUER = "test-issuer"
TEST_AUDIENCE = "test-audience"


class FailingCache(NullCache):
    """Cache stub whose health probe fails, for readiness tests."""

    async def healthcheck(self) -> bool:
        return False


@pytest.fixture
def settings() -> Settings:
    """Isolated settings.

    Values are passed explicitly rather than read from a .env file so a
    developer's local environment cannot change test outcomes.
    """
    return Settings(
        app_env=AppEnv.DEVELOPMENT,
        debug=True,
        log_format="console",
        log_level="WARNING",
        # In-memory SQLite: no file is created and nothing leaks between tests.
        database_url="sqlite+aiosqlite:///:memory:",
        redis_enabled=False,
        model_gateway_provider=ModelGatewayProvider.MOCK,
        auth_mode=AuthMode.DEVELOPMENT,
        jwt_secret=TEST_JWT_SECRET,
        jwt_issuer=TEST_ISSUER,
        jwt_audience=TEST_AUDIENCE,
        cors_allow_origins=["http://localhost:5173"],
        genai_api_key="",
    )


@pytest.fixture
def app(settings: Settings):
    """Application instance built from the isolated settings."""
    return create_app(settings)


@pytest_asyncio.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    """HTTP client that drives the app through its real lifespan.

    ``LifespanManager`` is not used; ASGITransport does not run lifespan, so the
    startup/shutdown hooks are exercised explicitly here.
    """
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as http_client:
            yield http_client


@pytest.fixture
def api_prefix(settings: Settings) -> str:
    return settings.api_v1_prefix


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    """Keep the cached global settings from leaking across tests."""
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Migrated database fixtures
#
# Cost and budget tests need real SQL — aggregation, grouping and date bucketing
# are the behaviour under test, so a stubbed repository would test nothing. The
# schema is built by running the Alembic migrations rather than
# `metadata.create_all`, so the tests exercise the same DDL production gets,
# CHECK constraints included.
# ---------------------------------------------------------------------------

BACKEND_DIR = Path(__file__).parent.parent


@pytest.fixture(scope="session")
def migrated_db_url(tmp_path_factory: pytest.TempPathFactory) -> str:
    """A file-backed SQLite database with every migration applied.

    Session-scoped: migrating once and giving each test its own transaction is
    much faster than re-migrating per test, and the rollback in ``db_session``
    keeps them isolated.
    """
    import os

    from alembic.config import Config

    from alembic import command
    from app.core.config import get_settings

    db_path = tmp_path_factory.mktemp("costing") / "costing.db"
    url = f"sqlite+aiosqlite:///{db_path}"

    # alembic/env.py builds a full Settings object, so every variable that
    # Settings validates must be present while the migration runs — not just the
    # database URL. These are set here rather than at import time so they cannot
    # leak into tests that assert on configuration.
    overrides = {
        "DATABASE_URL": url,
        "JWT_SECRET": TEST_JWT_SECRET,
        "GENAI_API_KEY": "test-placeholder",
    }
    previous = {name: os.environ.get(name) for name in overrides}
    os.environ.update(overrides)
    get_settings.cache_clear()
    try:
        config = Config(str(BACKEND_DIR / "alembic.ini"))
        config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
        config.set_main_option("path_separator", "os")
        command.upgrade(config, "head")
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        get_settings.cache_clear()

    return url


@pytest_asyncio.fixture
async def db_session(migrated_db_url: str) -> AsyncIterator[AsyncSession]:
    """A session that is rolled back afterwards, so tests cannot leak into each
    other through the shared database file."""
    engine = create_async_engine(migrated_db_url, connect_args={"timeout": 5})

    @event.listens_for(engine.sync_engine, "connect")
    def _pragmas(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
        await session.rollback()

    await engine.dispose()
