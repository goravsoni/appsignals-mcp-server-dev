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


def format_structured_audit_output(
    findings: List[Dict[str, Any]],
    banner: str,
    raw_json: str,
    audit_type: str = 'service',
) -> str:
    """Format audit findings into structured markdown that the LLM can directly use.

    This produces Pulsar-style output: executive summary, tables, root causes,
    and recommended actions — so the LLM doesn't have to parse raw JSON.

    Args:
        findings: List of AuditFindings from the API response
        banner: The banner/header text for the audit
        raw_json: The raw JSON response (appended at the end for completeness)
        audit_type: Type of audit ("service", "slo", "operation")

    Returns:
        Structured markdown string with findings summary + raw data
    """
    if not findings:
        return banner + 'No findings. All audited targets appear healthy.\n'

    # Group findings by severity
    critical = []
    warning = []
    info = []
    for f in findings:
        sev = (f.get('Severity') or 'INFO').upper()
        if sev == 'CRITICAL':
            critical.append(f)
        elif sev == 'WARNING':
            warning.append(f)
        else:
            info.append(f)

    # --- Executive Summary ---
    total = len(findings)
    parts = []
    if critical:
        parts.append(f'{len(critical)} critical')
    if warning:
        parts.append(f'{len(warning)} warning')
    if info:
        parts.append(f'{len(info)} informational')

    summary = (
        f'FINDINGS SUMMARY: {total} total findings ({", ".join(parts)}) '
        f'across {audit_type} audit.\n\n'
    )

    # --- Findings Table ---
    summary += '| # | Severity | Target | Finding | Description |\n'
    summary += '|---|----------|--------|---------|-------------|\n'

    ordered = critical + warning + info
    for i, f in enumerate(ordered, 1):
        sev = (f.get('Severity') or 'INFO').upper()
        target = f.get('TargetName', 'Unknown')
        finding_type = f.get('FindingType', '')
        title = f.get('Title', 'No title')
        desc = f.get('Description', '')
        # Truncate long descriptions for the table
        short_desc = (desc[:120] + '...') if len(desc) > 120 else desc
        # Escape pipes in markdown table cells
        short_desc = short_desc.replace('|', '\\|').replace('\n', ' ')
        title = title.replace('|', '\\|').replace('\n', ' ')
        summary += f'| {i} | {sev} | {target} | {title} | {short_desc} |\n'

    summary += '\n'

    # --- Critical Findings Detail ---
    if critical:
        summary += 'CRITICAL FINDINGS DETAIL:\n\n'
        for i, f in enumerate(critical, 1):
            target = f.get('TargetName', 'Unknown')
            title = f.get('Title', 'No title')
            desc = f.get('Description', 'No description')
            finding_id = f.get('FindingId', '')
            summary += f'{i}. [{target}] {title}\n'
            summary += f'   {desc}\n'
            if finding_id:
                summary += f'   FindingId: {finding_id}\n'

            # Extract evidence if available
            evidence = f.get('Evidence', {})
            if evidence:
                if isinstance(evidence, dict):
                    for key, val in evidence.items():
                        if val:
                            val_str = str(val)
                            if len(val_str) > 200:
                                val_str = val_str[:200] + '...'
                            summary += f'   Evidence ({key}): {val_str}\n'
            summary += '\n'

    # --- Warning Findings Detail ---
    if warning:
        summary += 'WARNING FINDINGS:\n\n'
        for i, f in enumerate(warning, 1):
            target = f.get('TargetName', 'Unknown')
            title = f.get('Title', 'No title')
            desc = f.get('Description', 'No description')
            summary += f'{i}. [{target}] {title}: {desc}\n'
        summary += '\n'

    # Append raw JSON for completeness (LLM can reference specific details)
    summary += '---\nRAW AUDIT DATA (for detailed reference):\n'
    summary += raw_json

    return banner + summary


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
