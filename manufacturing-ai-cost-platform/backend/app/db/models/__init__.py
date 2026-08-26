"""ORM models.

One module per table group, mapped exactly onto DATABASE_SCHEMA.md. Importing
this package registers every mapping with `Base.metadata`, which is what
Alembic autogenerate reads.
"""

from app.db.models.registry import (
    Capability,
    Modality,
    ModelRegistryEntry,
    RiskLevel,
)

__all__ = ["Capability", "Modality", "ModelRegistryEntry", "RiskLevel"]
