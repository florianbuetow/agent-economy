"""Versioned, strictly validated judge output (plan item 3.2, G09)."""

from __future__ import annotations

from dataclasses import dataclass

JUDGE_OUTPUT_SCHEMA_VERSION = 1

# Ordered worst to best; the vocabulary the Reputation service accepts.
QUALITY_RATINGS: tuple[str, ...] = ("dissatisfied", "satisfied", "extremely_satisfied")

_TEXT_FIELDS = ("spec_quality_explanation", "delivery_quality_explanation", "reasoning")


@dataclass(frozen=True)
class JudgeFindings:
    """One judge's validated findings: payout plus two independent quality ratings."""

    worker_pct: int
    spec_quality: str
    spec_quality_explanation: str
    delivery_quality: str
    delivery_quality_explanation: str
    reasoning: str


def validate_rating(field: str, value: object) -> str:
    """Return ``value`` if it is a known quality rating, else raise ``ValueError``."""
    if not isinstance(value, str) or value not in QUALITY_RATINGS:
        msg = f"{field} must be one of {', '.join(QUALITY_RATINGS)}"
        raise ValueError(msg)
    return value


def parse_judge_output(raw: object) -> JudgeFindings:
    """Validate a judge's JSON object; any missing or malformed field raises ``ValueError``."""
    if not isinstance(raw, dict):
        msg = "judge output must be a JSON object"
        raise ValueError(msg)
    version = raw.get("schema_version")
    if type(version) is not int or version != JUDGE_OUTPUT_SCHEMA_VERSION:
        msg = f"schema_version must be {JUDGE_OUTPUT_SCHEMA_VERSION}"
        raise ValueError(msg)
    worker_pct = raw.get("worker_pct")
    if type(worker_pct) is not int or not 0 <= worker_pct <= 100:
        msg = "worker_pct must be an integer in [0, 100]"
        raise ValueError(msg)
    texts: dict[str, str] = {}
    for field in _TEXT_FIELDS:
        value = raw.get(field)
        if not isinstance(value, str) or value.strip() == "":
            msg = f"{field} must be a non-empty string"
            raise ValueError(msg)
        texts[field] = value
    return JudgeFindings(
        worker_pct=worker_pct,
        spec_quality=validate_rating("spec_quality", raw.get("spec_quality")),
        spec_quality_explanation=texts["spec_quality_explanation"],
        delivery_quality=validate_rating("delivery_quality", raw.get("delivery_quality")),
        delivery_quality_explanation=texts["delivery_quality_explanation"],
        reasoning=texts["reasoning"],
    )


def median_rating(ratings: list[str]) -> str:
    """Median of an odd-sized panel's ratings on the ordered rating scale."""
    ranks = sorted(QUALITY_RATINGS.index(validate_rating("rating", r)) for r in ratings)
    return QUALITY_RATINGS[ranks[len(ranks) // 2]]
