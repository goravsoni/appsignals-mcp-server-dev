"""Self-contained HTML report for an LLM eval run.

No CDN dependencies - everything inlines so the report works offline after
being downloaded from a workflow artifact.
"""

from __future__ import annotations

import html
import json
from typing import Any


def _severity_color(score: float) -> str:
    if score >= 90:
        return "#16a34a"
    if score >= 70:
        return "#d97706"
    return "#dc2626"


def _status_badge(status: str) -> str:
    color = "#16a34a" if status.lower() == "pass" else "#dc2626"
    return (
        f'<span style="padding:2px 10px;border-radius:4px;font-size:11px;'
        f'font-weight:600;background:{color}22;color:{color}">{html.escape(status.upper())}</span>'
    )


def _format_tools(tools: list[str], color_bg: str, color_fg: str) -> str:
    if not tools:
        return '<span style="color:#999">(none)</span>'
    spans = []
    for t in tools:
        spans.append(
            f'<span style="display:inline-block;padding:1px 6px;margin:1px;'
            f'background:{color_bg};color:{color_fg};border-radius:4px;'
            f'font-size:10px;font-family:monospace">{html.escape(t)}</span>'
        )
    return "".join(spans)


def _format_tool_calls_table(calls: list[dict[str, Any]]) -> str:
    if not calls:
        return "<em>No tool calls</em>"
    rows = []
    for i, c in enumerate(calls, start=1):
        args = html.escape(json.dumps(c.get("arguments", {}))[:200])
        rows.append(
            f"<tr><td>{i}</td><td><code>{html.escape(c.get('name', '?'))}</code></td>"
            f"<td><code style='font-size:11px'>{args}</code></td></tr>"
        )
    return (
        "<table style='border-collapse:collapse;font-size:12px;width:100%'>"
        "<tr><th style='text-align:left;padding:4px;border-bottom:1px solid #ddd'>#</th>"
        "<th style='text-align:left;padding:4px;border-bottom:1px solid #ddd'>Tool</th>"
        "<th style='text-align:left;padding:4px;border-bottom:1px solid #ddd'>Arguments</th></tr>"
        + "".join(rows)
        + "</table>"
    )


def render_report(run_result: dict[str, Any]) -> str:
    """Render the run result as a standalone HTML document."""
    summary = run_result.get("summary", {})
    cases = run_result.get("cases", [])
    generated = run_result.get("generated_at", "")
    pr_number = run_result.get("pr_number", "local")
    model_id = run_result.get("model_id", "")

    overall_status = summary.get("overall_status", "fail")
    overall_score = summary.get("overall_score", 0)
    cases_passed = summary.get("cases_passed", 0)
    cases_total = summary.get("cases_total", 0)

    rows = []
    response_entries = []
    for i, case in enumerate(cases):
        case_score = case.get("case_score", 0)
        ta = case.get("tool_accuracy", {})
        tc_score = case.get("tool_correctness_score", 0)
        judge = case.get("judge", {})
        tool_calls = case.get("tool_calls", [])
        tools_actual = [c.get("name", "?") for c in tool_calls]
        tools_expected = case.get("expected_tool_calls", [])
        missing = ta.get("missing_tools", [])

        status = case.get("status", "fail")

        rows.append(f"""<tr>
  <td>{i + 1}</td>
  <td><span style="padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;background:#dbeafe;color:#1e40af">{html.escape(case.get("category", "?"))}</span></td>
  <td style="font-size:12px">{html.escape(case.get("name", ""))}</td>
  <td style="font-size:12px;max-width:250px">{html.escape(case.get("prompt", ""))}</td>
  <td style="text-align:center">{ta.get("tool_call_count", 0)}</td>
  <td style="text-align:center">{ta.get("duration_s", 0):.1f}s</td>
  <td><button onclick="showResp({i})" style="background:none;border:1px solid #d1d5db;border-radius:4px;padding:2px 10px;color:#2563eb;font-size:11px;cursor:pointer">View</button></td>
  <td>{_format_tools(tools_actual, "#dcfce7", "#166534")}</td>
  <td>{_format_tools(tools_expected, "#e0e7ff", "#4338ca")}</td>
  <td>{_format_tools(missing, "#fee2e2", "#991b1b")}</td>
  <td style="text-align:center"><span style="font-weight:700;color:{_severity_color(ta.get('score', 0))}">{ta.get("score", 0)}</span><div style="font-size:10px;color:#666">Coverage: {ta.get('coverage_pct', 0):.0f}%</div></td>
  <td style="text-align:center"><span style="font-weight:700;color:{_severity_color(tc_score)}">{tc_score}</span><div style="font-size:10px;color:#666">{html.escape((judge.get('reasoning') or '')[:120])}</div></td>
  <td style="text-align:center"><div><span style="font-weight:700;font-size:16px;color:{_severity_color(case_score)}">{case_score}</span></div>{_status_badge(status)}</td>
</tr>""")

        response_entries.append({
            "prompt": case.get("prompt", ""),
            "response": case.get("final_response", ""),
            "expected_behavior": case.get("expected_behavior", ""),
            "tool_calls_html": _format_tool_calls_table(tool_calls),
            "judge_reasoning": judge.get("reasoning", ""),
            "judge_verdict": judge.get("verdict", ""),
            "penalties": ta.get("penalties", []),
        })

    overall_color = _severity_color(overall_score)
    data_js = json.dumps(response_entries).replace("</", "<\\/")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>AppSignals MCP Eval Report - PR #{html.escape(str(pr_number))}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f3f4f6;color:#111827;padding:20px}}
.header{{text-align:center;padding:24px;background:linear-gradient(135deg,#1e293b 0%,#0f172a 100%);color:#fff;border-radius:12px;margin-bottom:20px}}
.header h1{{font-size:24px;margin-bottom:6px}}
.header .sub{{opacity:.7;font-size:13px}}
.header .score{{font-size:48px;font-weight:700;margin-top:12px;color:{overall_color}}}
.header .score small{{font-size:14px;color:#cbd5e1;display:block;margin-top:4px}}
.stats{{display:flex;gap:16px;justify-content:center;margin-top:12px;font-size:13px}}
.table-card{{background:#fff;border-radius:12px;padding:16px;box-shadow:0 1px 3px rgba(0,0,0,.08);overflow-x:auto}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
thead th{{background:#f9fafb;padding:10px 8px;text-align:left;font-weight:600;color:#4b5563;border-bottom:2px solid #e5e7eb;white-space:nowrap;font-size:12px}}
tbody td{{padding:10px 8px;border-bottom:1px solid #f3f4f6;vertical-align:top}}
tbody tr:hover td{{background:#f9fafb}}
.modal-overlay{{display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,.5);z-index:200;justify-content:center;align-items:center}}
.modal-overlay.open{{display:flex}}
.modal{{background:#fff;border-radius:12px;max-width:1100px;width:95%;max-height:85vh;display:flex;flex-direction:column}}
.modal-header{{display:flex;justify-content:space-between;align-items:center;padding:14px 20px;border-bottom:1px solid #e5e7eb}}
.modal-close{{background:none;border:none;font-size:22px;cursor:pointer;color:#6b7280}}
.modal-close:hover{{color:#111}}
.modal-body{{display:flex;flex:1;overflow:hidden}}
.modal-col{{flex:1;padding:16px;overflow-y:auto;font-size:13px;line-height:1.6}}
.modal-col+.modal-col{{border-left:1px solid #e5e7eb}}
.modal-col h4{{font-size:12px;color:#6b7280;text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px}}
.modal-col pre{{white-space:pre-wrap;font-family:inherit;font-size:13px}}
.penalty{{background:#fef2f2;border-left:3px solid #dc2626;padding:6px 10px;margin:4px 0;font-size:12px}}
</style>
</head>
<body>
<div class="header">
  <h1>Application Signals MCP - Eval Report</h1>
  <div class="sub">PR #{html.escape(str(pr_number))} &middot; {html.escape(str(model_id))}</div>
  <div class="sub">Generated {html.escape(str(generated))}</div>
  <div class="score">{overall_score:.1f}<small>Overall score &middot; threshold {summary.get("threshold", 90)} &middot; {_status_badge(overall_status)}</small></div>
  <div class="stats">
    <div>Cases passed: <b>{cases_passed}/{cases_total}</b></div>
    <div>Duration: <b>{summary.get("total_duration_s", 0):.1f}s</b></div>
    <div>Total tokens (in/out): <b>{summary.get("total_input_tokens", 0)}/{summary.get("total_output_tokens", 0)}</b></div>
  </div>
</div>
<div class="table-card">
<table>
<thead><tr>
  <th>#</th>
  <th>Category</th>
  <th>Case</th>
  <th>Prompt</th>
  <th>Calls</th>
  <th>Duration</th>
  <th>Response</th>
  <th>Actual Tools</th>
  <th>Expected Tools</th>
  <th>Missing</th>
  <th>Tool Accuracy</th>
  <th>Tool Correctness</th>
  <th>Case Score</th>
</tr></thead>
<tbody>
{"".join(rows)}
</tbody>
</table>
</div>

<div class="modal-overlay" id="respModal" onclick="if(event.target===this){{this.classList.remove('open')}}">
  <div class="modal">
    <div class="modal-header"><h3 id="respTitle">Case Detail</h3><button class="modal-close" onclick="document.getElementById('respModal').classList.remove('open')">&times;</button></div>
    <div class="modal-body">
      <div class="modal-col">
        <h4>Prompt</h4>
        <div id="respPrompt"></div>
        <h4 style="margin-top:16px">Expected behavior</h4>
        <div id="respExpected"></div>
        <h4 style="margin-top:16px">Tool calls</h4>
        <div id="respCalls"></div>
        <h4 style="margin-top:16px">Penalties</h4>
        <div id="respPenalties"></div>
      </div>
      <div class="modal-col">
        <h4>Agent response</h4>
        <pre id="respBody"></pre>
        <h4 style="margin-top:16px">Judge verdict</h4>
        <div id="respJudge"></div>
      </div>
    </div>
  </div>
</div>

<script>
const cases = {data_js};
function showResp(i) {{
  const c = cases[i];
  if (!c) return;
  document.getElementById('respPrompt').textContent = c.prompt;
  document.getElementById('respExpected').textContent = c.expected_behavior || '(not specified)';
  document.getElementById('respCalls').innerHTML = c.tool_calls_html;
  document.getElementById('respBody').textContent = c.response || '(empty)';
  const j = c.judge_verdict ? ('[' + c.judge_verdict.toUpperCase() + '] ') : '';
  document.getElementById('respJudge').textContent = j + (c.judge_reasoning || '(no reasoning)');
  const pen = c.penalties && c.penalties.length
    ? c.penalties.map(p => '<div class="penalty"><b>-' + p.amount + '</b> ' + p.reason + '</div>').join('')
    : '<em>No penalties</em>';
  document.getElementById('respPenalties').innerHTML = pen;
  document.getElementById('respModal').classList.add('open');
}}
</script>
</body>
</html>
"""
