"""Input guardrails (AI_WORKFLOWS.md section 8, SECURITY.md sections 8 and 9).

Runs before orchestration so a malformed or hostile request is refused before
any classification, routing or model work happens.

On prompt injection, honestly: **detection is the weakest of the defences here.**
SECURITY.md section 9 says so outright — "No prompt-injection detector is
perfect. Defense in depth is required." The load-bearing protections are
structural and live elsewhere:

* untrusted content is passed as ``user`` content, never merged into the system
  prompt (``TrustedContent`` / ``UntrustedContent`` below make that explicit);
* the model may only call registered, allowlisted tools (``tool_guard``);
* model output is validated before anything acts on it (``output_guard``);
* the model never holds credentials.

The pattern matcher is a tripwire that catches the obvious and noisy cases and
records them for SECURITY.md section 20 monitoring. It is not a filter anyone
should rely on, and it is deliberately *not* used to sanitise-and-continue:
rewriting hostile text and proceeding gives false confidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.core.logging import get_logger
from app.guardrails.errors import InputRejected, PayloadTooLarge, PromptInjectionSuspected
from app.security.events import SecurityEvent, record_security_event

logger = get_logger(__name__)


class TrustLevel(StrEnum):
    """Where a piece of content came from.

    External data is untrusted until explicitly classified
    (SECURITY.md section 2).
    """

    TRUSTED = "TRUSTED"
    UNTRUSTED = "UNTRUSTED"


@dataclass(frozen=True, slots=True)
class Content:
    """A piece of content with its trust level attached.

    The trust level travels with the text so a downstream caller cannot lose
    track of which strings are safe to treat as instructions.
    """

    text: str
    trust: TrustLevel
    source: str | None = None

    @property
    def is_trusted(self) -> bool:
        return self.trust is TrustLevel.TRUSTED


def TrustedContent(text: str, source: str | None = None) -> Content:  # noqa: N802
    """Platform-authored content: system prompts, templates."""
    return Content(text=text, trust=TrustLevel.TRUSTED, source=source)


def UntrustedContent(text: str, source: str | None = None) -> Content:  # noqa: N802
    """Anything from outside: documents, logs, supplier data, RAG results."""
    return Content(text=text, trust=TrustLevel.UNTRUSTED, source=source)


# ---------------------------------------------------------------------------
# Injection tripwire
# ---------------------------------------------------------------------------
#: Instruction-shaped phrases that have no business appearing in machine logs,
#: sensor readings or supplier records. Each is anchored on an imperative aimed
#: at the model rather than on a topic, so ordinary manufacturing prose does not
#: match.
_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "override_instructions",
        re.compile(
            r"(?i)\b(ignore|disregard|forget|override)\b[^.\n]{0,40}"
            r"\b(previous|prior|above|earlier|all)\b[^.\n]{0,20}"
            r"\b(instruction|prompt|rule|direction|context)s?\b"
        ),
    ),
    (
        "role_reassignment",
        re.compile(
            r"(?i)\b(you\s+are\s+now|act\s+as|pretend\s+to\s+be|from\s+now\s+on"
            r"\s+you)\b"
        ),
    ),
    (
        "system_prompt_probe",
        re.compile(
            r"(?i)\b(reveal|show|print|repeat|output|disclose)\b[^.\n]{0,30}"
            r"\b(system\s+prompt|initial\s+instruction|your\s+instruction)s?\b"
        ),
    ),
    (
        "credential_probe",
        re.compile(
            r"(?i)\b(reveal|show|print|give|send|leak|disclose)\b[^.\n]{0,30}"
            r"\b(api[\s_-]?key|secret|token|password|credential)s?\b"
        ),
    ),
    (
        "fake_role_marker",
        # Chat-turn markers embedded in data are an attempt to forge a turn.
        re.compile(r"(?im)^\s*(system|assistant)\s*:\s", re.MULTILINE),
    ),
    (
        "tool_injection",
        re.compile(
            r"(?i)\b(call|invoke|execute|run)\b[^.\n]{0,20}"
            r"\b(tool|function|command|shell|sql)\b"
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class InjectionFinding:
    pattern: str
    source: str | None


def scan_for_injection(content: Content) -> list[InjectionFinding]:
    """Scan untrusted content for instruction-shaped text.

    Trusted content is not scanned: the platform's own prompts legitimately
    contain instructions, and scanning them would be a guaranteed false
    positive.
    """
    if content.is_trusted:
        return []
    return [
        InjectionFinding(pattern=name, source=content.source)
        for name, pattern in _INJECTION_PATTERNS
        if pattern.search(content.text)
    ]


def enforce_no_injection(contents: list[Content]) -> None:
    """Refuse a request whose untrusted content looks like instructions.

    Rejects rather than sanitises. Stripping the matched phrase and continuing
    would leave the rest of an adversarial payload in place while making the
    request look clean.

    Raises:
        PromptInjectionSuspected: on any finding.
    """
    findings = [f for content in contents for f in scan_for_injection(content)]
    if not findings:
        return

    patterns = sorted({f.pattern for f in findings})
    record_security_event(
        SecurityEvent.PROMPT_INJECTION_DETECTED,
        reason="instruction_like_content_in_untrusted_input",
        patterns=patterns,
        sources=sorted({f.source for f in findings if f.source}),
    )
    raise PromptInjectionSuspected(
        reason="instruction_like_content",
        # The matched text is never echoed back: repeating it would hand an
        # attacker a working oracle for tuning the payload.
        details={"patterns": patterns},
    )


# ---------------------------------------------------------------------------
# Structural validation
# ---------------------------------------------------------------------------
def enforce_payload_size(payload: Any, max_bytes: int) -> None:
    """Reject an oversized payload before it is parsed further."""
    import json

    try:
        size = len(json.dumps(payload, default=str).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise InputRejected(
            "Request payload could not be serialized", reason="unserializable_payload"
        ) from exc

    if size > max_bytes:
        record_security_event(
            SecurityEvent.REQUEST_REJECTED_OVERSIZED,
            reason="payload_over_limit",
            size_bytes=size,
            max_bytes=max_bytes,
        )
        raise PayloadTooLarge(
            reason="payload_over_limit",
            details={"max_request_bytes": max_bytes, "size_bytes": size},
        )


def enforce_allowed_fields(payload: dict[str, Any], allowed: set[str]) -> None:
    """Refuse unexpected fields.

    An allowlist rather than a denylist: a field nobody anticipated is exactly
    the one worth refusing (SECURITY.md section 8).
    """
    unexpected = set(payload) - allowed
    if unexpected:
        raise InputRejected(
            "Request contains fields that are not permitted",
            reason="unexpected_fields",
            details={"unexpected_fields": sorted(unexpected)},
        )


def enforce_content_type(content_type: str, allowed: set[str]) -> None:
    """Refuse a media type the endpoint does not accept."""
    base = content_type.split(";", 1)[0].strip().lower()
    if base not in allowed:
        raise InputRejected(
            "Unsupported content type",
            reason="unsupported_content_type",
            details={"allowed": sorted(allowed)},
        )


@dataclass(frozen=True, slots=True)
class InputGuard:
    """The input layer, as one configured object.

    Composed by the orchestrator so limits come from settings and policy rather
    than being hard-coded at each call site.
    """

    max_payload_bytes: int
    allowed_fields: frozenset[str] | None = None

    def check(self, payload: dict[str, Any], contents: list[Content] | None = None) -> None:
        """Run the input layer. Raises on the first violation.

        Order matters: size first, because scanning a huge payload for
        injection patterns is work an attacker can ask for cheaply.
        """
        enforce_payload_size(payload, self.max_payload_bytes)
        if self.allowed_fields is not None:
            enforce_allowed_fields(payload, set(self.allowed_fields))
        if contents:
            enforce_no_injection(contents)
