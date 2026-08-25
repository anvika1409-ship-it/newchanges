"""Declarative base and metadata.

ORM models are added here as DATABASE_SCHEMA.md is implemented. No model is
defined yet: this scaffold contains no business functionality, and inventing
tables ahead of the schema document is not permitted
(AI_DEVELOPMENT_RULES.md sections 2 and 35).

The naming convention is set now so Alembic produces stable, explicitly named
constraints from the first migration onward — renaming them later is a painful
migration on any database.
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Base(DeclarativeBase):
    """Base class for all ORM models."""

    metadata = metadata
