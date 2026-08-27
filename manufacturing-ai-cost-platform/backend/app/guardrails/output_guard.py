"""Output guardrails (SECURITY.md section 12).

Validates what a model produced *before* anything acts on it or a caller sees
it. Two rules from section 12 drive the whole module:

* "Never execute generated SQL, shell commands or privileged operations
  directly from model output." Model output is data. This layer never runs it,
  and refuses to pass along anything shaped like an instruction to run
  something.
* Sensitive information must not leave in a response. A model can echo back a
  secret it was shown, or one that leaked into its context — the response is
  the last place to catch that.

Blocking is deliberate over redaction for secrets: a response that *contained* a
credential is evidence of a problem upstream, and quietly masking it would hide
the incident while still confirming to an attacker that something was there.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from app.core.logging import get_logger
from app.guardrails.errors import (
    OutputRejected,
    SensitiveOutputBlocked,
    UnsafeActionBlocked,
)
from app.security.events import SecurityEvent, record_security_event

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Sensitive content
# ---------------------------------------------------------------------------
#: Credential shapes that must never appear in a response. Deliberately narrow
#: and anchored, so ordinary manufacturing text does not trip them.
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("bearer_token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{16,}")),
    ("openai_style_key", re.compile(r"\bsk-[A-Za-z0-9]{16,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    (
        "assigned_credential",
        re.compile(
            r"(?i)\b(api[_-]?key|secret|password|token|credential)\b\s*[:=]\s*"
            r"['\"]?[A-Za-z0-9._~+/-]{12,}"
        ),
    ),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("connection_string", re.compile(r"(?i)\b\w+://[^\s:@]+:[^\s:@]+@[^\s/]+")),
)

# ---------------------------------------------------------------------------
# Executable / privileged content
# ---------------------------------------------------------------------------
#: Output shaped like something to execute. Matching does not mean the platform
#: would have run it — nothing here executes model output — but returning it
#: invites a downstream consumer to.
_UNSAFE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "sql_mutation",
        re.compile(
            r"(?i)\b(drop\s+table|truncate\s+table|delete\s+from|update\s+\w+\s+set"
            r"|insert\s+into|alter\s+table|grant\s+all)\b"
        ),
    ),
    (
        "shell_command",
        re.compile(r"(?i)(^|[;&|`]|\$\()\s*(rm\s+-rf|curl\s|wget\s|chmod\s|sudo\s)"),
    ),
    ("code_execution", re.compile(r"(?i)\b(eval|exec|system|subprocess|os\.popen)\s*\(")),
)


@dataclass(frozen=True, slots=True)
class OutputGuard:
    """The output layer, configured per workload."""

    #: Actions the workload may legitimately request. Empty means the output is
    #: informational only and may request no action at all.
    allowed_actions: frozenset[str] = frozenset()
    #: Fields a structured response must contain.
    required_fields: frozenset[str] = frozenset()
    #: Minimum confidence for a result to be acted on, when the model reports one.
    minimum_confidence: float | None = None

    # ------------------------------------------------------------- checks
    def check_text(self, content: str) -> None:
        """Validate free-text output.

        Raises:
            SensitiveOutputBlocked / UnsafeActionBlocked
        """
        secrets = [name for name, pattern in _SECRET_PATTERNS if pattern.search(content)]
        if secrets:
            record_security_event(
                SecurityEvent.SENSITIVE_OUTPUT_BLOCKED,
                reason="credential_in_output",
                patterns=sorted(secrets),
            )
            # The matched value is never logged or returned — that would move
            # the secret from one place it should not be to another.
            raise SensitiveOutputBlocked(
                reason="credential_in_output", details={"patterns": sorted(secrets)}
            )

        unsafe = [name for name, pattern in _UNSAFE_PATTERNS if pattern.search(content)]
        if unsafe:
            record_security_event(
                SecurityEvent.GUARDRAIL_REJECTED,
                reason="executable_content_in_output",
                patterns=sorted(unsafe),
            )
            raise UnsafeActionBlocked(
                reason="executable_content_in_output",
                details={"patterns": sorted(unsafe)},
            )

    def check_structured(self, payload: Any) -> dict[str, Any]:
        """Validate a structured result and return it.

        Consumed programmatically, so it is validated as data rather than
        trusted as a command (AI_DEVELOPMENT_RULES.md section 37).
        """
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise OutputRejected(
                    "Model output was not valid JSON", reason="invalid_json"
                ) from exc

        if not isinstance(payload, dict):
            raise OutputRejected(
                "Model output was not a JSON object", reason="not_an_object"
            )

        # Serialize once and scan the whole thing: a secret nested three levels
        # down is still a secret.
        self.check_text(json.dumps(payload, default=str))

        missing = set(self.required_fields) - set(payload)
        if missing:
            raise OutputRejected(
                "Model output is missing required fields",
                reason="missing_required_fields",
                details={"missing": sorted(missing)},
            )

        action = payload.get("action")
        if action is not None:
            if not self.allowed_actions:
                raise UnsafeActionBlocked(
                    "This workload's output may not request an action",
                    reason="actions_not_permitted",
                )
            if action not in self.allowed_actions:
                record_security_event(
                    SecurityEvent.GUARDRAIL_REJECTED,
                    reason="action_not_allowlisted",
                    requested_action=str(action),
                )
                raise UnsafeActionBlocked(
                    reason="action_not_allowlisted",
                    details={"allowed_actions": sorted(self.allowed_actions)},
                )

        confidence = payload.get("confidence")
        if self.minimum_confidence is not None and confidence is not None:
            try:
                value = float(confidence)
            except (TypeError, ValueError) as exc:
                raise OutputRejected(
                    "Model reported a non-numeric confidence",
                    reason="invalid_confidence",
                ) from exc
            if value < self.minimum_confidence:
                # Not an error — a low-confidence answer is a real answer. But
                # it must not be acted on automatically.
                raise OutputRejected(
                    "Model confidence is below the threshold for this workload",
                    reason="below_confidence_threshold",
                    details={"minimum_confidence": self.minimum_confidence},
                )

        return payload


def contains_secret(text: str) -> bool:
    """Whether text carries anything credential-shaped.

    Exposed for tests and for scanning at other boundaries (logs, audit
    records) where the same question is asked.
    """
    return any(pattern.search(text) for _, pattern in _SECRET_PATTERNS)
