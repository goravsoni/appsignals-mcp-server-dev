# LLM Eval on PR — Setup Guide

Label-gated GitHub Actions workflow that runs a Bedrock LLM eval against each PR.
The workflow lives at `.github/workflows/llm-eval.yml`.

## AWS resources (already provisioned)

- **Account**: `436335186916`
- **Region**: `us-east-1`
- **Model**: `us.anthropic.claude-opus-4-5-20251101-v1:0`
- **OIDC provider**: `arn:aws:iam::436335186916:oidc-provider/token.actions.githubusercontent.com`
- **IAM role**: `arn:aws:iam::436335186916:role/GitHubActions-LLMEval-Role`

Trust policy scope: `repo:goravsoni/appsignals-mcp-server-dev:environment:llm-eval`.
Inline permissions: `bedrock:InvokeModel` on the Opus 4.5 inference profile only.

## Flow

When a maintainer applies the `run-llm-eval` label to a PR:

1. `pull_request_target` fires; `gate` job verifies the actor has write permission.
2. `eval` job enters the `llm-eval` GitHub Environment and pauses for the required reviewer to approve.
3. On approval, OIDC mints short-lived credentials for the role, the harness invokes Bedrock, and the response is printed to the log.
4. The label is auto-removed. Subsequent runs require a fresh maintainer action.

## Guidance sources

- [OSA GitHub Actions wiki](https://w.amazon.com/bin/view/Open_Source/GitHub/Actions/) — OIDC, Environment-based gating for PR credentials.
- [AWS Security's GenAI-in-GitHub guidance](https://w.amazon.com/bin/view/AWS_IT_Security/AppSec/SecurityVerValTeam/GenerativeAI/Guidance/GithubAgents/) — authorization gating on all agent triggers, treat credentials to LLM workflows as effectively public, LLM quota exhaustion threat.
- [GitHub's "Preventing pwn requests" post](https://securitylab.github.com/research/github-actions-preventing-pwn-requests/) — canonical reference on `pull_request_target` pitfalls.

## Known gotchas

- **Dependabot PRs** don't get secrets, same as external fork PRs. To run the eval against a Dependabot branch, a maintainer needs to push an empty commit to re-trigger CI with secrets access.
- **`pull_request_target` runs against base branch code**, not PR code. Workflow changes have to land on the base branch before they take effect for PRs.
- The `synchronize` trigger will re-run the eval if someone pushes to an already-labeled PR, but the Environment approval gate still blocks AWS access until a reviewer approves.

## Cost controls (recommended)

- AWS Budget on the account with an alert email (e.g. $20/mo for demo).
- Service Quotas → Bedrock → lower Opus 4.5 RPM/TPM for this account.
- Add a cumulative-token cap in the harness before adding multi-turn agent loops.

## Teardown

```bash
aws iam delete-role-policy \
  --role-name GitHubActions-LLMEval-Role \
  --policy-name InvokeClaudeOpus45
aws iam delete-role --role-name GitHubActions-LLMEval-Role
aws iam delete-open-id-connect-provider \
  --open-id-connect-provider-arn \
    arn:aws:iam::436335186916:oidc-provider/token.actions.githubusercontent.com
```
