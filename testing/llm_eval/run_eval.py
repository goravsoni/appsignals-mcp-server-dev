"""Orchestrator for the Application Signals MCP LLM eval suite.

Walks every case JSON under cases/{category}/, runs each one through the MCP
server with stubbed AWS calls, scores it (programmatic + LLM-as-judge),
writes a JSON run result plus a standalone HTML report, and exits non-zero
if any threshold is missed.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from botocore.stub import Stubber


HARNESS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(HARNESS_DIR))

from judge import judge_response, tool_correctness_score  # noqa: E402
from reporter import render_report  # noqa: E402
from scorer import ToolCall, combine_case_score, score_tool_accuracy  # noqa: E402


FIXTURES_DIR = HARNESS_DIR / "fixtures"
CASES_DIR = HARNESS_DIR / "cases"
DEFAULT_THRESHOLD = 90
SOFT_FLOOR = 70  # no individual case may score below this


def _log(msg: str) -> None:
    print(f"[llm-eval] {msg}", flush=True)


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _discover_cases() -> list[Path]:
    """Find every case JSON under cases/{category}/ subdirs."""
    paths: list[Path] = []
    if not CASES_DIR.is_dir():
        return paths
    for category_dir in sorted(p for p in CASES_DIR.iterdir() if p.is_dir()):
        for case_file in sorted(category_dir.glob("*.json")):
            paths.append(case_file)
    return paths


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# MCP hosting
# ---------------------------------------------------------------------------

_mcp_thread: threading.Thread | None = None
_mcp_port: int | None = None


def _ensure_mcp_server() -> str:
    """Start the MCP server in-process once and return its URL.

    We reuse a single server across all cases. Each case re-activates a fresh
    Stubber on the same underlying boto3 client.
    """
    global _mcp_thread, _mcp_port
    if _mcp_port is not None:
        return f"http://127.0.0.1:{_mcp_port}/mcp/"

    from awslabs.cloudwatch_applicationsignals_mcp_server import server as mcp_server_module

    mcp = mcp_server_module.mcp
    port = _pick_free_port()
    mcp.settings.host = "127.0.0.1"
    mcp.settings.port = port

    def run() -> None:
        try:
            mcp.run(transport="streamable-http")
        except Exception as exc:  # noqa: BLE001
            _log(f"MCP server thread crashed: {exc!r}")

    t = threading.Thread(target=run, name="mcp-server", daemon=True)
    t.start()

    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                _mcp_thread = t
                _mcp_port = port
                url = f"http://127.0.0.1:{port}/mcp/"
                _log(f"MCP server listening on {url}")
                return url
        except OSError:
            time.sleep(0.25)
    raise RuntimeError("MCP server failed to become ready within 30s")


# ---------------------------------------------------------------------------
# Stubber
# ---------------------------------------------------------------------------

def _activate_case_stubs(case: dict) -> Stubber:
    """Activate Stubber with fixtures for one case. Caller deactivates."""
    from awslabs.cloudwatch_applicationsignals_mcp_server import aws_clients

    client = aws_clients.applicationsignals_client
    stubber = Stubber(client)

    fixtures = case.get("fixtures", {})
    # Map case-fixture keys to boto3 operation names. The tool set grows as
    # we add more cases.
    operation_to_fixture = {
        "list_services": fixtures.get("list_services"),
        "list_audit_findings": fixtures.get("list_audit_findings"),
        "list_service_level_objectives": fixtures.get("list_slos"),
    }
    # Fill many repeats so wildcard expansion + batching doesn't run out.
    for _ in range(15):
        for op_name, fixture_name in operation_to_fixture.items():
            if not fixture_name:
                continue
            response = _load_fixture(fixture_name)
            stubber.add_response(op_name, response)

    stubber.activate()
    return stubber


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------

def _run_case(mcp_url: str, case: dict, model_id: str, region: str) -> dict[str, Any]:
    """Run a single case. Returns a result dict ready for the report."""
    from mcp.client.streamable_http import streamablehttp_client
    from strands import Agent
    from strands.models import BedrockModel
    from strands.tools.mcp.mcp_client import MCPClient

    started = time.monotonic()
    stubber = _activate_case_stubs(case)

    def transport():
        return streamablehttp_client(mcp_url)

    mcp_client = MCPClient(transport)
    bedrock = BedrockModel(model_id=model_id, region_name=region)

    tool_calls: list[ToolCall] = []
    tool_outputs: list[str] = []
    final_response = ""
    error: str | None = None

    try:
        with mcp_client:
            mcp_tools = mcp_client.list_tools_sync()
            agent = Agent(model=bedrock, tools=mcp_tools)
            result = agent(case["prompt"])
            final_response = str(result)

            # Walk the conversation for toolUse + toolResult blocks.
            for msg in agent.messages:
                for block in msg.get("content", []) or []:
                    if not isinstance(block, dict):
                        continue
                    if "toolUse" in block:
                        tu = block["toolUse"]
                        tool_calls.append(ToolCall(
                            name=tu.get("name", "unknown"),
                            arguments=tu.get("input", {}) or {},
                        ))
                    elif "toolResult" in block:
                        tr = block["toolResult"]
                        text_parts = []
                        for c in tr.get("content", []) or []:
                            if isinstance(c, dict) and "text" in c:
                                text_parts.append(c["text"])
                        is_error = tr.get("status") == "error"
                        if is_error and tool_calls:
                            tool_calls[-1].error = True
                        if text_parts:
                            tool_outputs.append("\n".join(text_parts))
    except Exception as exc:  # noqa: BLE001
        error = repr(exc)
        _log(f"Case {case['name']} raised: {error}")
    finally:
        try:
            stubber.deactivate()
        except Exception:  # noqa: BLE001
            pass

    duration_s = time.monotonic() - started

    ta = score_tool_accuracy(
        tool_calls=tool_calls,
        expected_tools=case.get("expected_tool_calls", []),
        final_response=final_response,
        duration_s=duration_s,
    )

    # LLM-as-judge
    try:
        judge = judge_response(
            prompt=case["prompt"],
            expected_behavior=case.get("expected_behavior", ""),
            tool_calls=[{"name": c.name, "arguments": c.arguments} for c in tool_calls],
            tool_outputs=tool_outputs,
            final_response=final_response,
            model_id=model_id,
            region=region,
        )
    except Exception as exc:  # noqa: BLE001
        _log(f"Judge call failed for {case['name']}: {exc!r}")
        judge = {
            "response_completeness": 0,
            "response_accuracy": 0,
            "tool_selection": 0,
            "unnecessary_clarification": False,
            "verdict": "fail",
            "reasoning": f"Judge call failed: {exc!r}",
            "_input_tokens": 0,
            "_output_tokens": 0,
        }

    tc_score = tool_correctness_score(judge)
    case_score = combine_case_score(ta.score, tc_score)
    status = "pass" if case_score >= DEFAULT_THRESHOLD else "fail"

    return {
        "name": case["name"],
        "category": case["category"],
        "prompt": case["prompt"],
        "expected_behavior": case.get("expected_behavior", ""),
        "expected_tool_calls": case.get("expected_tool_calls", []),
        "tool_calls": [{"name": c.name, "arguments": c.arguments, "error": c.error} for c in tool_calls],
        "tool_outputs_captured": len(tool_outputs),
        "final_response": final_response,
        "error": error,
        "tool_accuracy": {
            "score": ta.score,
            "coverage_pct": ta.coverage_pct,
            "missing_tools": ta.missing_tools,
            "extra_tools": ta.extra_tools,
            "tool_call_count": ta.tool_call_count,
            "max_tool_repeats": ta.max_tool_repeats,
            "explosion_detected": ta.explosion_detected,
            "retry_count": ta.retry_count,
            "asked_clarification": ta.asked_clarification,
            "zero_tool_calls": ta.zero_tool_calls,
            "error_count": ta.error_count,
            "duration_s": ta.duration_s,
            "latency_grade": ta.latency_grade,
            "penalties": ta.penalties,
        },
        "tool_correctness_score": tc_score,
        "judge": judge,
        "case_score": case_score,
        "status": status,
        "token_usage": {
            "judge_input": judge.get("_input_tokens"),
            "judge_output": judge.get("_output_tokens"),
        },
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    model_id = os.environ["BEDROCK_MODEL_ID"]
    region = os.environ.get("AWS_REGION", "us-east-1")
    pr_number = os.environ.get("PR_NUMBER", "local")
    result_path = Path(os.environ["EVAL_RESULT_PATH"])
    report_path = Path(os.environ.get("EVAL_REPORT_PATH", str(result_path.with_suffix(".html"))))
    threshold = int(os.environ.get("EVAL_THRESHOLD", DEFAULT_THRESHOLD))

    started_iso = datetime.now(timezone.utc).isoformat()
    _log(f"Model: {model_id}")
    _log(f"Region: {region}")
    _log(f"Threshold: {threshold}")

    case_files = _discover_cases()
    if not case_files:
        raise RuntimeError(f"No case JSONs found under {CASES_DIR}")
    _log(f"Discovered {len(case_files)} cases")

    mcp_url = _ensure_mcp_server()

    cases_out: list[dict[str, Any]] = []
    total_start = time.monotonic()
    for path in case_files:
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw.setdefault("category", path.parent.name)
        raw.setdefault("name", path.stem)
        _log(f"Running case: {raw['category']}/{raw['name']}")
        cases_out.append(_run_case(mcp_url, raw, model_id, region))
    total_duration = time.monotonic() - total_start

    cases_passed = sum(1 for c in cases_out if c["status"] == "pass")
    cases_total = len(cases_out)
    overall_score = round(sum(c["case_score"] for c in cases_out) / max(1, cases_total), 1)

    # Category averages.
    by_category: dict[str, list[dict[str, Any]]] = {}
    for c in cases_out:
        by_category.setdefault(c["category"], []).append(c)
    category_scores = {
        cat: {
            "average_score": round(sum(x["case_score"] for x in lst) / len(lst), 1),
            "passed": sum(1 for x in lst if x["status"] == "pass"),
            "total": len(lst),
        }
        for cat, lst in by_category.items()
    }

    floor_violations = [c["name"] for c in cases_out if c["case_score"] < SOFT_FLOOR]
    category_failures = [cat for cat, stats in category_scores.items() if stats["average_score"] < threshold]

    overall_status = "pass"
    if cases_passed < cases_total:
        overall_status = "fail"
    if category_failures:
        overall_status = "fail"
    if floor_violations:
        overall_status = "fail"
    if overall_score < threshold:
        overall_status = "fail"

    total_input_tokens = sum(c["token_usage"].get("judge_input") or 0 for c in cases_out)
    total_output_tokens = sum(c["token_usage"].get("judge_output") or 0 for c in cases_out)

    summary = {
        "overall_status": overall_status,
        "overall_score": overall_score,
        "threshold": threshold,
        "soft_floor": SOFT_FLOOR,
        "cases_passed": cases_passed,
        "cases_total": cases_total,
        "category_scores": category_scores,
        "category_failures": category_failures,
        "floor_violations": floor_violations,
        "total_duration_s": round(total_duration, 2),
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
    }

    run_result = {
        "generated_at": started_iso,
        "pr_number": pr_number,
        "model_id": model_id,
        "region": region,
        "summary": summary,
        "cases": cases_out,
    }

    result_path.write_text(json.dumps(run_result, indent=2), encoding="utf-8")
    _log(f"Wrote JSON result to {result_path}")

    report_path.write_text(render_report(run_result), encoding="utf-8")
    _log(f"Wrote HTML report to {report_path}")

    _log(f"Overall: {overall_status} ({overall_score}/{threshold})")
    _log(f"Cases passed: {cases_passed}/{cases_total}")
    for cat, stats in category_scores.items():
        _log(f"  {cat}: {stats['average_score']} ({stats['passed']}/{stats['total']} passed)")

    return 0 if overall_status == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
