# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Utilities for presenting audit findings and managing user interaction."""

import json
from loguru import logger
from typing import Any, Dict, List, Optional, Tuple

# Mapping from finding Type to human-readable metric label.
# Prevents agents from conflating fault rate with error rate.
FINDING_TYPE_LABELS = {
    'Availability': 'FAULT RATE',
    'Error': 'ERROR RATE',
    'Latency': 'LATENCY',
}

# Maximum response size in characters before truncation kicks in.
MAX_RESPONSE_CHARS = 18000


def generate_findings_summary_line(findings: List[Dict[str, Any]]) -> str:
    """Generate a one-line natural-language summary of audit findings.

    Placed at the top of the response so the agent can lead with the answer.
    Returns empty string if there are no findings.
    """
    if not findings:
        return ''

    # Count by severity
    high = [f for f in findings if _finding_severity(f) == 'HIGH']
    total = len(findings)

    if not high:
        return f'📋 SUMMARY: {total} finding(s), none high-severity.\n\n'

    # Build summary from the first (most important) HIGH finding
    first = high[0]
    service = first.get('KeyAttributes', {}).get('Name', 'unknown service')
    operation = first.get('Operation', '')
    finding_type = first.get('Type', '')
    label = FINDING_TYPE_LABELS.get(finding_type, finding_type)

    # Extract the key metric from the first auditor result
    desc = ''
    auditor_results = first.get('AuditorResults', [])
    if auditor_results:
        desc = auditor_results[0].get('Description', '')

    parts = [f'🚨 SUMMARY: {len(high)} high-severity finding(s)']
    if total > len(high):
        parts[0] += f' ({total} total)'
    parts.append(f'— {service}')
    if operation:
        parts.append(f'/ {operation}')
    if label:
        parts.append(f'[{label}]')
    if desc:
        # Take just the first sentence of the description
        first_sentence = desc.split('.')[0].strip()
        if first_sentence:
            parts.append(f': {first_sentence}.')

    return ' '.join(parts) + '\n\n'


def label_finding_types(findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Prefix each finding's auditor descriptions with a metric-type label.

    Adds [FAULT RATE], [ERROR RATE], or [LATENCY] prefix to the Description
    field so the agent cannot conflate different metric types.

    Modifies findings in place and returns them.
    """
    for finding in findings:
        finding_type = finding.get('Type', '')
        label = FINDING_TYPE_LABELS.get(finding_type)
        if not label:
            continue

        for auditor_result in finding.get('AuditorResults', []):
            desc = auditor_result.get('Description', '')
            # Don't double-label
            if desc and not desc.startswith('['):
                auditor_result['Description'] = f'[{label}] {desc}'

    return findings


def truncate_findings_by_severity(
    findings: List[Dict[str, Any]], max_chars: int = MAX_RESPONSE_CHARS
) -> Tuple[List[Dict[str, Any]], int]:
    """Truncate findings to fit within a character budget, preserving HIGH severity first.

    Returns:
        Tuple of (truncated_findings, count_of_dropped_findings)
    """
    if not findings:
        return findings, 0

    # Severity priority: HIGH first, then everything else
    severity_order = {'HIGH': 0, 'CRITICAL': 0, 'MEDIUM': 1, 'WARNING': 1, 'LOW': 2, 'INFO': 2}
    sorted_findings = sorted(
        findings, key=lambda f: severity_order.get(_finding_severity(f), 2)
    )

    kept = []
    current_size = 0
    dropped = 0

    for finding in sorted_findings:
        finding_size = len(json.dumps(finding, default=str))
        if current_size + finding_size > max_chars and kept:
            # We've exceeded the budget and already have at least one finding
            dropped += 1
            continue
        kept.append(finding)
        current_size += finding_size

    return kept, dropped


def _finding_severity(finding: Dict[str, Any]) -> str:
    """Extract the severity from a finding, checking AuditorResults if needed."""
    # Check top-level Severity first
    severity = finding.get('Severity', '').upper()
    if severity:
        return severity
    # Fall back to first AuditorResult severity
    for ar in finding.get('AuditorResults', []):
        s = ar.get('Severity', '').upper()
        if s:
            return s
    return 'INFO'


def extract_findings_summary(audit_result: str) -> Tuple[List[Dict[str, Any]], str]:
    """Extract findings from audit result and return summary with original result.

    Returns:
        Tuple of (findings_list, original_result)
    """
    try:
        # Find the JSON part in the audit result
        json_start = audit_result.find('{')
        if json_start == -1:
            return [], audit_result

        json_part = audit_result[json_start:]
        audit_data = json.loads(json_part)

        findings = audit_data.get('AuditFindings', [])
        return findings, audit_result

    except (json.JSONDecodeError, KeyError) as e:
        logger.warning(f'Failed to parse audit result for findings extraction: {e}')
        return [], audit_result


def format_findings_summary(findings: List[Dict[str, Any]], audit_type: str = 'service') -> str:
    """Format findings into a user-friendly summary for selection.

    Args:
        findings: List of audit findings
        audit_type: Type of audit ("service", "slo", "operation")

    Returns:
        Formatted summary string
    """
    if not findings:
        return f'✅ No issues found in {audit_type} audit. All targets appear healthy.'

    # Group findings by severity
    critical_findings = []
    warning_findings = []
    info_findings = []

    for finding in findings:
        severity = finding.get('Severity', 'INFO').upper()
        if severity == 'CRITICAL':
            critical_findings.append(finding)
        elif severity == 'WARNING':
            warning_findings.append(finding)
        else:
            info_findings.append(finding)

    # Build summary
    summary = f'🔍 **{audit_type.title()} Audit Results Summary**\n\n'
    summary += f'Found **{len(findings)} total findings**:\n'

    if critical_findings:
        summary += (
            f'🚨 **{len(critical_findings)} Critical Issues** (require immediate attention)\n'
        )
    if warning_findings:
        summary += f'⚠️  **{len(warning_findings)} Warning Issues** (should be investigated)\n'
    if info_findings:
        summary += f'ℹ️  **{len(info_findings)} Info Issues** (for awareness)\n'

    summary += '\n---\n\n'

    # List findings with selection numbers
    finding_counter = 1

    if critical_findings:
        summary += '🚨 **CRITICAL ISSUES:**\n'
        for finding in critical_findings:
            finding_id = finding.get('FindingId', f'finding-{finding_counter}')
            description = finding.get('Description', 'No description available')
            summary += f'**{finding_counter}.** Finding ID: {finding_id}\n'
            summary += f'   💬 {description}\n\n'
            finding_counter += 1

    if warning_findings:
        summary += '⚠️  **WARNING ISSUES:**\n'
        for finding in warning_findings:
            finding_id = finding.get('FindingId', f'finding-{finding_counter}')
            description = finding.get('Description', 'No description available')
            summary += f'**{finding_counter}.** Finding ID: {finding_id}\n'
            summary += f'   💬 {description}\n\n'
            finding_counter += 1

    if info_findings:
        summary += 'ℹ️  **INFORMATIONAL:**\n'
        for finding in info_findings:
            finding_id = finding.get('FindingId', f'finding-{finding_counter}')
            description = finding.get('Description', 'No description available')
            summary += f'**{finding_counter}.** Finding ID: {finding_id}\n'
            summary += f'   💬 {description}\n\n'
            finding_counter += 1

    summary += '---\n\n'
    summary += '🎯 **Next Steps:**\n'
    summary += "To investigate any specific issue in detail, please let me know which finding number you'd like me to analyze further.\n"
    summary += 'I can perform comprehensive root cause analysis including traces, logs, metrics, and dependencies.\n\n'
    summary += '**Example:** "Please investigate finding #1 in detail" or "Show me root cause analysis for finding #3"\n'

    return summary


def create_targeted_audit_request(
    original_targets: List[Dict[str, Any]],
    findings: List[Dict[str, Any]],
    selected_finding_index: int,
    audit_type: str,
) -> Dict[str, Any]:
    """Create a targeted audit request for a specific finding.

    Args:
        original_targets: Original audit targets
        findings: List of all findings
        selected_finding_index: Index of the selected finding (1-based)
        audit_type: Type of audit ("service", "slo", "operation")

    Returns:
        Dictionary with targeted audit parameters
    """
    if selected_finding_index < 1 or selected_finding_index > len(findings):
        raise ValueError(
            f'Invalid finding index {selected_finding_index}. Must be between 1 and {len(findings)}'
        )

    selected_finding = findings[selected_finding_index - 1]
    target_name = selected_finding.get('TargetName', '')

    # Find the matching target from original targets
    targeted_targets = []

    for target in original_targets:
        target_matches = False

        if audit_type == 'service':
            service_data = target.get('Data', {}).get('Service', {})
            service_name = service_data.get('Name', '')
            if service_name == target_name:
                target_matches = True
        elif audit_type == 'slo':
            slo_data = target.get('Data', {}).get('Slo', {})
            slo_name = slo_data.get('SloName', '')
            if slo_name == target_name:
                target_matches = True
        elif audit_type == 'operation':
            service_op_data = target.get('Data', {}).get('ServiceOperation', {})
            service_data = service_op_data.get('Service', {})
            service_name = service_data.get('Name', '')
            operation = service_op_data.get('Operation', '')
            # For operations, target name might be "service-name:operation"
            if f'{service_name}:{operation}' == target_name or service_name == target_name:
                target_matches = True

        if target_matches:
            targeted_targets.append(target)

    if not targeted_targets:
        # If we can't find exact match, create a new target based on the finding
        logger.warning(
            f'Could not find exact target match for finding {selected_finding_index}, creating new target'
        )
        if audit_type == 'service':
            targeted_targets = [
                {'Type': 'service', 'Data': {'Service': {'Type': 'Service', 'Name': target_name}}}
            ]
        elif audit_type == 'slo':
            targeted_targets = [{'Type': 'slo', 'Data': {'Slo': {'SloName': target_name}}}]

    return {
        'targets': targeted_targets,
        'finding': selected_finding,
        'auditors': 'all',  # Use all auditors for comprehensive root cause analysis
    }


def format_detailed_finding_analysis(finding: Dict[str, Any], detailed_result: str) -> str:
    """Format the detailed analysis result for a specific finding.

    Args:
        finding: The specific finding being analyzed
        detailed_result: The detailed audit result

    Returns:
        Formatted analysis string
    """
    target_name = finding.get('TargetName', 'Unknown Target')
    finding_type = finding.get('FindingType', 'Unknown')
    title = finding.get('Title', 'No title')
    severity = finding.get('Severity', 'INFO').upper()

    # Severity emoji mapping
    severity_emoji = {'CRITICAL': '🚨', 'WARNING': '⚠️', 'INFO': 'ℹ️'}

    analysis = f'{severity_emoji.get(severity, "ℹ️")} **DETAILED ROOT CAUSE ANALYSIS**\n\n'
    analysis += f'**Target:** {target_name}\n'
    analysis += f'**Issue Type:** {finding_type}\n'
    analysis += f'**Severity:** {severity}\n'
    analysis += f'**Title:** {title}\n\n'

    # Add the original finding description if available
    description = finding.get('Description', '')
    if description:
        analysis += f'**Issue Description:**\n{description}\n\n'

    analysis += '---\n\n'
    analysis += '**COMPREHENSIVE ANALYSIS RESULTS:**\n\n'
    analysis += detailed_result

    return analysis


def format_pagination_info(
    has_wildcards: bool,
    names_in_batch: list,
    returned_next_token: Optional[str],
    unix_start: int,
    unix_end: int,
    tool_name: str,
    max_param_name: str,
    max_param_value: int,
    item_type: str = 'services',
) -> str:
    """Helper function to format pagination information for audit tools.

    Args:
        has_wildcards: Whether wildcards were used
        names_in_batch: List of item names processed in this batch
        returned_next_token: Token for next batch, if any
        unix_start: Start time as unix timestamp
        unix_end: End time as unix timestamp
        tool_name: Name of the audit tool (e.g., 'audit_services')
        max_param_name: Name of the max parameter (e.g., 'max_services')
        max_param_value: Value of the max parameter
        item_type: Type of items being processed (e.g., 'services', 'SLOs')

    Returns:
        Formatted pagination information string
    """
    if not has_wildcards or not names_in_batch:
        return ''

    result = ''

    if returned_next_token:
        # Convert unix timestamps to string format
        start_time_str = str(unix_start)
        end_time_str = str(unix_end)
        result += f'\n\n📊 Processed {len(names_in_batch)} {item_type} in this batch:\n'
        for name in names_in_batch:
            result += f'   • {name}\n'

        result += f'\n\n🔄 PAGINATION: More {item_type} available!\n'
        result += f'⚠️ IMPORTANT: To continue auditing remaining {item_type}, use:\n'
        result += f'   {tool_name}(\n'
        result += f'       start_time="{start_time_str}",\n'
        result += f'       end_time="{end_time_str}",\n'
        result += f'       next_token="{returned_next_token}",\n'
        result += f'       {max_param_name}={max_param_value}\n'
        result += '   )\n'
    else:
        result += f'\n\n✅ PAGINATION: Complete! This was the last batch of {item_type}.\n'
        result += f'📊 Processed {len(names_in_batch)} {item_type} in final batch:\n'
        for name in names_in_batch:
            result += f'   • {name}\n'

    return result
