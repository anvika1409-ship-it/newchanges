"""Manufacturing Quality Control workload logic.

Implements inspection result parsing and prompt generation for vision-based
defect detection workflows (ARCHITECTURE.md section 2, AI_WORKFLOWS.md section 2).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class QualityVerdict(StrEnum):
    """Manufacturing inspection outcome."""

    PASS = "PASS"
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"


@dataclass(frozen=True, slots=True)
class QualityResult:
    """Structured result of a quality inspection."""

    verdict: QualityVerdict
    defect_type: str | None = None
    confidence: float | None = None
    raw_response: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": str(self.verdict),
            "defect_type": self.defect_type,
            "confidence": self.confidence,
            "raw_response": self.raw_response,
        }


def build_quality_prompt(payload: dict[str, Any] | None = None) -> str:
    """Build structured inspection instructions for a vision model.

    Asks for strict JSON output with verdict, defect_type, and confidence.
    """
    context_str = ""
    if payload:
        spec = payload.get("part_spec") or payload.get("description") or payload.get("component")
        if spec:
            context_str = f"\nComponent specifications / expected standards: {spec}\n"

    return (
        "You are an expert manufacturing quality control inspector. "
        "Examine the provided image carefully for physical defects, surface scratches, "
        "cracks, misalignments, solder defects, or manufacturing anomalies."
        f"{context_str}\n"
        "Respond ONLY with a valid JSON object matching this schema:\n"
        "{\n"
        '  "verdict": "PASS" | "FAIL",\n'
        '  "defect_type": "<defect name or null if pass>",\n'
        '  "confidence": <float between 0.0 and 1.0>\n'
        "}\n"
        "Do not include Markdown code blocks or any other commentary outside the JSON."
    )


def parse_quality_response(content: str) -> QualityResult:
    """Parse the vision model's output into a structured QualityResult.

    Attempts strict JSON parsing first, then JSON extraction from markdown/text,
    and finally heuristic fallback if unparseable.
    """
    cleaned = content.strip()
    if not cleaned:
        return QualityResult(
            verdict=QualityVerdict.INCONCLUSIVE,
            defect_type=None,
            confidence=None,
            raw_response=content,
        )

    # 1. Try direct JSON parse
    data = None
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        # 2. Try extracting JSON from markdown or code fences
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if match:
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

    if isinstance(data, dict):
        raw_verdict = str(data.get("verdict", "")).strip().upper()
        if raw_verdict in ("PASS", "PASSED", "OK"):
            verdict = QualityVerdict.PASS
        elif raw_verdict in ("FAIL", "FAILED", "DEFECT", "REJECT"):
            verdict = QualityVerdict.FAIL
        else:
            verdict = QualityVerdict.INCONCLUSIVE

        defect_type = data.get("defect_type")
        if defect_type is not None:
            defect_type = str(defect_type).strip() or None
            if defect_type and defect_type.lower() in ("none", "null", "n/a"):
                defect_type = None

        confidence = data.get("confidence")
        if confidence is not None:
            try:
                confidence = float(confidence)
                confidence = max(0.0, min(1.0, confidence))
            except (ValueError, TypeError):
                confidence = None

        return QualityResult(
            verdict=verdict,
            defect_type=defect_type,
            confidence=confidence,
            raw_response=content,
        )

    # 3. Heuristic fallback
    lower = cleaned.lower()
    if "pass" in lower and "fail" not in lower:
        return QualityResult(
            verdict=QualityVerdict.PASS,
            defect_type=None,
            confidence=None,
            raw_response=content,
        )
    if "fail" in lower or "defect" in lower or "crack" in lower or "scratch" in lower:
        return QualityResult(
            verdict=QualityVerdict.FAIL,
            defect_type="detected_defect",
            confidence=None,
            raw_response=content,
        )

    return QualityResult(
        verdict=QualityVerdict.INCONCLUSIVE,
        defect_type=None,
        confidence=None,
        raw_response=content,
    )
