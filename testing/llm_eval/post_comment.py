"""Post LLM eval result as a PR comment. Stdlib only."""

import json
import os
import sys
import urllib.request
from pathlib import Path

COMMENT_TAG = "<!-- llm-eval-comment -->"


def gh(method, url, token, body=None):
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


def main():
    token = os.environ["GITHUB_TOKEN"]
    repo = os.environ["GITHUB_REPOSITORY"]
    pr_number = os.environ["PR_NUMBER"]
    result = json.loads(Path(os.environ["EVAL_RESULT_PATH"]).read_text())

    status = result.get("status", "unknown")
    status_badge = "PASS" if status == "passed" else "FAIL"

    tool_calls = result.get("tool_calls", [])
    tools_called = [c.get("name", "?") for c in tool_calls]
    final_response = (result.get("final_response") or "").strip()
    failure_reasons = result.get("failure_reasons") or []

    lines = [
        COMMENT_TAG,
        f"## LLM Eval Results: {status_badge}",
        "",
        f"**Case:** `{result.get('case_name')}`",
        f"**Model:** `{result.get('model_id')}`",
        f"**Prompt:** {result.get('prompt')!r}",
        "",
        "### Tool calls",
        "",
    ]
    if tool_calls:
        lines.append("| # | Tool | Arguments (truncated) |")
        lines.append("|---|------|-----------------------|")
        for i, c in enumerate(tool_calls, start=1):
            args_str = json.dumps(c.get("arguments", {}))
            if len(args_str) > 120:
                args_str = args_str[:117] + "..."
            lines.append(f"| {i} | `{c.get('name')}` | `{args_str}` |")
    else:
        lines.append("_No tool calls recorded._")
    lines += ["", "### Final response", ""]
    if final_response:
        truncated = final_response if len(final_response) <= 1500 else final_response[:1500] + " [...truncated]"
        for para in truncated.splitlines():
            lines.append(f"> {para}" if para else ">")
    else:
        lines.append("_No response captured._")

    if failure_reasons:
        lines += ["", "### Failures", ""]
        for r in failure_reasons:
            lines.append(f"- {r}")

    lines += [
        "",
        f"Tools called: `{tools_called}`",
        "",
        "_Posted automatically by the `llm-eval` workflow._",
    ]
    body = "\n".join(lines)

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
