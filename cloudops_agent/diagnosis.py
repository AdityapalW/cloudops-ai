"""Evidence-backed Cloud Run triage. Rules report observations, not proven causes."""

import json
from datetime import datetime, timezone
from urllib.parse import urlencode

from .gcp import GCPError, _context, _service_name, _tool, get_gcp_service, list_gcp_logs
from .observability import get_gcp_service_metrics, list_gcp_revisions
from .redaction import redact


def build_report(service_name, project, region, sources):
    """Build the same deterministic report for live tools and offline demo fixtures."""
    evidence, findings, gaps = [], [], []
    observed_at = datetime.now(timezone.utc).isoformat()

    def add_evidence(source, data):
        identifier = f"E{len(evidence) + 1}"
        evidence.append(
            {"id": identifier, "source": source, "observed_at": observed_at, "data": redact(data)}
        )
        return identifier

    def finding(code, level, observation, refs, check):
        findings.append(
            {
                "code": code,
                "severity": level,
                "observation": observation,
                "evidence_ids": refs,
                "suggested_check": check,
            }
        )

    for source in ("service", "revisions", "logs", "metrics"):
        if source not in sources:
            gaps.append({"source": source, "reason": "Evidence source was not collected."})
    refs = {}
    for source, result in sources.items():
        if "error" in result:
            gaps.append({"source": source, "error": result["error"]})
            continue
        if result.get("truncated") or result.get("partial"):
            gaps.append(
                {
                    "source": source,
                    "reason": "Results are truncated or incomplete.",
                    "errors": result.get("errors", {}),
                }
            )
        refs[source] = add_evidence(source, result)

    service = sources.get("service", {}).get("service", {})
    if service:
        condition = service.get("terminal_condition") or {}
        state = condition.get("state")
        if service.get("reconciling"):
            finding(
                "deployment_in_progress",
                "info",
                "Cloud Run is still reconciling this service.",
                [refs["service"]],
                "Recheck readiness after reconciliation finishes; current state may be transitional.",
            )
        elif state == "CONDITION_FAILED":
            finding(
                "service_not_ready",
                "error",
                "Cloud Run reports a failed terminal readiness condition.",
                [refs["service"]],
                "Inspect the condition reason/message and the newest revision's startup logs.",
            )
        elif state != "CONDITION_SUCCEEDED":
            gaps.append(
                {"source": "service", "reason": "Service readiness was not explicitly successful or failed."}
            )
        created = service.get("latest_created_revision")
        ready = service.get("latest_ready_revision")
        if created and ready and created != ready:
            finding(
                "revision_not_promoted",
                "warning",
                "The newest created revision differs from the latest ready revision.",
                [refs["service"]],
                "Compare revision readiness and traffic allocation. A failed new revision may leave an older revision serving normally.",
            )

    if "service" in refs and not service:
        gaps.append({"source": "service", "reason": "Service details were empty."})

    logs = sources.get("logs", {})
    entries = logs.get("entries", [])
    if entries and "logs" in refs:
        finding(
            "error_logs_present",
            "warning",
            f"Found {len(entries)} ERROR-or-higher log entries in the requested window.",
            [refs["logs"]],
            "Group errors by revision and timestamp; compare them with revision creation times and affected requests.",
        )
        text = json.dumps(redact(entries)).lower()
        for pattern, code, description, check in (
            (
                ("out of memory", "memory limit", "oomkilled"),
                "possible_memory_pressure",
                "Logs contain a memory-exhaustion signature.",
                "Check revision memory limits and memory utilization before changing capacity.",
            ),
            (
                ("failed to start", "failed to listen", "startup probe failed"),
                "possible_startup_failure",
                "Logs contain a container startup failure signature.",
                "Check the container port, startup probe, entrypoint, and revision condition messages.",
            ),
            (
                ("permission denied", "permission_denied"),
                "possible_dependency_permission",
                "Logs contain a permission-denied signature.",
                "Identify the failed dependency and inspect the runtime service account's permissions.",
            ),
        ):
            if any(term in text for term in pattern):
                finding(
                    code,
                    "warning",
                    description + " This suggests a cause; it does not prove one.",
                    [refs["logs"]],
                    check,
                )

    metrics = sources.get("metrics", {})
    if "metrics" in refs:
        count = metrics.get("request_count")
        rate = metrics.get("server_error_rate")
        if count is None:
            gaps.append(
                {
                    "source": "metrics",
                    "reason": "No request samples are available; traffic health is unknown.",
                }
            )
        elif count == 0:
            gaps.append(
                {
                    "source": "metrics",
                    "reason": "Returned request samples contain no traffic; successful request handling is unverified.",
                }
            )
        elif rate is not None and rate > 0:
            finding(
                "server_errors_observed",
                "error",
                f"Observed HTTP 5xx responses: {metrics.get('server_error_count')} of {count} requests ({rate:.1%}).",
                [refs["metrics"]],
                "Compare the per-revision error counts with traffic allocation and logs. These are interval totals, not a before/after causal analysis.",
            )

    revisions = sources.get("revisions", {}).get("revisions", [])
    newest = service.get("latest_created_revision", "").rsplit("/", 1)[-1]
    for revision in revisions:
        if newest and revision.get("name", "").rsplit("/", 1)[-1] == newest:
            if not revision.get("reconciling") and any(
                c.get("state") == "CONDITION_FAILED" for c in revision.get("conditions", [])
            ):
                finding(
                    "latest_revision_failed",
                    "error",
                    "The newest revision reports a failed readiness condition.",
                    [refs["revisions"], refs["service"]],
                    "Inspect the revision condition and startup logs; check whether an older ready revision still handles traffic.",
                )
    if "revisions" in refs and not revisions:
        gaps.append({"source": "revisions", "reason": "No revision history was returned."})
    status = (
        "issues_detected"
        if any(f["severity"] in {"error", "warning"} for f in findings)
        else ("insufficient_evidence" if gaps else "no_issue_detected")
    )
    if service.get("reconciling") and status == "no_issue_detected":
        status = "in_progress"
    query = urlencode({"project": project})
    return {
        "provider": "gcp",
        "project": project,
        "region": region,
        "service_name": service_name,
        "observed_at": observed_at,
        "status": status,
        "findings": findings,
        "evidence": evidence,
        "gaps": gaps,
        "partial": bool(gaps),
        "console_url": f"https://console.cloud.google.com/run/detail/{region}/{service_name}/metrics?{query}",
        "limitations": [
            "Read-only triage; no rollback, deployment, or IAM changes were performed.",
            "Readiness and sampled telemetry do not prove end-to-end application health.",
            "Revision timing and error signatures are clues, not proof of causation.",
            "Log redaction is best effort; review outputs before sharing.",
        ],
    }


@_tool
def diagnose_gcp_service(service_name: str, hours: int = 1) -> dict:
    """Triage a Cloud Run service using readiness, newest revisions, ERROR logs and request metrics. Returns observations with evidence IDs, possible causes, next checks and explicit data gaps. Hours: 1–24. Does not change infrastructure."""
    _service_name(service_name)
    if isinstance(hours, bool) or not isinstance(hours, int) or not 1 <= hours <= 24:
        raise GCPError("invalid_argument", "hours must be an integer between 1 and 24.")
    project, region = _context()
    sources = {
        "service": get_gcp_service(service_name),
        "revisions": list_gcp_revisions(service_name, limit=10),
        "logs": list_gcp_logs(service_name, severity="ERROR", hours=hours, limit=50),
        "metrics": get_gcp_service_metrics(service_name, hours=hours),
    }
    return build_report(service_name, project, region, sources)
