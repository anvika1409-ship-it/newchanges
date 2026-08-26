"""create models table (model registry)

Implements DATABASE_SCHEMA.md section 11.

Revision ID: 0001_models
Revises:
Create Date: 2026-08-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_models"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "models",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("model_name", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=True),
        sa.Column("capability", sa.String(), nullable=False),
        sa.Column("modality", sa.String(), nullable=True),
        # Cost, context, quality and latency are nullable because they are
        # operator-supplied configuration. NULL means unknown, never zero.
        sa.Column("input_cost", sa.Float(), nullable=True),
        sa.Column("output_cost", sa.Float(), nullable=True),
        sa.Column("cost_unit", sa.String(), nullable=True),
        sa.Column("max_context_tokens", sa.Integer(), nullable=True),
        sa.Column("supports_vision", sa.Boolean(), nullable=True),
        sa.Column("supports_tools", sa.Boolean(), nullable=True),
        sa.Column("supports_structured_output", sa.Boolean(), nullable=True),
        sa.Column("supports_embeddings", sa.Boolean(), nullable=True),
        sa.Column("quality_score", sa.Float(), nullable=True),
        sa.Column("latency_score", sa.Float(), nullable=True),
        sa.Column("risk_level", sa.String(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "capability IN ('text','vision','multimodal','embedding','speech',"
            "'coding','reasoning')",
            name=op.f("ck_models_capability_valid"),
        ),
        sa.CheckConstraint(
            "modality IS NULL OR modality IN ('text','image','multimodal','structured')",
            name=op.f("ck_models_modality_valid"),
        ),
        sa.CheckConstraint(
            "risk_level IS NULL OR risk_level IN ('LOW','MEDIUM','HIGH','CRITICAL')",
            name=op.f("ck_models_risk_level_valid"),
        ),
        sa.CheckConstraint(
            "input_cost IS NULL OR input_cost >= 0",
            name=op.f("ck_models_input_cost_non_negative"),
        ),
        sa.CheckConstraint(
            "output_cost IS NULL OR output_cost >= 0",
            name=op.f("ck_models_output_cost_non_negative"),
        ),
        sa.CheckConstraint(
            "max_context_tokens IS NULL OR max_context_tokens > 0",
            name=op.f("ck_models_max_context_tokens_positive"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_models")),
    )

    # model_name is the identifier callers use against the gateway, so it must
    # be unique and is looked up directly.
    op.create_index(op.f("ix_models_model_name"), "models", ["model_name"], unique=True)
    op.create_index(op.f("ix_models_provider"), "models", ["provider"], unique=False)
    op.create_index(op.f("ix_models_capability"), "models", ["capability"], unique=False)
    op.create_index(op.f("ix_models_modality"), "models", ["modality"], unique=False)
    op.create_index(op.f("ix_models_enabled"), "models", ["enabled"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_models_enabled"), table_name="models")
    op.drop_index(op.f("ix_models_modality"), table_name="models")
    op.drop_index(op.f("ix_models_capability"), table_name="models")
    op.drop_index(op.f("ix_models_provider"), table_name="models")
    op.drop_index(op.f("ix_models_model_name"), table_name="models")
    op.drop_table("models")
