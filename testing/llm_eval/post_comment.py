"""Post the LLM eval result as a PR comment.

Runs in a separate job that has `pull-requests: write` but no AWS access
and no direct LLM access. Its only input is the JSON artifact produced by
run_eval.py. This enforces the read/write job separation mitigation from
AWS Security's GenAI-in-GitHub guidance: the LLM job can reason but can't
write to the PR; this job can write to the PR but can't reason.

We also re-run the secret redactor here as defense in depth, in case the
artifact ever grows to carry richer content.

Environment variables:
    EVAL_RESULT_PATH   Path to the result JSON from run_eval.py.
    GITHUB_TOKEN       Auth token (set automatically by Actions).
    GITHUB_REPOSITORY  owner/repo (set automatically by Actions).
    PR_NUMBER          PR number.
    COMMENT_TAG        Stable tag to identify our comment for upserts.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path


SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("aws_access_key_id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("aws_secret_access_key", re.compile(r"(?i)aws_secret_access_key\s*[=:]\s*\S+")),
    ("bearer_token", re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{20,}")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
]

COMMENT_TAG_DEFAULT = "<!-- llm-eval-comment -->"


def _redact(text: str) -> str:
    for _, pattern in SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def _gh_request(
    method: str,
    url: str,
    token: str,
    body: dict | None = None,
) -> dict | list | None:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "llm-eval-post-comment")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = resp.read()
            return json.loads(payload) if payload else None
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API {method} {url} failed: {e.code} {msg}")


def _build_body(result: dict, tag: str) -> str:
    status_icon = "✅" if result.get("status") == "passed" else "❌"
    response = _redact(result.get("response_text", ""))
    redactions = result.get("redactions") or []

    lines = [
        tag,
        "## 🤖 LLM Eval Results",
        "",
        f"**Model:** `{result.get('model_id')}`  ",
        f"**Status:** {status_icon} {result.get('status', 'unknown')}",
        "",
        "### Model response",
        "",
        f"> {response.strip()}",
        "",
        "### Token usage",
        "",
        "| Input | Output |",
        "|------:|-------:|",
        f"| {result.get('input_tokens')} | {result.get('output_tokens')} |",
    ]
    if redactions:
        lines += [
            "",
            f"> ⚠️ Redacted patterns from model output: `{', '.join(redactions)}`",
        ]
    lines += [
        "",
        "_This comment is posted automatically by the `llm-eval` workflow._",
    ]
    return "\n".join(lines)


def main() -> int:
    token = os.environ["GITHUB_TOKEN"]
    repo = os.environ["GITHUB_REPOSITORY"]
    pr_number = os.environ["PR_NUMBER"]
    tag = os.environ.get("COMMENT_TAG", COMMENT_TAG_DEFAULT)
    result_path = Path(os.environ["EVAL_RESULT_PATH"])

    result = json.loads(result_path.read_text(encoding="utf-8"))
    body = _build_body(result, tag)

    list_url = (
        f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments"
        f"?per_page=100"
    )
    comments = _gh_request("GET", list_url, token) or []
    existing = next(
        (c for c in comments if isinstance(c, dict) and tag in c.get("body", "")),
        None,
    )

    if existing:
        url = existing["url"]
        _gh_request("PATCH", url, token, {"body": body})
        print(f"[post-comment] Updated comment {url}")
    else:
        _gh_request("POST", list_url, token, {"body": body})
        print("[post-comment] Created new comment")

    return 0


if __name__ == "__main__":
    sys.exit(main())
