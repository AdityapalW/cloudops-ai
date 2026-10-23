"""Pub/Sub metadata and backlog inspection, without reading or acknowledging messages."""

import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from .gcp import GCPError, _context, _limit, _pages, _request, _segment, _session, _tool
from .incidents import timestamp
from .observability import _time_series


def subscription_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9\-_.~+%]{2,254}", value):
        raise GCPError(
            "invalid_argument",
            "Use a Pub/Sub subscription ID (3–255 allowed characters), not a resource path.",
        )
    return value


def subscription_summary(subscription):
    push = subscription.get("pushConfig", {})
    endpoint = urlparse(push.get("pushEndpoint", ""))
    destination = "pull"
    if push.get("pushEndpoint"):
        destination = "push"
    for key, name in (
        ("bigqueryConfig", "bigquery"),
        ("cloudStorageConfig", "cloud_storage"),
        ("bigtableConfig", "bigtable"),
    ):
        if subscription.get(key):
            destination = name
    return {
        "name": subscription.get("name"),
        "topic": subscription.get("topic"),
        "state": subscription.get("state"),
        "detached": subscription.get("detached", False),
        "delivery_type": destination,
        "push_endpoint_host": endpoint.hostname,
        "push_oidc_configured": bool(push.get("oidcToken")),
        "ack_deadline_seconds": subscription.get("ackDeadlineSeconds"),
        "message_retention": subscription.get("messageRetentionDuration"),
        "retain_acked_messages": subscription.get("retainAckedMessages", False),
        "dead_letter_policy": subscription.get("deadLetterPolicy"),
        "retry_policy": subscription.get("retryPolicy"),
        "expiration_policy": subscription.get("expirationPolicy"),
        "exactly_once_delivery": subscription.get("enableExactlyOnceDelivery", False),
    }


@_tool
def list_gcp_subscriptions(limit: int = 100) -> dict:
    """List project-wide Pub/Sub subscription metadata (1–500 results). Excludes message payloads, push URL paths/query parameters and auth configuration details."""
    _limit(limit)
    project, _ = _context()
    with _session() as session:
        rows, truncated = _pages(
            session,
            f"https://pubsub.googleapis.com/v1/projects/{_segment(project)}/subscriptions",
            {"pageSize": min(limit, 100)},
            "subscriptions",
            limit,
        )
    return {
        "provider": "gcp",
        "project": project,
        "subscriptions": [subscription_summary(row) for row in rows],
        "truncated": truncated,
    }


def latest_gauge(rows):
    candidates = []
    for row in rows:
        points = []
        for point in row.get("points", []):
            instant = timestamp(point.get("interval", {}).get("endTime"))
            value = point.get("value", {})
            raw = value.get("int64Value", value.get("doubleValue"))
            if instant and raw is not None:
                points.append((instant, float(raw)))
        if points:
            instant, value = max(points, key=lambda point: point[0])
            candidates.append({"value": value, "sampled_at": instant.isoformat()})
    if len(candidates) != 1:
        return {
            "value": None,
            "sampled_at": None,
            "reason": "no_data" if not candidates else "ambiguous_series",
        }
    return candidates[0]


@_tool
def diagnose_gcp_subscription(
    subscription_name: str, backlog_threshold: int = 1000, age_threshold_seconds: int = 300
) -> dict:
    """Inspect subscription metadata and latest backlog/oldest-unacked-age samples from the last hour. Thresholds are user-selected heuristics. No messages are pulled, acknowledged, or modified."""
    subscription_id(subscription_name)
    for value, name in (
        (backlog_threshold, "backlog_threshold"),
        (age_threshold_seconds, "age_threshold_seconds"),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise GCPError("invalid_argument", f"{name} must be a positive integer.")
    project, _ = _context()
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=1)
    metrics, errors, findings = {}, {}, []
    with _session() as session:
        subscription = _request(
            session,
            "GET",
            f"https://pubsub.googleapis.com/v1/projects/{_segment(project)}/subscriptions/{_segment(subscription_name)}",
        )
        for key, metric, threshold in (
            ("backlog_messages", "subscription/num_undelivered_messages", backlog_threshold),
            ("oldest_unacked_age_seconds", "subscription/oldest_unacked_message_age", age_threshold_seconds),
        ):
            filters = {
                "metric.type": "pubsub.googleapis.com/" + metric,
                "resource.type": "pubsub_subscription",
                "resource.labels.project_id": project,
                "resource.labels.subscription_id": subscription_name,
            }
            try:
                rows, truncated, failures = _time_series(
                    session, project, filters, start.isoformat(), end.isoformat()
                )
            except GCPError as exc:
                errors[key] = {"code": exc.code, "message": str(exc)}
                continue
            gauge = latest_gauge(rows)
            metrics[key] = gauge
            instant = timestamp(gauge.get("sampled_at"))
            gauge["stale"] = instant is not None and (end - instant).total_seconds() > 300
            if truncated or failures or gauge.get("reason") or gauge["stale"]:
                errors[key] = {
                    "code": "incomplete_telemetry",
                    "message": "Metric data is missing, stale, ambiguous, or incomplete.",
                }
            if gauge["value"] is not None and gauge["value"] >= threshold:
                findings.append(
                    {
                        "code": key + "_above_threshold",
                        "value": gauge["value"],
                        "threshold": threshold,
                        "sampled_at": gauge["sampled_at"],
                        "stale": gauge["stale"],
                        "observation": "A sampled metric exceeded the selected threshold; inspect subscriber throughput and acknowledgement failures.",
                    }
                )
    metadata = subscription_summary(subscription)
    if metadata["detached"] or metadata["topic"] == "_deleted-topic_":
        findings.append(
            {
                "code": "subscription_disconnected",
                "observation": "The subscription is detached or its topic was deleted.",
            }
        )
    if metadata["state"] == "RESOURCE_ERROR":
        findings.append(
            {
                "code": "subscription_resource_error",
                "observation": "Pub/Sub reports a subscription resource error.",
            }
        )
    return {
        "provider": "gcp",
        "project": project,
        "subscription_name": subscription_name,
        "subscription": metadata,
        "metrics": metrics,
        "findings": findings,
        "errors": errors,
        "partial": bool(errors),
        "status": "attention_needed"
        if findings
        else ("insufficient_evidence" if errors else "no_pressure_detected"),
        "notes": [
            "Backlog and age are gauges: samples are never summed over time.",
            "Latest samples older than five minutes are marked stale. Monitoring delivery can lag.",
            "Global subscription metrics can hide regional variation; this is a first-pass inspection.",
            "A quiet subscription is not proof that publishers or subscribers are healthy.",
        ],
    }
