"""Audit logging.

SECURITY.md section 16 requires an audit record for every privileged operation,
naming budget changes, model enable/disable, routing policy changes, optimization
approvals and rejections, guardrail triggers and high-risk approvals.

The ``audit_events`` table and ``AuditEventRepository`` already existed. Nothing
wrote to them, so ``/governance/audit`` returned an empty list no matter what
happened — an audit trail that records nothing is worse than none, because it
looks like evidence of absence.

Two rules govern what is written:

* **Never store a secret** (SECURITY.md section 16, DATABASE_SCHEMA.md
  section 20). State snapshots are scanned before they are persisted, and a
  record carrying anything credential-shaped is redacted rather than dropped —
  losing the event would lose the evidence that something leaked.
* **A failed write must not fail the operation.** The privileged action has
  already happened; discarding its result because the log failed helps nobody.
  The failure is logged loudly instead.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from app.core.context import get_request_id, get_trace_id
from app.core.logging import get_logger
from app.db.models.audit import AuditEvent
from app.guardrails.output_guard import contains_secret
from app.repositories.audit_repository import AuditEventRepository

logger = get_logger(__name__)

_REDACTED = "***redacted***"


class AuditAction(StrEnum):
    """Auditable actions, from SECURITY.md section 16's examples."""

    BUDGET_CHANGED = "budget.changed"
    MODEL_ENABLED = "model.enabled"
    MODEL_DISABLED = "model.disabled"
    ROUTING_POLICY_CHANGED = "routing_policy.changed"
    OPTIMIZATION_APPROVED = "optimization.approved"
    OPTIMIZATION_REJECTED = "optimization.rejected"
    OPTIMIZATION_APPLIED = "optimization.applied"
    OPTIMIZATION_ROLLED_BACK = "optimization.rolled_back"
    GUARDRAIL_TRIGGERED = "guardrail.triggered"
    HIGH_RISK_ACTION_APPROVED = "high_risk_action.approved"


def _safe_state(state: Any) -> str | None:
    """Serialize a state snapshot, redacting anything credential-shaped.

    Redacted rather than dropped: an audit record that vanishes because it
    contained a secret destroys the evidence that a secret was there.
    """
    if state is None:
        return None
    try:
        text = json.dumps(state, default=str, sort_keys=True)
    except (TypeError, ValueError):
        text = str(state)
    if contains_secret(text):
        logger.warning("audit_state_redacted")
        return _REDACTED
    return text


class AuditService:
    """Writes audit records for privileged operations."""

    def __init__(self, repository: AuditEventRepository) -> None:
        self._repository = repository

    async def record(
        self,
        action: AuditAction,
        *,
        tenant_id: str,
        resource_type: str,
        resource_id: str,
        user_id: str | None = None,
        before_state: Any = None,
        after_state: Any = None,
        reason: str | None = None,
        approval_id: str | None = None,
    ) -> AuditEvent | None:
        """Write one audit record.

        Returns the persisted event, or ``None`` when the write failed. Never
        raises: the operation being audited has already happened.

        Correlation ids come from the request context so an audit entry can be
        joined to the telemetry for the same request
        (ARCHITECTURE.md section 15).
        """
        try:
            event = AuditEvent(
                id=str(uuid.uuid4()),
                timestamp=datetime.now(UTC),
                request_id=get_request_id(),
                trace_id=get_trace_id(),
                tenant_id=tenant_id,
                user_id=user_id,
                action=str(action),
                resource_type=resource_type,
                resource_id=resource_id,
                before_state=_safe_state(before_state),
                after_state=_safe_state(after_state),
                reason=reason,
                approval_id=approval_id,
            )
            await self._repository.add(event)
            logger.info(
                "audit_event_recorded",
                extra={
                    "action": str(action),
                    "resource_type": resource_type,
                    "resource_id": resource_id,
                },
            )
            return event
        except Exception:
            # The privileged action already happened. Losing its result because
            # the audit write failed helps nobody, but the failure must be loud
            # (AI_DEVELOPMENT_RULES.md section 26).
            logger.exception(
                "audit_write_failed",
                extra={"action": str(action), "resource_id": resource_id},
            )
            return None
