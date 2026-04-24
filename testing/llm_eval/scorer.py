"""Programmatic tool-accuracy scoring for LLM eval cases.

All scoring here is deterministic. The LLM-as-judge piece lives in judge.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# Latency buckets in seconds.
LATENCY_FAST = 30
LATENCY_MEDIUM = 120
LATENCY_SLOW = 300
EXPLOSION_THRESHOLD = 5  # any tool called >5 times


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]
    duration_s: float | None = None
    error: bool = False


@dataclass
class ToolAccuracyResult:
    score: float
    coverage_pct: float
    missing_tools: list[str]
    extra_tools: list[str]
    tool_call_count: int
    max_tool_repeats: int
    explosion_detected: bool
    retry_count: int
    asked_clarification: bool
    zero_tool_calls: bool
    error_count: int
    duration_s: float
    latency_grade: str
    penalties: list[dict[str, Any]] = field(default_factory=list)


def _latency_grade(duration_s: float) -> str:
    if duration_s <= LATENCY_FAST:
        return "fast"
    if duration_s <= LATENCY_MEDIUM:
        return "medium"
    if duration_s <= LATENCY_SLOW:
        return "slow"
    return "very_slow"


def score_tool_accuracy(
    tool_calls: list[ToolCall],
    expected_tools: list[str],
    final_response: str,
    duration_s: float,
) -> ToolAccuracyResult:
    """Deterministic tool-accuracy score. Starts at 100, penalties subtract."""
    tool_names = [c.name for c in tool_calls]
    tool_set = set(tool_names)
    expected_set = set(expected_tools)

    missing = [t for t in expected_tools if t not in tool_set]
    extra = [t for t in tool_set if t not in expected_set]
    counts: dict[str, int] = {}
    for n in tool_names:
        counts[n] = counts.get(n, 0) + 1
    max_repeats = max(counts.values()) if counts else 0

    retry_count = 0
    for i in range(1, len(tool_names)):
        if tool_names[i] == tool_names[i - 1]:
            retry_count += 1

    error_count = sum(1 for c in tool_calls if c.error)
    explosion = max_repeats > EXPLOSION_THRESHOLD
    zero_calls = len(tool_calls) == 0

    # Crude clarification detection: no tool calls AND the response ends with a
    # question mark or starts with phrasings that indicate asking the user.
    clarification_markers = (
        "could you", "can you clarify", "which service", "which slo",
        "please specify", "could you provide", "could you share",
    )
    lower_tail = final_response.strip().lower()
    asked_clarification = zero_calls and (
        lower_tail.endswith("?")
        or any(m in lower_tail for m in clarification_markers)
    )

    score = 100.0
    penalties: list[dict[str, Any]] = []

    def _p(amount: float, reason: str) -> None:
        nonlocal score
        score -= amount
        penalties.append({"amount": amount, "reason": reason})

    if zero_calls and expected_tools:
        # Auto-fail: no tools called when they were expected.
        _p(100, "Zero tool calls when tools were expected")
    else:
        if missing:
            penalty = min(40 * len(missing), 80)
            _p(penalty, f"Missing expected tools: {missing}")
        if extra:
            _p(5 * len(extra), f"Extra tools called: {extra}")
        if explosion:
            _p(20, f"Explosion detected: max tool repeats = {max_repeats}")
        if retry_count:
            _p(min(5 * retry_count, 15), f"Back-to-back tool retries: {retry_count}")
        if error_count:
            _p(min(10 * error_count, 20), f"Tool call errors: {error_count}")
        if asked_clarification:
            _p(30, "Asked clarification instead of calling tools")

    grade = _latency_grade(duration_s)
    if grade == "very_slow":
        _p(15, f"Very slow run ({duration_s:.1f}s)")
    elif grade == "slow":
        _p(5, f"Slow run ({duration_s:.1f}s)")

    score = max(0.0, score)
    coverage = 0.0
    if expected_tools:
        covered = len([t for t in expected_tools if t in tool_set])
        coverage = 100.0 * covered / len(expected_tools)

    return ToolAccuracyResult(
        score=round(score, 1),
        coverage_pct=round(coverage, 1),
        missing_tools=missing,
        extra_tools=extra,
        tool_call_count=len(tool_calls),
        max_tool_repeats=max_repeats,
        explosion_detected=explosion,
        retry_count=retry_count,
        asked_clarification=asked_clarification,
        zero_tool_calls=zero_calls,
        error_count=error_count,
        duration_s=round(duration_s, 2),
        latency_grade=grade,
        penalties=penalties,
    )


def combine_case_score(tool_accuracy: float, tool_correctness: float) -> float:
    """Weighted average of the two sub-scores."""
    return round(0.5 * tool_accuracy + 0.5 * tool_correctness, 1)
