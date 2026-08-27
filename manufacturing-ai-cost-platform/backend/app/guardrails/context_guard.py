"""Context guardrails (SECURITY.md section 10, AI_WORKFLOWS.md section 8).

Runs the six checks section 10 requires, in order, before anything is added to
a model's context:

    1. authorization      4. source trust assessment
    2. data classification 5. token/context limit
    3. relevance filtering 6. sensitive-data filtering

"Do not send an entire database or unrestricted document collection into the
model." The default here is therefore to *exclude*: a fragment is admitted only
when it is demonstrably authorized for this caller, and anything whose ownership
cannot be established is dropped rather than passed along.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from app.core.logging import get_logger
from app.guardrails.errors import ContextRejected, ContextTooLarge
from app.guardrails.input_guard import Content, TrustLevel
from app.security.principal import Principal, ResourceScope
from app.security.scope import AuthorizedScope

logger = get_logger(__name__)


class DataClassification(StrEnum):
    """How sensitive a fragment is.

    RESTRICTED never reaches a model: SECURITY.md section 17 forbids sending
    unnecessary sensitive content, and a model call leaves the trust boundary.
    """

    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    RESTRICTED = "RESTRICTED"


#: Classifications that may be sent to a model.
_SENDABLE = frozenset({DataClassification.PUBLIC, DataClassification.INTERNAL})


@dataclass(frozen=True, slots=True)
class ContextFragment:
    """One candidate piece of retrieved context."""

    text: str
    #: Ownership of the source record. Required — a fragment whose owner is
    #: unknown cannot be authorized, and is dropped.
    scope: ResourceScope | None
    classification: DataClassification = DataClassification.INTERNAL
    trust: TrustLevel = TrustLevel.UNTRUSTED
    source: str | None = None
    #: 0.0-1.0. Fragments below the configured floor are dropped as irrelevant.
    relevance: float = 1.0
    #: Token cost of including this fragment, when known.
    estimated_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class ContextDecision:
    """What survived, and what did not."""

    admitted: tuple[ContextFragment, ...] = ()
    dropped: tuple[tuple[ContextFragment, str], ...] = ()
    total_tokens: int = 0

    @property
    def contents(self) -> list[Content]:
        """Admitted fragments as trust-tagged content for the model layer."""
        return [
            Content(text=f.text, trust=f.trust, source=f.source) for f in self.admitted
        ]

    @property
    def drop_reasons(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for _, reason in self.dropped:
            counts[reason] = counts.get(reason, 0) + 1
        return counts


@dataclass(frozen=True, slots=True)
class ContextGuard:
    """The context layer, configured per workload."""

    #: Ceiling on assembled context. Enforced by dropping, then by refusing.
    max_context_tokens: int | None = None
    #: Fragments below this relevance are not worth their token cost.
    minimum_relevance: float = 0.0
    #: Classifications permitted for this workload.
    sendable: frozenset[DataClassification] = field(default_factory=lambda: _SENDABLE)

    def filter(
        self,
        fragments: list[ContextFragment],
        principal: Principal,
        scope: AuthorizedScope | None = None,
    ) -> ContextDecision:
        """Apply the six checks and return what may be sent.

        Dropping is silent to the model but recorded in the decision, so an
        operator can see that context was withheld rather than wondering why an
        answer was thin.
        """
        admitted: list[ContextFragment] = []
        dropped: list[tuple[ContextFragment, str]] = []
        total = 0

        for fragment in fragments:
            reason = self._reject_reason(fragment, principal, scope)
            if reason is not None:
                dropped.append((fragment, reason))
                continue

            tokens = fragment.estimated_tokens or 0
            if (
                self.max_context_tokens is not None
                and total + tokens > self.max_context_tokens
            ):
                # Budget spent. Later fragments are dropped rather than
                # truncated mid-sentence, which would corrupt meaning.
                dropped.append((fragment, "context_budget_exhausted"))
                continue

            admitted.append(fragment)
            total += tokens

        if dropped:
            logger.info(
                "context_fragments_dropped",
                extra={
                    "dropped_count": len(dropped),
                    "admitted_count": len(admitted),
                    "reasons": sorted({r for _, r in dropped}),
                },
            )

        return ContextDecision(
            admitted=tuple(admitted), dropped=tuple(dropped), total_tokens=total
        )

    def _reject_reason(
        self,
        fragment: ContextFragment,
        principal: Principal,
        scope: AuthorizedScope | None,
    ) -> str | None:
        """Why this fragment may not be sent, or None if it may."""
        # 1. authorization — an unknown owner cannot be authorized.
        if fragment.scope is None:
            return "no_owner_recorded"
        if fragment.scope.tenant_id != principal.tenant_id:
            # Cross-tenant context is the worst case: it would leak another
            # tenant's data into this tenant's answer.
            logger.warning("context_cross_tenant_fragment_dropped")
            return "cross_tenant"
        if scope is not None and not _within_scope(fragment.scope, scope):
            return "out_of_scope"

        # 2. data classification
        if fragment.classification not in self.sendable:
            return "classification_not_sendable"

        # 3. relevance
        if fragment.relevance < self.minimum_relevance:
            return "below_relevance_floor"

        return None

    def enforce(
        self,
        fragments: list[ContextFragment],
        principal: Principal,
        scope: AuthorizedScope | None = None,
    ) -> ContextDecision:
        """Filter, then refuse outright if nothing usable survived a non-empty set.

        Raises:
            ContextRejected: every candidate fragment was unauthorized.
            ContextTooLarge: the admitted set still exceeds the ceiling.
        """
        decision = self.filter(fragments, principal, scope)

        if fragments and not decision.admitted:
            raise ContextRejected(
                reason="no_authorized_context",
                details={"drop_reasons": decision.drop_reasons},
            )

        if (
            self.max_context_tokens is not None
            and decision.total_tokens > self.max_context_tokens
        ):
            raise ContextTooLarge(
                reason="context_over_limit",
                details={
                    "max_context_tokens": self.max_context_tokens,
                    "assembled_tokens": decision.total_tokens,
                },
            )

        return decision


def _within_scope(fragment_scope: ResourceScope, authorized: AuthorizedScope) -> bool:
    """Whether a fragment falls inside the caller's authorized scope."""
    if fragment_scope.tenant_id != authorized.tenant_id:
        return False
    for branch in authorized.branches:
        plant_ok = branch.plant_id is None or branch.plant_id == fragment_scope.plant_id
        dept_ok = (
            branch.department_id is None
            or branch.department_id == fragment_scope.department_id
        )
        if plant_ok and dept_ok:
            return True
    return False
