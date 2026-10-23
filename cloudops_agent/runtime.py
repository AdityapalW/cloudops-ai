"""Resource-pressure and request-based SLO inspection for Cloud Run."""

import math
from datetime import datetime, timedelta, timezone

from .gcp import GCPError, _context, _service_name, _session, _tool
from .observability import _metric, get_gcp_service_metrics


def _fraction(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise GCPError("invalid_argument", f"{name} must be a finite numeric fraction.")
    if not 0 < value < 1:
        raise GCPError("invalid_argument", f"{name} must be greater than zero and less than one.")


def distribution_summary(rows):
    groups = {}
    for row in rows:
        resource = row.get("resource", {}).get("labels", {})
        metric = row.get("metric", {}).get("labels", {})
        revision, container = (
            resource.get("revision_name", "unknown"),
            metric.get("container_name", "unknown"),
        )
        group = groups.setdefault(
            (revision, container),
            {
                "revision": revision,
                "container": container,
                "sample_count": 0,
                "weighted_sum": 0.0,
                "highest_interval_mean": None,
                "latest_sample_at": None,
            },
        )
        for point in row.get("points", []):
            distribution = point.get("value", {}).get("distributionValue", {})
            count = int(distribution.get("count", 0))
            if count <= 0:
                continue
            mean = float(distribution.get("mean", 0))
            group["sample_count"] += count
            group["weighted_sum"] += mean * count
            previous = group["highest_interval_mean"]
            group["highest_interval_mean"] = max(previous, mean) if previous is not None else mean
            stamp = point.get("interval", {}).get("endTime")
            if stamp and (group["latest_sample_at"] is None or stamp > group["latest_sample_at"]):
                group["latest_sample_at"] = stamp
    output = []
    for group in groups.values():
        weighted_sum = group.pop("weighted_sum")
        group["weighted_mean"] = weighted_sum / group["sample_count"] if group["sample_count"] else None
        output.append(group)
    return output


@_tool
def get_gcp_resource_pressure(service_name: str, hours: int = 1, threshold: float = 0.85) -> dict:
    """Inspect CPU and memory utilization distributions by revision/container. Returns sample-weighted means and highest interval means, not instantaneous peaks. Threshold is a heuristic fraction (default 0.85); hours: 1–24."""
    _service_name(service_name)
    _fraction(threshold, "threshold")
    if isinstance(hours, bool) or not isinstance(hours, int) or not 1 <= hours <= 24:
        raise GCPError("invalid_argument", "hours must be an integer between 1 and 24.")
    project, region = _context()
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    results, errors, signals = {}, {}, []
    truncated = False
    with _session() as session:
        for name, metric in (
            ("cpu", "container/cpu/utilizations"),
            ("memory", "container/memory/utilizations"),
        ):
            try:
                rows, cut, execution_errors = _metric(
                    session, project, region, service_name, metric, start.isoformat(), end.isoformat()
                )
            except GCPError as exc:
                errors[name] = {"code": exc.code, "message": str(exc)}
                continue
            truncated |= cut
            results[name] = distribution_summary(rows)
            if execution_errors:
                errors[name] = {
                    "code": "partial_execution",
                    "message": "Monitoring returned incomplete data.",
                }
            for group in results[name]:
                if group["highest_interval_mean"] is not None and group["highest_interval_mean"] >= threshold:
                    signals.append(
                        {
                            "resource": name,
                            "revision": group["revision"],
                            "container": group["container"],
                            "highest_interval_mean": group["highest_interval_mean"],
                            "threshold": threshold,
                        }
                    )
    available = any(g["sample_count"] for groups in results.values() for g in groups)
    return {
        "provider": "gcp",
        "project": project,
        "region": region,
        "service_name": service_name,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "utilization": results,
        "pressure_signals": signals,
        "errors": errors,
        "truncated": truncated,
        "partial": bool(errors) or truncated,
        "data_status": "available" if available else "no_data",
        "notes": [
            "Utilization is a fraction; multiply by 100 for percent. CPU values can exceed 1.",
            "A threshold crossing is a heuristic signal, not proof of saturation or a recommendation to resize.",
            "Highest interval mean is not an instantaneous peak; distributions are weighted by sample count.",
            "Missing or delayed telemetry does not establish low resource use.",
        ],
    }


def evaluate_slo(metrics, target, minimum_requests):
    count, errors = metrics.get("request_count"), metrics.get("server_error_count")
    rate = metrics.get("server_error_rate")
    reason = None
    if metrics.get("error") or metrics.get("partial") or metrics.get("truncated"):
        reason = "Request telemetry is unavailable or incomplete."
    elif count is None or errors is None or rate is None:
        reason = "Request telemetry is missing or contains no traffic."
    elif count < minimum_requests:
        reason = (
            f"Only {count} requests were observed; at least {minimum_requests} are required by this check."
        )
    observed = 1 - rate if rate is not None else None
    burn = rate / (1 - target) if rate is not None else None
    return {
        "status": "insufficient_evidence"
        if reason
        else ("within_target" if observed >= target else "outside_target"),
        "target_success_fraction": target,
        "observed_success_fraction": observed,
        "request_count": count,
        "bad_request_count": errors,
        "minimum_requests": minimum_requests,
        "observed_burn_rate": burn,
        "reason": reason,
        "definition": "Good requests are all observed responses except HTTP 5xx. HTTP 4xx count as good for this availability definition.",
        "notes": [
            "This evaluates the queried interval only, not a rolling monthly SLO or an alerting policy.",
            "Cloud Run request_count excludes requests that never reach the container, so this is a container-response availability proxy.",
            "Burn rate is observed 5xx fraction divided by allowed error fraction; it is descriptive, not a forecast.",
            "Partial or low-volume observations are reported without a pass/fail conclusion.",
        ],
    }


@_tool
def check_gcp_service_slo(
    service_name: str, target: float = 0.999, hours: int = 1, minimum_requests: int = 100
) -> dict:
    """Evaluate a request-based availability target over 1–24 hours using HTTP 5xx as bad requests. Target is a fraction below 1; low volume or partial data yields insufficient_evidence. Does not create alerts."""
    _fraction(target, "target")
    if isinstance(minimum_requests, bool) or not isinstance(minimum_requests, int) or minimum_requests < 1:
        raise GCPError("invalid_argument", "minimum_requests must be a positive integer.")
    metrics = get_gcp_service_metrics(service_name, hours)
    if "error" in metrics:
        return metrics
    return {
        "provider": "gcp",
        "service_name": service_name,
        "window": metrics.get("window"),
        "slo": evaluate_slo(metrics, target, minimum_requests),
        "metrics": metrics,
        "partial": metrics.get("partial", False),
    }
