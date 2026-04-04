# Application Signals MCP Server — SME Reference

## Architecture Overview

The MCP server is a FastMCP-based Python application that exposes 20 tools for AWS Application Signals observability. It runs as either a local stdio server or a Lambda function behind API Gateway (remote deployment).

### Two Deployment Modes

1. **Local (stdio)**: `mcp.run(transport='stdio')` — used by IDE clients directly
2. **Remote (Lambda)**: Wrapped by `ApplicationSignalsRemoteMCPServer` package which adds FAS credential decryption, per-request context isolation, and JSON-RPC 2.0 handling over API Gateway

### AWS Clients (aws_clients.py)

9 boto3 clients initialized at module level:

- `applicationsignals_client` — core Application Signals API (list_services, get_service, list_audit_findings, list_service_operations, SLO CRUD)
- `cloudwatch_client` — CloudWatch metrics (get_metric_statistics)
- `logs_client` — CloudWatch Logs Insights (start_query, get_query_results) for Transaction Search
- `xray_client` — X-Ray traces (get_trace_summaries)
- `synthetics_client` — CloudWatch Synthetics canaries (describe_canaries, get_canary_runs)
- `s3_client` — canary artifact retrieval (logs, screenshots, HAR files)
- `iam_client` — canary IAM role analysis
- `lambda_client` — canary Lambda function inspection
- `sts_client` — caller identity

Region: `AWS_REGION` env var, defaults to `us-east-1`. Supports endpoint overrides via `MCP_*_ENDPOINT` env vars. Supports `AWS_PROFILE` for local development.

In remote mode, the Lambda handler monkey-patches these module-level clients with per-request FAS-credentialed clients via `_inject_fas_clients()`.

---

## Tool Inventory (20 tools)

### Tier 1: Audit Tools (defined in server.py, use Application Signals ListAuditFindings API)

| Tool                       | Purpose                                                                  | Default Auditors      |
| -------------------------- | ------------------------------------------------------------------------ | --------------------- |
| `audit_services`           | Service-level health: SLO compliance, error rates, latency, dependencies | slo, operation_metric |
| `audit_slos`               | SLO compliance: breach detection, error budget, root cause               | slo                   |
| `audit_service_operations` | Operation-level: latency/error/fault per API endpoint                    | operation_metric      |

All three share the same backend: `execute_audit_api()` in `audit_utils.py` which calls `applicationsignals_client.list_audit_findings()`. They differ in target format and default auditors.

Available auditors: `slo`, `operation_metric`, `trace`, `log`, `dependency_metric`, `top_contributor`, `service_quota`. Use `"all"` for comprehensive analysis.

Key behaviors:

- Wildcard support: `*` in service/SLO names triggers automatic discovery via `expand_service_wildcard_patterns()` / `expand_slo_wildcard_patterns()`
- Pagination: Wildcards process in batches of 5 (configurable via `max_services`/`max_slos`), return `next_token` for continuation
- Instrumentation filtering: `_filter_instrumented_services()` removes UNINSTRUMENTED and AWS_NATIVE services from wildcard expansion
- Batching: Targets > 5 are processed in batches of 5 via `execute_audit_api()`
- Error handling: Batch errors are captured individually, successful batches still return findings

Target formats:

- Service: `[{"Type":"service","Data":{"Service":{"Type":"Service","Name":"*","Environment":"eks:cluster"}}}]`
- Service shorthand: `[{"Type":"service","Service":"my-service"}]` (environment auto-discovered)
- SLO by name: `[{"Type":"slo","Data":{"Slo":{"SloName":"*"}}}]`
- SLO by ARN: `[{"Type":"slo","Data":{"Slo":{"SloArn":"arn:aws:..."}}}]`
- Operation: `[{"Type":"service_operation","Data":{"ServiceOperation":{"Service":{"Type":"Service","Name":"*"},"Operation":"GET /api","MetricType":"Latency"}}}]`

### Tier 2: Service Discovery Tools (service_tools.py)

| Tool                      | Purpose                                                                |
| ------------------------- | ---------------------------------------------------------------------- |
| `list_monitored_services` | Lists all services with instrumented/uninstrumented separation         |
| `get_service_detail`      | Detailed metadata for a single service (platform, metrics, log groups) |
| `query_service_metrics`   | CloudWatch metric timelines with baseline comparison                   |
| `list_service_operations` | Recently active operations for a service (max 24h window)              |

`list_monitored_services` now separates instrumented vs uninstrumented services. If all services are uninstrumented, it short-circuits to onboarding guidance directing users to `get_enablement_guide`.

`query_service_metrics` includes baseline comparison: fetches the previous equivalent time period and shows current vs previous with delta percentage.

### Tier 3: SLO Tools (slo_tools.py)

| Tool        | Purpose                                                         |
| ----------- | --------------------------------------------------------------- |
| `list_slos` | Lists all SLOs with names, ARNs, operations, console deep-links |
| `get_slo`   | Detailed SLO config: thresholds, goals, burn rate settings      |

`list_slos` uses `applicationsignals_client.list_service_level_objectives()` with optional key attribute filtering. Now includes console deep-links per SLO.

`get_slo` uses `applicationsignals_client.get_service_level_objective()` and formats: metric config (period-based or request-based), comparison operators, attainment goals, warning thresholds, burn rate configurations.

### Tier 4: Trace Tools (trace_tools.py)

| Tool                       | Purpose                                                   | Data Source     |
| -------------------------- | --------------------------------------------------------- | --------------- |
| `search_transaction_spans` | CloudWatch Logs Insights queries on `aws/spans` log group | 100% sampled    |
| `query_sampled_traces`     | X-Ray trace summaries with filter expressions             | 5% sampled      |
| `list_slis`                | SLI health summary (healthy/breached/insufficient counts) | SLO metric data |

`search_transaction_spans` checks if Transaction Search is enabled first via `check_transaction_search_enabled()`. If not enabled, returns a structured message with fallback recommendation. Uses `logs_client.start_query()` + polling `get_query_results()`.

`query_sampled_traces` has a 6-hour max time window. Deduplicates traces by fault message. Returns dedup stats. Includes Transaction Search status in response.

`list_slis` uses `SLIReportClient` which queries SLO metric data via CloudWatch `get_metric_data()` to determine breach status per SLO.

### Tier 5: Canary Analysis (server.py + canary_utils.py)

| Tool                      | Purpose                                                   |
| ------------------------- | --------------------------------------------------------- |
| `analyze_canary_failures` | Comprehensive canary failure analysis with auto-discovery |

Now accepts optional `canary_name` (default=''). When empty, calls `synthetics_client.describe_canaries()` to discover all canaries, checks last run status, and analyzes failing ones.

Analysis includes:

- Recent run history (last 5 runs)
- Canary configuration details
- Telemetry and service insights via `get_canary_metrics_and_service_insights()`
- S3 artifact analysis: logs (`analyze_log_files`), screenshots (`analyze_screenshots`), HAR files (`analyze_har_file`)
- IAM role and policy analysis (`analyze_iam_role_and_policies`)
- Resource ARN validation (`check_resource_arns_correct`)
- Disk/memory usage metrics (`extract_disk_memory_usage_metrics`)
- Canary source code retrieval (`get_canary_code`)
- Time-windowed log analysis (`analyze_canary_logs_with_time_window`)

### Tier 6: Change Correlation (change_tools.py)

| Tool                 | Purpose                                                                     |
| -------------------- | --------------------------------------------------------------------------- |
| `list_change_events` | Infrastructure/deployment change events correlated with service performance |

Two modes via `comprehensive_history` parameter:

- `True` (default): Uses `ListEntityEvents` API — requires `service_key_attributes`, returns full change history
- `False`: Uses `ListServiceStates` API — optional attributes, returns current service state with latest changes

### Tier 7: Group Tools (group_tools.py)

| Tool                                  | Purpose                                                   |
| ------------------------------------- | --------------------------------------------------------- |
| `list_group_services`                 | Services belonging to a group (by ServiceGroups metadata) |
| `audit_group_health`                  | Health assessment per group (SLI-first, metrics fallback) |
| `get_group_dependencies`              | Intra-group, cross-group, and external dependency mapping |
| `get_group_changes`                   | Deployment events across group services                   |
| `list_grouping_attribute_definitions` | Custom grouping attribute definitions                     |

All group tools share `_setup_group_tool()` which discovers groups via `list_grouping_attribute_definitions` then finds matching services via `_discover_services_by_group()`.

`audit_group_health` uses SLI-first approach: checks SLO breach status per service. Falls back to raw metrics (fault rate, error rate, latency) with configurable thresholds.

### Tier 8: Enablement (enablement_tools.py)

| Tool                   | Purpose                                                                               |
| ---------------------- | ------------------------------------------------------------------------------------- |
| `get_enablement_guide` | Step-by-step instrumentation guides for EC2/ECS/Lambda/EKS × Python/Java/Node.js/.NET |

Reads markdown templates from `enablement_guides/templates/{platform}/{platform}-{language}-enablement.md`. Requires absolute paths for IaC and app directories.

---

## Key Utility Modules

### audit_utils.py

- `execute_audit_api()` — core audit execution with batching, error handling with actionable guidance
- `_filter_instrumented_services()` — removes UNINSTRUMENTED/AWS_NATIVE services
- `_fetch_instrumented_services_with_pagination()` — paginated instrumented service discovery
- `expand_service_wildcard_patterns()` — wildcard → concrete service targets
- `expand_slo_wildcard_patterns()` — wildcard → concrete SLO targets
- `expand_service_operation_wildcard_patterns()` — wildcard → concrete operation targets
- `parse_auditors()` — parses auditor string, applies defaults

### audit_presentation_utils.py

- `extract_findings_summary()` — parses JSON findings from audit result
- `format_findings_summary()` — severity-grouped display with CRITICAL/WARNING/INFO, time-since-onset for critical findings
- `format_pagination_info()` — pagination continuation guidance

### service_audit_utils.py

- `coerce_service_target()` — tolerant shorthand → canonical format conversion
- `normalize_service_targets()` — validates and normalizes target arrays
- `validate_and_enrich_service_targets()` — auto-discovers Environment for targets missing it

### utils.py

- `parse_timestamp()` — flexible timestamp parsing (unix seconds or datetime strings)
- `console_url_service()` / `console_url_slo()` / `console_url_overview()` — AWS Console deep-links
- `calculate_name_similarity()` — fuzzy matching for service name resolution
- `fetch_metric_stats()` — CloudWatch metric retrieval helper
- `list_services_paginated()` — paginated service listing

### sli_report_client.py

- `SLIReportClient` — generates SLI health reports by querying SLO metric data
- Uses CloudWatch `get_metric_data()` with SLO-derived metric queries
- Determines breach status per SLO based on attainment vs goal

---

## Instrumentation Awareness

Services have an `InstrumentationType` in their `AttributeMaps`:

- `INSTRUMENTED` — actively sending telemetry, paying customer
- `UNINSTRUMENTED` — discovered on AWS workload but not instrumented
- `AWS_NATIVE` — AWS-managed service (e.g., S3, DynamoDB as dependency)

The MCP handles this at two levels:

1. `list_monitored_services` — separates and labels instrumented vs uninstrumented, short-circuits to onboarding if all uninstrumented
2. Audit tools — `_filter_instrumented_services()` silently excludes uninstrumented/AWS_NATIVE from wildcard expansion

---

## Error Handling Patterns

Audit tools return actionable error guidance:

- Throttling → "Do NOT retry with same parameters"
- Access denied → "Check IAM permissions, do NOT retry"
- Not found → "Use list_monitored_services() to verify targets"
- Validation → "Check JSON format and service names"
- Timeout → "Reduce targets or time range"

Generic errors → "Do NOT retry with identical parameters"

---

## Output Enhancements (our additions)

1. **Baseline comparison** in `query_service_metrics` — current vs previous period with delta %
2. **Console deep-links** in `list_monitored_services` and `list_slos`
3. **Time-since-onset** for CRITICAL findings in `format_findings_summary()`
4. **Actionable error messages** in `execute_audit_api()` error handler
5. **Canary auto-discovery** when `canary_name` is omitted
6. **Wildcard guidance** in audit tool docstrings ("use '\*' if user doesn't specify")
7. **Instrumentation-aware listing** with onboarding short-circuit

---

## Configuration

| Env Var                                        | Purpose                     | Default   |
| ---------------------------------------------- | --------------------------- | --------- |
| `AWS_REGION`                                   | AWS region                  | us-east-1 |
| `AWS_PROFILE`                                  | boto3 profile for local dev | (none)    |
| `MCP_CLOUDWATCH_APPLICATION_SIGNALS_LOG_LEVEL` | Log level                   | INFO      |
| `AUDITOR_LOG_PATH`                             | Audit API log file path     | tempdir   |
| `MCP_RUN_FROM`                                 | User agent suffix           | (none)    |
| `MCP_APPLICATIONSIGNALS_ENDPOINT`              | Endpoint override           | (none)    |
| `MCP_LOGS_ENDPOINT`                            | Endpoint override           | (none)    |
| `MCP_CLOUDWATCH_ENDPOINT`                      | Endpoint override           | (none)    |
| `MCP_XRAY_ENDPOINT`                            | Endpoint override           | (none)    |
| `MCP_SYNTHETICS_ENDPOINT`                      | Endpoint override           | (none)    |

Remote-only (Lambda handler):
| `Q_FAS_KMS_KEY_ARN` | KMS key for FAS credential decryption |
| `CONDUCTOR_TOOL_NAME` | Tool name for FAS header lookup | APPLICATIONSIGNALS |
| `STAGE` | Deployment stage | (none) |
| `IS_Q_REQUEST` | Flag for Q Console requests | (none) |
| `LOG_LEVEL` | Lambda log level | INFO |
