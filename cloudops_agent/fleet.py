"""Bounded fleet triage with explicit inventory and diagnostic coverage."""

from .diagnosis import diagnose_gcp_service
from .gcp import GCPError, _limit, _tool, list_gcp_services


def _priority(service):
    state = (service.get("ready_condition") or {}).get("state")
    score = 0 if state == "CONDITION_FAILED" else (1 if service.get("reconciling") else 2)
    return score, service["name"]


@_tool
def triage_gcp_services(limit: int = 5, hours: int = 1) -> dict:
    """Triage up to five regional Cloud Run services, prioritizing failed readiness and reconciling services. Inventory is capped at 100 and nested diagnoses share 120 seconds. Returns compact findings and explicit uninspected coverage, without log payloads."""
    _limit(limit, 5)
    if isinstance(hours, bool) or not isinstance(hours, int) or not 1 <= hours <= 24:
        raise GCPError("invalid_argument", "hours must be an integer between 1 and 24.")
    inventory = list_gcp_services(limit=100)
    if "error" in inventory:
        return inventory
    ordered = sorted(inventory["services"], key=_priority)
    results = []
    for service in ordered[:limit]:
        name = service["name"].rsplit("/", 1)[-1]
        report = diagnose_gcp_service(name, hours)
        if "error" in report:
            results.append(
                {
                    "service_name": name,
                    "status": "collection_failed",
                    "error": report["error"],
                    "partial": True,
                }
            )
            continue
        results.append(
            {
                "service_name": name,
                "status": report["status"],
                "observed_at": report["observed_at"],
                "partial": report["partial"],
                "gaps": report["gaps"],
                "findings": [
                    {k: f[k] for k in ("code", "severity", "observation") if k in f}
                    for f in report["findings"]
                ],
                "console_url": report["console_url"],
            }
        )
    omitted = [s["name"].rsplit("/", 1)[-1] for s in ordered[limit:]]
    incomplete = inventory["truncated"] or bool(omitted) or any(r["partial"] for r in results)
    statuses = {r["status"] for r in results}
    status = (
        "attention_needed"
        if "issues_detected" in statuses
        else "incomplete"
        if incomplete
        else "no_services"
        if not results
        else "in_progress"
        if "in_progress" in statuses
        else "no_issue_detected"
    )
    return {
        "provider": "gcp",
        "project": inventory["project"],
        "region": inventory["region"],
        "status": status,
        "services": results,
        "inventory_count": len(ordered),
        "inspected_count": len(results),
        "uninspected_services": omitted,
        "inventory_truncated": inventory["truncated"],
        "partial": incomplete,
        "notes": [
            "Failed readiness is prioritized, followed by reconciliation, then service name; this is not a comprehensive incident ranking.",
            "Compact fleet results omit log payloads. Use per-service diagnosis or investigation to collect full evidence.",
            "The scope is one configured region, not the entire GCP project.",
        ],
    }
