"""LLM eval harness for the Application Signals MCP Server.

Hosts the PR's MCP server in-process over streamable HTTP on 127.0.0.1,
wires up a botocore Stubber that intercepts AWS calls and returns fixture
data, runs a Strands agent (Bedrock Opus 4.5) with a fixed prompt that
exercises the MCP server's tools, and asserts both that the agent called
the expected tool and that its final response mentions the fixture data.

Env vars:
    BEDROCK_MODEL_ID   Model id (Opus 4.5 inference profile).
    AWS_REGION         Bedrock + Application Signals region.
    PR_NUMBER          Echoed into the result for the PR comment.
    PR_SOURCE_DIR      Path to the checked-out PR code. Unused here since
                       we `pip install -e` the PR before running; kept for
                       parity with the earlier harness signature.
    EVAL_RESULT_PATH   Output JSON path.
    CASE_FILE          Optional path to a case JSON (default: first case
                       under testing/llm_eval/cases/).
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
FIXTURES_DIR = HARNESS_DIR / "fixtures"
CASES_DIR = HARNESS_DIR / "cases"


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _log(msg: str) -> None:
    print(f"[llm-eval] {msg}", flush=True)


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _load_case() -> dict:
    case_path_env = os.environ.get("CASE_FILE")
    if case_path_env:
        path = Path(case_path_env)
    else:
        cases = sorted(CASES_DIR.glob("*.json"))
        if not cases:
            raise FileNotFoundError(f"No case JSON under {CASES_DIR}")
        path = cases[0]
    _log(f"Using case file: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# AWS stubbing
# ---------------------------------------------------------------------------

def _activate_stubber(case: dict) -> Stubber:
    """Attach a Stubber to the MCP server's application-signals client.

    Import inside the function so this happens AFTER we've set any env vars
    the server's aws_clients module reads at import time.
    """
    from awslabs.cloudwatch_applicationsignals_mcp_server import aws_clients

    client = aws_clients.applicationsignals_client
    stubber = Stubber(client)

    # Queue up responses for every call the agent might make. The stubber
    # matches responses in FIFO order to operations. We add the same fixture
    # multiple times because the MCP server can make several list_services
    # or list_audit_findings calls (wildcard expansion + batching).
    list_services_fixture = _load_fixture(case["fixtures"]["list_services"])
    list_audit_findings_fixture = _load_fixture(case["fixtures"]["list_audit_findings"])

    for _ in range(10):
        stubber.add_response("list_services", list_services_fixture)
        stubber.add_response("list_audit_findings", list_audit_findings_fixture)

    stubber.activate()
    _log("Stubber activated on applicationsignals_client")
    return stubber


# ---------------------------------------------------------------------------
# MCP server hosting
# ---------------------------------------------------------------------------

def _start_mcp_server(port: int) -> threading.Thread:
    """Import the MCP server and run it over streamable HTTP in a thread."""
    from awslabs.cloudwatch_applicationsignals_mcp_server import server as mcp_server_module

    mcp = mcp_server_module.mcp  # the FastMCP instance
    # FastMCP settings for streamable HTTP
    mcp.settings.host = "127.0.0.1"
    mcp.settings.port = port
    # The streamable-http transport serves at /mcp/ by default

    def run():
        try:
            mcp.run(transport="streamable-http")
        except Exception as exc:  # noqa: BLE001
            _log(f"MCP server thread crashed: {exc!r}")

    thread = threading.Thread(target=run, name="mcp-server", daemon=True)
    thread.start()

    # Wait for the server to start accepting connections
    deadline = time.time() + 30
    url_host = ("127.0.0.1", port)
    while time.time() < deadline:
        try:
            with socket.create_connection(url_host, timeout=1):
                _log(f"MCP server listening on http://{url_host[0]}:{url_host[1]}/mcp/")
                return thread
        except OSError:
            time.sleep(0.25)
    raise RuntimeError("MCP server failed to become ready within 30s")


# ---------------------------------------------------------------------------
# Strands agent + tool-call capture
# ---------------------------------------------------------------------------

class ToolCallCapture:
    """Collects tool calls made by the agent during its run."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def record(self, name: str, arguments: dict[str, Any]) -> None:
        self.calls.append({"name": name, "arguments": arguments})

    @property
    def tool_names(self) -> list[str]:
        return [c["name"] for c in self.calls]


def _run_agent(mcp_url: str, model_id: str, region: str, prompt: str,
               capture: ToolCallCapture) -> str:
    """Run a Strands agent against the MCP server. Returns the final text."""
    from mcp.client.streamable_http import streamablehttp_client
    from strands import Agent
    from strands.models import BedrockModel
    from strands.tools.mcp.mcp_client import MCPClient

    def transport():
        return streamablehttp_client(mcp_url)

    mcp_client = MCPClient(transport)

    bedrock = BedrockModel(model_id=model_id, region_name=region)

    with mcp_client:
        mcp_tools = mcp_client.list_tools_sync()
        _log(f"Agent sees {len(mcp_tools)} tools from MCP server")

        agent = Agent(model=bedrock, tools=mcp_tools)
        result = agent(prompt)

        # Walk the agent's message history to find every tool use block.
        # Strands stores messages as a list of {role, content} dicts where
        # content is a list of blocks. Tool uses appear as {"toolUse": {...}}.
        for msg in agent.messages:
            for block in msg.get("content", []) or []:
                if isinstance(block, dict) and "toolUse" in block:
                    tu = block["toolUse"]
                    capture.record(
                        name=tu.get("name", "unknown"),
                        arguments=tu.get("input", {}) or {},
                    )

    for call in capture.calls:
        args_preview = json.dumps(call["arguments"])[:200]
        _log(f"Tool call: {call['name']}({args_preview})")

    return str(result)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    started = datetime.now(timezone.utc).isoformat()
    model_id = os.environ["BEDROCK_MODEL_ID"]
    region = os.environ.get("AWS_REGION", "us-east-1")
    pr_number = os.environ.get("PR_NUMBER", "local")
    result_path = Path(os.environ["EVAL_RESULT_PATH"])

    case = _load_case()
    _log(f"Model: {model_id}")
    _log(f"Region: {region}")
    _log(f"Case: {case['name']}")
    _log(f"Prompt: {case['prompt']!r}")

    stubber = _activate_stubber(case)

    port = _pick_free_port()
    _start_mcp_server(port)
    mcp_url = f"http://127.0.0.1:{port}/mcp/"

    capture = ToolCallCapture()
    status = "passed"
    failure_reasons: list[str] = []
    final_response = ""

    try:
        final_response = _run_agent(mcp_url, model_id, region, case["prompt"], capture)
        _log(f"Final response ({len(final_response)} chars)")
    except Exception as exc:  # noqa: BLE001
        status = "failed"
        failure_reasons.append(f"Agent execution raised: {exc!r}")
        _log(f"ERROR: {exc!r}")
    finally:
        try:
            stubber.deactivate()
        except Exception:  # noqa: BLE001
            pass

    # ----- assertions -----
    asserts = case.get("assertions", {})

    for required in asserts.get("required_tool_calls", []):
        if required not in capture.tool_names:
            status = "failed"
            failure_reasons.append(
                f"Agent did not call required tool '{required}'. "
                f"Tools called: {capture.tool_names}"
            )

    needles = asserts.get("response_must_contain_any_of", [])
    required_mentions = asserts.get("response_must_mention_count", 1)
    if needles:
        hits = [n for n in needles if n.lower() in final_response.lower()]
        if len(hits) < required_mentions:
            status = "failed"
            failure_reasons.append(
                f"Response mentioned {len(hits)}/{required_mentions} required needles. "
                f"Hits: {hits}. Needles: {needles}"
            )

    result = {
        "status": status,
        "started_at": started,
        "pr_number": pr_number,
        "model_id": model_id,
        "region": region,
        "case_name": case["name"],
        "prompt": case["prompt"],
        "tool_calls": capture.calls,
        "final_response": final_response,
        "failure_reasons": failure_reasons,
    }
    result_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    _log(f"Status: {status}")
    _log(f"Tools called: {capture.tool_names}")
    _log(f"Wrote result to {result_path}")
    return 0 if status == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
