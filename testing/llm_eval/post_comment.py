"""Post LLM eval scoreboard as a PR comment. Stdlib only."""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

COMMENT_TAG = "<!-- llm-eval-comment -->"


def gh(method: str, url: str, token: str, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "llm-eval-post-comment")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = resp.read()
        return json.loads(payload) if payload else None


def _emoji(status: str) -> str:
    return "✅" if status.lower() == "pass" else "❌"


def _render_body(result: dict, run_url: str | None) -> str:
    summary = result.get("summary", {})
    cases = result.get("cases", [])
    status = summary.get("overall_status", "fail")
    overall_score = summary.get("overall_score", 0)
    threshold = summary.get("threshold", 90)
    cases_passed = summary.get("cases_passed", 0)
    cases_total = summary.get("cases_total", 0)
    duration = summary.get("total_duration_s", 0)

    lines: list[str] = [
        COMMENT_TAG,
        f"## LLM Eval Results: {_emoji(status)} {status.upper()}",
        "",
        f"**Overall score:** `{overall_score}` / threshold `{threshold}`",
        f"**Cases passed:** {cases_passed} / {cases_total}",
        f"**Duration:** {duration:.1f}s",
        f"**Model:** `{result.get('model_id')}`",
        "",
        "### Category scoreboard",
        "",
        "| Category | Average | Passed | Status |",
        "|---|---:|---:|:---:|",
    ]
    categories = summary.get("category_scores", {})
    for cat in sorted(categories):
        stats = categories[cat]
        cat_status = "✅" if stats["average_score"] >= threshold and stats["passed"] == stats["total"] else "❌"
        lines.append(f"| {cat} | {stats['average_score']} | {stats['passed']}/{stats['total']} | {cat_status} |")

    lines += ["", "### Cases", "", "| Case | Category | Tool Acc | Tool Corr | Case Score | Status |",
              "|---|---|---:|---:|---:|:---:|"]
    for c in cases:
        ta = c.get("tool_accuracy", {}).get("score", 0)
        tc = c.get("tool_correctness_score", 0)
        lines.append(
            f"| `{c.get('name')}` | {c.get('category')} | {ta} | {tc} | "
            f"**{c.get('case_score')}** | {_emoji(c.get('status', 'fail'))} |"
        )

    floor = summary.get("floor_violations") or []
    cat_fails = summary.get("category_failures") or []
    if floor or cat_fails:
        lines += ["", "### Why this failed", ""]
        if cat_fails:
            lines.append(f"- Category averages below threshold {threshold}: `{', '.join(cat_fails)}`")
        if floor:
            lines.append(f"- Cases below soft floor ({summary.get('soft_floor', 70)}): `{', '.join(floor)}`")

    lines += ["", "### Download full report"]
    if run_url:
        lines.append(f"Download the `eval-report` artifact from [this Actions run]({run_url}) for a detailed per-case breakdown (tool-call traces, judge reasoning, response text).")
    else:
        lines.append("Download the `eval-report` artifact from the Actions run for a detailed per-case breakdown.")

    lines += ["", "_Posted automatically by the `llm-eval` workflow._"]
    return "\n".join(lines)


def main() -> int:
    token = os.environ["GITHUB_TOKEN"]
    repo = os.environ["GITHUB_REPOSITORY"]
    pr_number = os.environ["PR_NUMBER"]
    run_url = os.environ.get("GITHUB_RUN_URL")  # set by workflow
    result = json.loads(Path(os.environ["EVAL_RESULT_PATH"]).read_text())
    body = _render_body(result, run_url)

    list_url = f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments?per_page=100"
    comments = gh("GET", list_url, token) or []
    existing = next(
        (c for c in comments if isinstance(c, dict) and COMMENT_TAG in c.get("body", "")),
        None,
    )
    if existing:
        gh("PATCH", existing["url"], token, {"body": body})
        print(f"[post-comment] Updated {existing['url']}")
    else:
        gh("POST", list_url, token, {"body": body})
        print("[post-comment] Created new comment")
    return 0


if __name__ == "__main__":
    sys.exit(main())
