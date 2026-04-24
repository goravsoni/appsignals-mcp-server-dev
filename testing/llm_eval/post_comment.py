"""Post LLM eval result as a PR comment.

Reads the JSON artifact from run_eval.py. No AWS or LLM access here by
design (per AWS Security's read/write job separation guidance).

Env vars:
    GITHUB_TOKEN       Auth (set by Actions).
    GITHUB_REPOSITORY  owner/repo (set by Actions).
    PR_NUMBER          PR number.
    EVAL_RESULT_PATH   Path to the JSON result.
"""

import json
import os
import sys
import urllib.error
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
    result_path = Path(os.environ["EVAL_RESULT_PATH"])

    result = json.loads(result_path.read_text(encoding="utf-8"))
    response = result.get("response_text", "").strip()

    body_lines = [
        COMMENT_TAG,
        "## LLM Eval Results",
        "",
        f"**Model:** `{result.get('model_id')}`",
        f"**Tokens:** {result.get('input_tokens')} in / {result.get('output_tokens')} out",
        "",
        "**Response:**",
        "",
        f"> {response}",
        "",
        "_Posted automatically by the `llm-eval` workflow._",
    ]
    comment_body = "\n".join(body_lines)

    list_url = f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments?per_page=100"
    comments = gh("GET", list_url, token) or []
    existing = next(
        (c for c in comments if isinstance(c, dict) and COMMENT_TAG in c.get("body", "")),
        None,
    )
    if existing:
        gh("PATCH", existing["url"], token, {"body": comment_body})
        print(f"[post-comment] Updated {existing['url']}")
    else:
        gh("POST", list_url, token, {"body": comment_body})
        print("[post-comment] Created new comment")

    return 0


if __name__ == "__main__":
    sys.exit(main())
