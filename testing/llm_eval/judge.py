"""LLM-as-judge for MCP eval cases.

Given the prompt, tool outputs, and the agent's final response, asks Bedrock
Opus 4.5 to score the response on completeness, accuracy, and tool selection,
and return a verdict. Deterministic scoring happens in scorer.py; this file
covers the parts that require judgment on natural-language output.
"""

from __future__ import annotations

import json
import os
from typing import Any

import boto3


JUDGE_PROMPT = """You are an eval judge for an AI agent that uses MCP tools for AWS Application Signals observability.

Score the agent's final response on three dimensions, each 0-100. Also give a pass/fail verdict and flag whether the agent unnecessarily asked the user for clarification instead of using its tools.

**User prompt:**
{prompt}

**Expected behavior (from the case definition):**
{expected_behavior}

**Tool calls the agent made:**
{tool_calls_text}

**Tool outputs the agent received:**
{tool_outputs_text}

**Agent's final response:**
{final_response}

Scoring rubric for each 0-100 dimension:

- **response_completeness**: did the response address everything the user asked? 100 = complete; 50 = partial; 0 = ignored the question.
- **response_accuracy**: is every factual claim supported by the tool outputs? 100 = fully supported; 50 = some unsupported; 0 = fabricated.
- **tool_selection**: did the agent pick the right tools for this prompt? 100 = optimal; 50 = OK with unnecessary extras or missing a useful tool; 0 = wrong tools entirely.

Verdict:
- **pass**: response is acceptable for a customer to receive. Addresses the question, factually grounded, reasonable tool choice.
- **fail**: response would frustrate a customer, contains significant errors, or missed clearly-expected behavior.

Respond with ONLY a JSON object on a single line:
{{"response_completeness": <int 0-100>, "response_accuracy": <int 0-100>, "tool_selection": <int 0-100>, "unnecessary_clarification": <true|false>, "verdict": "pass" or "fail", "reasoning": "<2-3 sentences>"}}
"""


def _format_tool_calls(tool_calls: list[dict[str, Any]]) -> str:
    if not tool_calls:
        return "(none)"
    lines = []
    for i, c in enumerate(tool_calls, start=1):
        args = json.dumps(c.get("arguments", {}))[:400]
        lines.append(f"{i}. {c.get('name')}({args})")
    return "\n".join(lines)


def _format_tool_outputs(tool_outputs: list[str]) -> str:
    if not tool_outputs:
        return "(none captured)"
    # Cap aggregate length so we don't blow past the judge's context window.
    budget = 6000
    out_lines = []
    used = 0
    for i, text in enumerate(tool_outputs, start=1):
        header = f"--- tool output {i} ---"
        remaining = budget - used - len(header) - 10
        if remaining <= 100:
            out_lines.append(f"[truncated: {len(tool_outputs) - i + 1} more tool outputs omitted]")
            break
        snippet = text if len(text) <= remaining else text[:remaining] + "...[truncated]"
        out_lines.append(header)
        out_lines.append(snippet)
        used += len(header) + len(snippet) + 2
    return "\n".join(out_lines)


def judge_response(
    prompt: str,
    expected_behavior: str,
    tool_calls: list[dict[str, Any]],
    tool_outputs: list[str],
    final_response: str,
    *,
    model_id: str,
    region: str,
) -> dict[str, Any]:
    """Calls Bedrock, returns a dict with the rubric scores and a verdict."""
    formatted = JUDGE_PROMPT.format(
        prompt=prompt,
        expected_behavior=expected_behavior or "(none specified)",
        tool_calls_text=_format_tool_calls(tool_calls),
        tool_outputs_text=_format_tool_outputs(tool_outputs),
        final_response=final_response.strip() or "(empty response)",
    )

    client = boto3.client("bedrock-runtime", region_name=region)
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 600,
        "messages": [{"role": "user", "content": formatted}],
    }
    response = client.invoke_model(modelId=model_id, body=json.dumps(body))
    payload = json.loads(response["body"].read())
    output_text = "".join(
        b.get("text", "")
        for b in payload.get("content", [])
        if b.get("type") == "text"
    ).strip()

    # Try parsing the judge's JSON. If it's wrapped in prose or a code fence,
    # pull the first {...} block out.
    parsed = _extract_json(output_text)
    usage = payload.get("usage", {})

    # Normalize fields the scorer cares about.
    return {
        "response_completeness": int(parsed.get("response_completeness", 0)),
        "response_accuracy": int(parsed.get("response_accuracy", 0)),
        "tool_selection": int(parsed.get("tool_selection", 0)),
        "unnecessary_clarification": bool(parsed.get("unnecessary_clarification", False)),
        "verdict": str(parsed.get("verdict", "fail")).lower(),
        "reasoning": str(parsed.get("reasoning", "")).strip(),
        "_raw": output_text,
        "_input_tokens": usage.get("input_tokens"),
        "_output_tokens": usage.get("output_tokens"),
    }


def tool_correctness_score(judge_result: dict[str, Any]) -> float:
    """Combined correctness score from the judge dimensions."""
    completeness = judge_result.get("response_completeness", 0)
    accuracy = judge_result.get("response_accuracy", 0)
    selection = judge_result.get("tool_selection", 0)
    base = (completeness + accuracy + selection) / 3.0
    if judge_result.get("unnecessary_clarification"):
        base -= 20
    return round(max(0.0, base), 1)


def _extract_json(text: str) -> dict[str, Any]:
    """Find the first {...} block in text and parse it. Returns {} on failure."""
    start = text.find("{")
    if start < 0:
        return {}
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                blob = text[start : i + 1]
                try:
                    return json.loads(blob)
                except json.JSONDecodeError:
                    return {}
    return {}
