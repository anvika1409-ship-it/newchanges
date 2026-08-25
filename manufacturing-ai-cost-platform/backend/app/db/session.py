"""Async database abstraction.

SQLite is the MVP database (DATABASE_SCHEMA.md section 1). Everything above this
module talks to SQLAlchemy sessions and repositories, never to SQLite directly,
so the same logical schema can move to PostgreSQL later
(AI_DEVELOPMENT_RULES.md section 16).

The SQLite pragmas required by DATABASE_SCHEMA.md section 22 and
AI_DEVELOPMENT_RULES.md section 17 are applied on every connection.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def _is_memory_url(url: str) -> bool:
    return ":memory:" in url or "mode=memory" in url


class Database:
    """Owns the engine and session factory for the application's lifetime."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._engine: AsyncEngine | None = None
        self._sessionmaker: async_sessionmaker[AsyncSession] | None = None

    # ------------------------------------------------------------------ setup
    async def connect(self) -> None:
        if self._engine is not None:
            return

        url = self._settings.database_url
        kwargs: dict[str, Any] = {
            "echo": self._settings.database_echo,
            "future": True,
        }

        if self._settings.is_sqlite:
            # busy timeout is given to the driver in seconds; the setting is ms.
            kwargs["connect_args"] = {
                "timeout": self._settings.sqlite_busy_timeout_ms / 1000
            }
            if _is_memory_url(url):
                # A single shared connection, otherwise each connection would get
                # its own empty in-memory database.
                kwargs["poolclass"] = StaticPool
            else:
                self._ensure_sqlite_directory(url)
        else:
            kwargs["pool_pre_ping"] = True

        engine = create_async_engine(url, **kwargs)

        if self._settings.is_sqlite:
            self._register_sqlite_pragmas(engine)

        self._engine = engine
        self._sessionmaker = async_sessionmaker(
            bind=engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
        logger.info("database_connected", extra={"dialect": engine.dialect.name})

    @staticmethod
    def _ensure_sqlite_directory(url: str) -> None:
        """Create the parent directory for a file-backed SQLite database."""
        _, _, path_part = url.partition(":///")
        if not path_part:
            return
        db_path = Path(path_part.split("?", 1)[0])
        if db_path.parent and str(db_path.parent) not in ("", "."):
            db_path.parent.mkdir(parents=True, exist_ok=True)

    def _register_sqlite_pragmas(self, engine: AsyncEngine) -> None:
        busy_timeout_ms = self._settings.sqlite_busy_timeout_ms
        use_wal = not _is_memory_url(self._settings.database_url)

        @event.listens_for(engine.sync_engine, "connect")
        def _set_pragmas(dbapi_connection: Any, _record: Any) -> None:
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON")
                if use_wal:
                    cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
            finally:
                cursor.close()

    # --------------------------------------------------------------- teardown
    async def disconnect(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            logger.info("database_disconnected")
        self._engine = None
        self._sessionmaker = None

    # ------------------------------------------------------------------ usage
    @property
    def engine(self) -> AsyncEngine:
        if self._engine is None:
            raise RuntimeError("Database.connect() must be awaited before use")
        return self._engine

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Transactional session scope. Commits on success, rolls back on error."""
        if self._sessionmaker is None:
            raise RuntimeError("Database.connect() must be awaited before use")
        session = self._sessionmaker()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    async def healthcheck(self) -> bool:
        """Cheap liveness probe for the readiness endpoint."""
        try:
            async with self.engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except Exception:
            logger.exception("database_healthcheck_failed")
            return False
        return True
