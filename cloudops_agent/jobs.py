"""Cloud Run Jobs inventory and execution triage. Never runs or cancels jobs."""

from datetime import datetime, timezone

from .gcp import GCPError, _context, _limit, _pages, _request, _segment, _session, _tool
from .incidents import timestamp
from .redaction import redact
from .revisions import revision_id


def execution_state(execution):
    condition = next((c for c in execution.get("conditions", []) if c.get("type") == "Completed"), {})
    state = condition.get("state")
    if execution.get("completionTime"):
        if execution.get("cancelledCount", 0) or condition.get("executionReason") == "CANCELLED":
            return "cancelled"
        if state == "CONDITION_SUCCEEDED":
            return "succeeded"
        if state == "CONDITION_FAILED" or execution.get("failedCount", 0):
            return "failed"
        return "completed_unknown"
    if execution.get("runningCount", 0) or execution.get("reconciling"):
        return "running"
    if state == "CONDITION_FAILED":
        return "failed"
    return "pending_or_unknown"


def execution_summary(execution):
    return {
        "name": execution.get("name"),
        "created": execution.get("createTime"),
        "started": execution.get("startTime"),
        "completed": execution.get("completionTime"),
        "state": execution_state(execution),
        "task_count": execution.get("taskCount", 0),
        "running_count": execution.get("runningCount", 0),
        "succeeded_count": execution.get("succeededCount", 0),
        "failed_count": execution.get("failedCount", 0),
        "cancelled_count": execution.get("cancelledCount", 0),
        "retried_count": execution.get("retriedCount", 0),
        "conditions": redact(execution.get("conditions", [])),
    }


def _base(project, region):
    return f"https://run.googleapis.com/v2/projects/{_segment(project)}/locations/{_segment(region)}/jobs"


@_tool
def list_gcp_jobs(limit: int = 100) -> dict:
    """List Cloud Run Jobs in the configured region, including task count and most recent execution reference. Does not execute jobs. Limit: 1–500."""
    _limit(limit)
    project, region = _context()
    with _session() as session:
        jobs, truncated = _pages(
            session, _base(project, region), {"pageSize": min(limit, 100)}, "jobs", limit
        )
    return {
        "provider": "gcp",
        "project": project,
        "region": region,
        "jobs": [
            {
                "name": j.get("name"),
                "task_count": j.get("template", {}).get("taskCount"),
                "parallelism": j.get("template", {}).get("parallelism"),
                "execution_count": j.get("executionCount"),
                "latest_execution": j.get("latestCreatedExecution"),
                "terminal_condition": redact(j.get("terminalCondition")),
            }
            for j in jobs
        ],
        "truncated": truncated,
    }


@_tool
def list_gcp_job_executions(job_name: str, limit: int = 20) -> dict:
    """List a Cloud Run job's newest executions, with task outcomes and retry counts. Limit: 1–100. No environment or secret values are returned."""
    revision_id(job_name)
    _limit(limit, 100)
    project, region = _context()
    with _session() as session:
        rows, truncated = _pages(
            session,
            f"{_base(project, region)}/{job_name}/executions",
            {"pageSize": limit},
            "executions",
            limit,
        )
    return {
        "provider": "gcp",
        "project": project,
        "region": region,
        "job_name": job_name,
        "executions": [execution_summary(row) for row in rows],
        "truncated": truncated,
    }


@_tool
def diagnose_gcp_job(job_name: str, stale_after_hours: int = 24) -> dict:
    """Inspect job readiness and the latest 20 execution outcomes. Flags failed/cancelled tasks, retries, and an old latest successful completion using a user-selected threshold. Does not infer a schedule or run/cancel the job."""
    revision_id(job_name)
    if (
        isinstance(stale_after_hours, bool)
        or not isinstance(stale_after_hours, int)
        or not 1 <= stale_after_hours <= 8760
    ):
        raise GCPError("invalid_argument", "stale_after_hours must be an integer between 1 and 8760.")
    project, region = _context()
    with _session() as session:
        job = _request(session, "GET", f"{_base(project, region)}/{job_name}")
    history = list_gcp_job_executions(job_name)
    findings = []
    condition = job.get("terminalCondition", {})
    if condition.get("state") == "CONDITION_FAILED":
        findings.append(
            {
                "code": "job_not_ready",
                "observation": "The job reports a failed readiness condition.",
                "evidence": redact(condition),
            }
        )
    executions = history.get("executions", [])
    for execution in executions:
        if execution["state"] in {"failed", "cancelled"}:
            findings.append(
                {
                    "code": "execution_" + execution["state"],
                    "execution": execution["name"],
                    "observation": "A sampled execution did not complete successfully.",
                    "failed_tasks": execution["failed_count"],
                    "cancelled_tasks": execution["cancelled_count"],
                }
            )
        if execution["retried_count"]:
            findings.append(
                {
                    "code": "tasks_retried",
                    "execution": execution["name"],
                    "observation": f"The execution reports {execution['retried_count']} retried tasks; retries can recover successfully.",
                }
            )
    completed = [timestamp(e["completed"]) for e in executions if e["state"] == "succeeded"]
    completed = [t for t in completed if t]
    latest_success = max(completed) if completed else None
    age = (datetime.now(timezone.utc) - latest_success).total_seconds() / 3600 if latest_success else None
    if age is not None and age > stale_after_hours:
        findings.append(
            {
                "code": "old_successful_execution",
                "observation": f"The latest sampled successful completion is older than {stale_after_hours} hours.",
                "note": "This threshold is user-selected; no schedule was inspected.",
            }
        )
    gaps = []
    if history.get("error"):
        gaps.append({"source": "executions", "error": history["error"]})
    if history.get("truncated"):
        gaps.append({"source": "executions", "reason": "Only the newest 20 executions were inspected."})
    if not executions or latest_success is None:
        gaps.append(
            {
                "source": "executions",
                "reason": "No successful completion was observed in the sampled history.",
            }
        )
    if condition.get("state") != "CONDITION_SUCCEEDED" and not findings:
        gaps.append({"source": "job", "reason": "Job readiness is not explicitly successful."})
    return {
        "provider": "gcp",
        "project": project,
        "region": region,
        "job_name": job_name,
        "status": "attention_needed"
        if findings
        else ("insufficient_evidence" if gaps else "no_issue_detected"),
        "findings": findings,
        "gaps": gaps,
        "partial": bool(gaps),
        "latest_sampled_success_at": latest_success.isoformat() if latest_success else None,
        "latest_sampled_success_age_hours": age,
        "execution_history": history,
        "notes": [
            "A past failure can be followed by a successful run; inspect timestamps before treating it as an active incident.",
            "No schedule, task logs, or external dependency health was inspected.",
        ],
    }
