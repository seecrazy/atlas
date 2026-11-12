"""Human-readable project health reports."""

from __future__ import annotations

from .db import SCHEMA_VERSION


def _warnings(report: dict) -> list[str]:
    warnings = []
    if report["repo_count"] == 0:
        warnings.append("no repositories are indexed")
    if report["file_count"] == 0:
        warnings.append("no files are indexed")
    if report["diagnostics"].get("error"):
        warnings.append("index diagnostics contain errors")
    unresolved = report.get("references_by_resolution", {}).get("unresolved", 0)
    ambiguous = report.get("references_by_resolution", {}).get("ambiguous", 0)
    if unresolved or ambiguous:
        warnings.append(f"{unresolved + ambiguous} references are ambiguous or unresolved")
    if report.get("memory_by_status", {}).get("stale"):
        warnings.append("stale memories require review")
    if report.get("pending_approval_count"):
        warnings.append("kernel approvals require review")
    if report.get("pending_patch_count"):
        warnings.append("proposed patches require review")
    if report.get("failed_agent_run_count"):
        warnings.append("agent runs failed; inspect audit events")
    router_validation = report.get("router_validation")
    if not router_validation:
        warnings.append("router validation has not been run")
    elif router_validation["intent_accuracy"] < 0.8:
        warnings.append("router intent accuracy is below 0.8")
    return warnings


def health_report(report: dict) -> str:
    """Render ATLAS health as deterministic Markdown."""
    lines = [
        "# ATLAS Health Report",
        "",
        f"- Schema version: `{SCHEMA_VERSION}`",
        f"- Repositories: `{report['repo_count']}`",
        f"- Files: `{report['file_count']}`",
        f"- Symbols: `{report['symbol_count']}`",
        f"- References: `{report['reference_count']}`",
        f"- Memories: `{report['memory_count']}`",
        f"- Diagnostics: `{report['diagnostic_count']}`",
        f"- Exported symbol changes: `{report['exported_symbol_change_count']}`",
        f"- Contracts: `{report['contract_count']}`",
        f"- Architecture docs: `{report['architecture_doc_count']}`",
        f"- Semantic memories: `{report['semantic_memory_count']}`",
        f"- Pending approvals: `{report['pending_approval_count']}`",
        f"- Pending patches: `{report['pending_patch_count']}`",
        f"- Agent runs: `{report['agent_run_count']}`",
        f"- Strategy rules: `{report['strategy_rule_count']}`",
        f"- Accepted variants: `{report['accepted_variant_count']}`",
        "",
        "## Language coverage",
        "",
    ]
    if report["indexed_languages"]:
        lines.extend(
            f"- `{language}`: `{count}`"
            for language, count in sorted(report["indexed_languages"].items())
        )
    else:
        lines.append("- none")

    lines.extend(["", "## Repositories", "", "| Repo | Subsystem | Files | Symbols |", "| --- | --- | ---: | ---: |"])
    for repo in report["repos"]:
        lines.append(
            f"| `{repo['name']}` | `{repo['subsystem']}` | {repo['file_count']} | {repo['symbol_count']} |"
        )
    if not report["repos"]:
        lines.append("| none | - | 0 | 0 |")

    lines.extend(["", "## Reference resolution", ""])
    if report.get("references_by_resolution"):
        lines.extend(
            f"- `{status}`: `{count}`"
            for status, count in sorted(report["references_by_resolution"].items())
        )
    else:
        lines.append("- none")

    lines.extend(["", "## Memory status", ""])
    if report.get("memory_by_status"):
        lines.extend(
            f"- `{status}`: `{count}`"
            for status, count in sorted(report["memory_by_status"].items())
        )
    else:
        lines.append("- none")

    lines.extend(["", "## Memory layers", ""])
    if report.get("memory_by_layer"):
        lines.extend(
            f"- `{layer}`: `{count}`"
            for layer, count in sorted(report["memory_by_layer"].items())
        )
    else:
        lines.append("- none")

    lines.extend(["", "## Kernel and evolution", ""])
    lines.extend(
        [
            f"- Contracts: `{report['contract_count']}`",
            f"- Architecture docs: `{report['architecture_doc_count']}`",
            f"- Pending approvals: `{report['pending_approval_count']}`",
            f"- Pending patches: `{report['pending_patch_count']}`",
            f"- Agent runs: `{report['agent_run_count']}`",
            f"- Failed agent runs: `{report['failed_agent_run_count']}`",
            f"- Strategy rules: `{report['strategy_rule_count']}`",
            f"- Accepted variants: `{report['accepted_variant_count']}`",
        ]
    )

    lines.extend(["", "## Index diagnostics", ""])
    if report.get("diagnostics"):
        lines.extend(
            f"- `{level}`: `{count}`"
            for level, count in sorted(report["diagnostics"].items())
        )
    else:
        lines.append("- none")

    lines.extend(["", "## Router validation", ""])
    router_validation = report.get("router_validation")
    if router_validation:
        lines.extend(
            [
                f"- Validated at: `{router_validation['validated_at']}`",
                f"- Cases: `{router_validation['case_count']}`",
                f"- Intent accuracy: `{router_validation['intent_accuracy']}`",
                f"- Role evaluated: `{router_validation['role_evaluated']}`",
                f"- Role accuracy: `{router_validation['role_accuracy']}`",
            ]
        )
    else:
        lines.append("- not run")

    lines.extend(["", "## Warnings", ""])
    warnings = _warnings(report)
    if warnings:
        lines.extend(f"- {warning}" for warning in warnings)
    else:
        lines.append("- none")
    lines.append("")
    return "\n".join(lines)
