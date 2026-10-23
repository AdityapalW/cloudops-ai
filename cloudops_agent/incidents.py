"""Bounded error grouping and revision/log timelines without model inference."""

import hashlib
import json
import re
from datetime import datetime, timezone

from .gcp import GCPError, _service_name, _tool, list_gcp_logs
from .observability import list_gcp_revisions
from .redaction import redact


def timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except ValueError:
        return None


def _message(entry):
    if "textPayload" in entry:
        return str(entry["textPayload"])
    payload = entry.get("jsonPayload", {})
    for key in ("message", "error", "exception"):
        if key in payload:
            value = payload[key]
            return value if isinstance(value, str) else json.dumps(value, sort_keys=True)
    if entry.get("httpRequest"):
        request = entry["httpRequest"]
        return f"HTTP {request.get('requestMethod', 'unknown')} status={request.get('status', 'unknown')}"
    return json.dumps(payload, sort_keys=True)


def error_groups(entries):
    groups, seen = {}, set()
    duplicates = invalid_times = 0
    for entry in entries:
        identifier = entry.get("insertId")
        key = (entry.get("logName"), identifier, entry.get("timestamp"))
        if identifier and key in seen:
            duplicates += 1
            continue
        if identifier:
            seen.add(key)
        message = redact(_message(redact(entry)))
        normalized = re.sub(r"\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b", "<uuid>", message)
        normalized = re.sub(r"\b[0-9a-fA-F]{16,}\b", "<hex-id>", normalized)
        normalized = re.sub(
            r"\b((?:request|trace|span|correlation)[_-]?id)\s*[=:]\s*[^\s,;]+",
            r"\1=<id>",
            normalized,
            flags=re.I,
        )
        normalized = " ".join(normalized.split())
        severity = entry.get("severity", "DEFAULT")
        signature = hashlib.sha256((severity + "\n" + normalized).encode()).hexdigest()[:16]
        group = groups.setdefault(
            signature,
            {
                "signature": signature,
                "severity": severity,
                "message_pattern": normalized,
                "count": 0,
                "first_seen": None,
                "last_seen": None,
                "revisions": {},
                "sample": message,
            },
        )
        group["count"] += 1
        revision = entry.get("resource", {}).get("labels", {}).get("revision_name", "unknown")
        group["revisions"][revision] = group["revisions"].get(revision, 0) + 1
        instant = timestamp(entry.get("timestamp"))
        if instant:
            value = instant.isoformat()
            if group["first_seen"] is None or value < group["first_seen"]:
                group["first_seen"] = value
            if group["last_seen"] is None or value > group["last_seen"]:
                group["last_seen"] = value
        else:
            invalid_times += 1
    return {
        "groups": sorted(groups.values(), key=lambda g: (-g["count"], g["signature"])),
        "unique_entries": sum(g["count"] for g in groups.values()),
        "duplicates_removed": duplicates,
        "entries_without_valid_timestamp": invalid_times,
        "notes": [
            "Grouping normalizes UUIDs and trace/request identifiers; it can merge unrelated errors or split one cause.",
            "Counts and first/last seen refer only to the returned log sample, not all project logs.",
        ],
    }


@_tool
def summarize_gcp_errors(service_name: str, hours: int = 1, revision_name: str = "") -> dict:
    """Group up to 200 recent ERROR logs by redacted message signature and show revision counts and sample timestamps. Can filter one revision. Does not infer root cause."""
    _service_name(service_name)
    logs = list_gcp_logs(service_name, severity="ERROR", hours=hours, limit=200, revision_name=revision_name)
    if "error" in logs:
        return logs
    return {
        "provider": "gcp",
        "project": logs["project"],
        "service_name": service_name,
        "filter": logs["filter"],
        "truncated": logs["truncated"],
        **error_groups(logs["entries"]),
    }


@_tool
def build_gcp_incident_timeline(service_name: str, hours: int = 1) -> dict:
    """Combine recent revision creation timestamps with first occurrences of grouped ERROR logs in a bounded sample. A timeline shows correlation, not causation."""
    _service_name(service_name)
    if isinstance(hours, bool) or not isinstance(hours, int) or not 1 <= hours <= 168:
        raise GCPError("invalid_argument", "hours must be an integer between 1 and 168.")
    logs = summarize_gcp_errors(service_name, hours)
    revisions = list_gcp_revisions(service_name, limit=20)
    events, errors = [], {}
    for source, result in (("logs", logs), ("revisions", revisions)):
        if "error" in result:
            errors[source] = result["error"]
    if "error" not in logs:
        for group in logs["groups"]:
            if group["first_seen"]:
                events.append(
                    {
                        "timestamp": group["first_seen"],
                        "type": "first_sampled_error",
                        "signature": group["signature"],
                        "message_pattern": group["message_pattern"],
                        "count": group["count"],
                        "revisions": group["revisions"],
                    }
                )
    if "error" not in revisions:
        for revision in revisions["revisions"]:
            created = timestamp(revision.get("created"))
            if created:
                events.append(
                    {
                        "timestamp": created.isoformat(),
                        "type": "revision_created",
                        "revision": revision.get("name"),
                    }
                )
    events.sort(key=lambda event: event["timestamp"])
    return {
        "provider": "gcp",
        "service_name": service_name,
        "events": events,
        "error_summary": logs,
        "revision_history": revisions,
        "errors": errors,
        "partial": bool(errors)
        or logs.get("truncated", False)
        or revisions.get("truncated", False)
        or bool(logs.get("entries_without_valid_timestamp")),
        "notes": [
            "Revision history may predate the log window; creation is not the time a revision started serving traffic.",
            "First sampled error is not the first-ever occurrence. Ordering does not establish deployment causation.",
        ],
    }
