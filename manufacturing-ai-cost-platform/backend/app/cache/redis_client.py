"""Redis abstraction.

ARCHITECTURE.md section 13 lists Redis as a core backend component but does not
yet state what the platform stores in it — that is recorded as an open item in
the architecture document.

This module therefore provides only a connection lifecycle and a health probe.
No caching, locking, queueing or rate-limiting semantics are invented here. When
the architecture records Redis's role, the behaviour belongs behind this
interface so callers stay provider-agnostic.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@runtime_checkable
class CacheClient(Protocol):
    """Interface the application depends on, so Redis can be faked in tests."""

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def healthcheck(self) -> bool: ...


class RedisCache:
    """Thin lifecycle wrapper around ``redis.asyncio``."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: Any | None = None

    async def connect(self) -> None:
        if not self._settings.redis_enabled:
            logger.info("redis_disabled")
            return
        if self._client is not None:
            return

        # Imported lazily so the package is not required when Redis is disabled.
        from redis.asyncio import Redis

        self._client = Redis.from_url(
            self._settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
        )
        logger.info("redis_client_created")

    async def disconnect(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            logger.info("redis_disconnected")
        self._client = None

    async def healthcheck(self) -> bool:
        """Return True when Redis answers, or when Redis is disabled by config."""
        if not self._settings.redis_enabled:
            return True
        if self._client is None:
            return False
        try:
            return bool(await self._client.ping())
        except Exception:
            logger.exception("redis_healthcheck_failed")
            return False


class NullCache:
    """No-op cache used when Redis is disabled or unavailable in tests."""

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def healthcheck(self) -> bool:
        return True
