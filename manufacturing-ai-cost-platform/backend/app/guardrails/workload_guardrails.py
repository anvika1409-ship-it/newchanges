"""Guardrails composed for one workload, wired into the execution path.

The individual layers in this package are deliberately fine-grained. This module
assembles them into the collaborator the orchestrator actually calls, so that
every ``/ai/execute`` request passes through the input layer before routing and
the output layer before a result is returned.

Without this, the layers are a tested library that nothing invokes — which is
the state they were in before: implemented, covered, and enforcing nothing.

Per-workload configuration is deliberately conservative. A workload that has not
declared which actions its output may request gets **none**: an output that asks
to stop a production line is a recommendation for a human, never an instruction
the platform relays (SECURITY.md sections 12 and 14).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.config import Settings
from app.core.logging import get_logger
from app.guardrails.errors import ALLOW, GuardrailViolation
from app.guardrails.input_guard import Content, InputGuard, UntrustedContent
from app.guardrails.output_guard import OutputGuard
from app.security.events import SecurityEvent, record_security_event

logger = get_logger(__name__)

#: Actions each workload's output may legitimately request.
#:
#: Empty for every workload today: no source document defines an action
#: vocabulary, and inventing one would let a model request something the
#: platform then treats as permitted. A workload gains entries here only when
#: its allowed actions are actually specified.
WORKLOAD_ALLOWED_ACTIONS: dict[str, frozenset[str]] = {
    "quality_check": frozenset(),
    "predictive_maintenance": frozenset(),
    "supply_chain": frozenset(),
}


def _untrusted_strings(payload: Any, path: str = "request_payload") -> list[Content]:
    """Collect the free text inside a request payload as untrusted content.

    Everything a caller supplies is untrusted until classified
    (SECURITY.md section 2). Walking the structure matters: injected text hidden
    three levels down in a maintenance report is still reaching the model.
    """
    found: list[Content] = []
    if isinstance(payload, str):
        found.append(UntrustedContent(payload, source=path))
    elif isinstance(payload, dict):
        for key, value in payload.items():
            found.extend(_untrusted_strings(value, f"{path}.{key}"))
    elif isinstance(payload, (list, tuple)):
        for index, value in enumerate(payload):
            found.extend(_untrusted_strings(value, f"{path}[{index}]"))
    return found


@dataclass(slots=True)
class WorkloadGuardrails:
    """The orchestrator's guardrail collaborator.

    Exposes the async ``check_input`` / ``check_output`` pair the orchestrator
    calls, and remembers the terminal decision so it can be recorded on the
    usage event (DATABASE_SCHEMA.md section 14).
    """

    input_guard: InputGuard
    output_guard: OutputGuard
    workload_type: str | None = None

    #: Terminal decision for the current request: ALLOW, or ``LAYER:reason``.
    decision: str = field(default=ALLOW)

    async def check_input(self, payload: Any) -> None:
        """Input layer: size, shape, and injection scanning of untrusted text.

        Raises:
            GuardrailViolation: the request does not proceed to routing.
        """
        try:
            self.input_guard.check(
                payload if isinstance(payload, dict) else {"payload": payload},
                _untrusted_strings(payload),
            )
        except GuardrailViolation as violation:
            self.decision = violation.decision
            record_security_event(
                SecurityEvent.GUARDRAIL_REJECTED,
                reason=violation.reason,
                guardrail_layer=str(violation.layer),
                workload_type=self.workload_type,
            )
            raise
        self.decision = ALLOW

    async def check_output(self, content: str) -> None:
        """Output layer: secrets and executable content, before a caller sees it.

        Raises:
            GuardrailViolation: the result is withheld.
        """
        try:
            self.output_guard.check_text(content)
        except GuardrailViolation as violation:
            self.decision = violation.decision
            record_security_event(
                SecurityEvent.GUARDRAIL_REJECTED,
                reason=violation.reason,
                guardrail_layer=str(violation.layer),
                workload_type=self.workload_type,
            )
            raise
        self.decision = ALLOW


def build_workload_guardrails(
    settings: Settings, workload_type: str | None = None
) -> WorkloadGuardrails:
    """Compose the guardrails for a workload from configuration.

    The payload ceiling reuses ``max_request_bytes`` rather than introducing a
    second limit that could drift away from the transport-level one enforced by
    ``MaxBodySizeMiddleware``.
    """
    return WorkloadGuardrails(
        input_guard=InputGuard(max_payload_bytes=settings.max_request_bytes),
        output_guard=OutputGuard(
            allowed_actions=WORKLOAD_ALLOWED_ACTIONS.get(
                workload_type or "", frozenset()
            )
        ),
        workload_type=workload_type,
    )
