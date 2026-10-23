"""A deeper Cloud Run workflow that reuses collected evidence and adds targeted checks."""

from copy import deepcopy

from .diagnosis import diagnose_gcp_service
from .gcp import _tool
from .incidents import error_groups, timestamp
from .revisions import compare_gcp_revisions
from .runtime import get_gcp_resource_pressure


def enrich_investigation(diagnosis, pressure, comparison=None):
    report = deepcopy(diagnosis)
    report["workflow"] = "cloud_run_investigation/v1"
    sources = {e["source"]: e["data"] for e in report.get("evidence", [])}
    for source, result in (("resource_pressure", pressure), ("revision_comparison", comparison)):
        if result is None:
            continue
        if "error" in result:
            report["gaps"].append({"source": source, "error": result["error"]})
            continue
        reference = f"E{len(report['evidence']) + 1}"
        report["evidence"].append(
            {"id": reference, "source": source, "observed_at": report["observed_at"], "data": result}
        )
        if result.get("partial") or result.get("truncated") or result.get("data_status") == "no_data":
            report["gaps"].append(
                {
                    "source": source,
                    "reason": "Additional evidence is missing or incomplete.",
                    "errors": result.get("errors", {}),
                }
            )
        if source == "resource_pressure":
            for signal in result.get("pressure_signals", []):
                report["findings"].append(
                    {
                        "code": "resource_pressure_signal",
                        "severity": "warning",
                        "observation": f"{signal['resource']} on revision {signal['revision']} reached an interval mean of {signal['highest_interval_mean']:.1%}, above the {signal['threshold']:.1%} heuristic threshold.",
                        "evidence_ids": [reference],
                        "suggested_check": "Correlate resource utilization with errors and traffic before changing limits; this is not an instantaneous peak.",
                    }
                )
        elif result.get("changes"):
            report["findings"].append(
                {
                    "code": "revision_configuration_changed",
                    "severity": "info",
                    "observation": f"The ready and newest-created revisions differ in {len(result['changes'])} inspected configuration fields.",
                    "evidence_ids": [reference],
                    "suggested_check": "Review these changes alongside revision readiness and error timestamps; a difference alone does not prove causation.",
                }
            )
    grouped = error_groups(sources.get("logs", {}).get("entries", []))
    # The source evidence keeps the original bounded log sample. This is a compact index.
    report["error_patterns"] = [
        {key: value for key, value in group.items() if key != "sample"} for group in grouped["groups"][:10]
    ]
    report["error_pattern_count"] = len(grouped["groups"])
    report["error_pattern_index_truncated"] = len(grouped["groups"]) > 10
    report["timeline"] = []
    for revision in sources.get("revisions", {}).get("revisions", []):
        instant = timestamp(revision.get("created"))
        if instant:
            report["timeline"].append(
                {
                    "timestamp": instant.isoformat(),
                    "type": "revision_created",
                    "revision": revision.get("name"),
                }
            )
    for group in grouped["groups"]:
        if group["first_seen"]:
            report["timeline"].append(
                {
                    "timestamp": group["first_seen"],
                    "type": "first_sampled_error",
                    "signature": group["signature"],
                }
            )
    report["timeline"].sort(key=lambda event: event["timestamp"])
    report["partial"] = bool(report["gaps"])
    if any(f["severity"] in {"error", "warning"} for f in report["findings"]):
        report["status"] = "issues_detected"
    elif report["gaps"]:
        report["status"] = "insufficient_evidence"
    report["limitations"].append(
        "The deeper workflow reuses the initial log/revision sample. Its timeline and error counts remain bounded by that sample."
    )
    return report


@_tool
def investigate_gcp_service(service_name: str, hours: int = 1) -> dict:
    """Deep read-only Cloud Run investigation: base diagnosis, CPU/memory pressure, compact error patterns, timeline, and configuration comparison when newest/ready revisions differ. Reuses logs/revisions and shares a 120-second budget."""
    diagnosis = diagnose_gcp_service(service_name, hours)
    if "error" in diagnosis:
        return diagnosis
    pressure = get_gcp_resource_pressure(service_name, hours)
    service = next(
        (e["data"].get("service", {}) for e in diagnosis.get("evidence", []) if e["source"] == "service"), {}
    )
    before = service.get("latest_ready_revision")
    after = service.get("latest_created_revision")
    comparison = None
    if before and after and before != after:
        comparison = compare_gcp_revisions(service_name, before.rsplit("/", 1)[-1], after.rsplit("/", 1)[-1])
    return enrich_investigation(diagnosis, pressure, comparison)
