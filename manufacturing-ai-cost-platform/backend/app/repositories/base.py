"""Repository abstraction.

Keeps SQLite behind SQLAlchemy so business logic never depends on
SQLite-specific SQL and the same repositories can serve PostgreSQL later
(ARCHITECTURE.md section 12, AI_DEVELOPMENT_RULES.md section 16).

Concrete repositories are added alongside the ORM models they own.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession


class AsyncRepository[ModelT]:
    """Base class for persistence adapters.

    A repository receives a session; it does not create or own one. Transaction
    boundaries belong to the calling service so a unit of work can span several
    repositories — which also keeps SQLite write transactions short
    (DATABASE_SCHEMA.md section 22).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        return self._session
