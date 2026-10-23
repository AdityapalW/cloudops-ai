"""Revision history and bounded, raw Cloud Monitoring summaries."""

import json
from datetime import datetime, timedelta, timezone

from .gcp import GCPError, _context, _limit, _pages, _request, _segment, _service_name, _session, _tool


@_tool
def list_gcp_revisions(service_name: str, limit: int = 10) -> dict:
    """List a Cloud Run service's newest revisions first, including creation times, readiness, images and resource limits. Limit: 1–100. Secret configuration is excluded."""
    _service_name(service_name)
    _limit(limit, 100)
    project, region = _context()
    url = f"https://run.googleapis.com/v2/projects/{_segment(project)}/locations/{_segment(region)}/services/{service_name}/revisions"
    with _session() as session:
        revisions, truncated = _pages(session, url, {"pageSize": limit}, "revisions", limit)
    return {"provider": "gcp", "project": project, "region": region,
            "revisions": [{"name": r.get("name"), "created": r.get("createTime"),
                           "conditions": r.get("conditions", []), "reconciling": r.get("reconciling", False),
                           "containers": [{"image": c.get("image"), "resources": c.get("resources", {})}
                                          for c in r.get("containers", [])]} for r in revisions],
            "truncated": truncated}


def _metric(session, project, region, service, metric, start, end):
    filters = {"metric.type": f"run.googleapis.com/{metric}", "resource.type": "cloud_run_revision",
               "resource.labels.project_id": project, "resource.labels.location": region,
               "resource.labels.service_name": service}
    params = {"filter": " AND ".join(f"{k} = {json.dumps(v)}" for k, v in filters.items()),
              "interval.startTime": start, "interval.endTime": end, "view": "FULL", "pageSize": 1000}
    series, points, errors = [], 0, []
    token = None
    for _ in range(20):
        page = _request(session, "GET", f"https://monitoring.googleapis.com/v3/projects/{_segment(project)}/timeSeries", params=dict(params))
        errors.extend(page.get("executionErrors", []))
        for row in page.get("timeSeries", []):
            remaining = 10000 - points
            row = dict(row)
            original = row.get("points", [])
            row["points"] = original[:remaining]
            series.append(row)
            points += len(row["points"])
            if len(original) > remaining:
                return series, True, errors
        token = page.get("nextPageToken")
        if not token or points >= 10000:
            break
        params["pageToken"] = token
    return series, bool(token), errors


@_tool
def get_gcp_service_metrics(service_name: str, hours: int = 1) -> dict:
    """Summarize Cloud Run request counts, HTTP 4xx/5xx rates and sample-weighted mean latency in milliseconds. Hours: 1–24. Missing data is not zero; raw DELTA samples are bounded to 10,000 points per metric. Does not calculate percentiles."""
    _service_name(service_name)
    if isinstance(hours, bool) or not isinstance(hours, int) or not 1 <= hours <= 24:
        raise GCPError("invalid_argument", "hours must be an integer between 1 and 24.")
    project, region = _context()
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    result = {"provider": "gcp", "project": project, "region": region, "service_name": service_name,
              "window": {"start": start.isoformat(), "end": end.isoformat()},
              "request_count": None, "server_error_count": None, "client_error_count": None,
              "server_error_rate": None, "client_error_rate": None, "mean_latency_ms": None,
              "latency_sample_count": 0, "by_revision": {}, "errors": {}, "truncated": False,
              "notes": ["Cloud Monitoring samples can arrive late; recent minutes may be incomplete.",
                        "Counts include raw DELTA samples returned for this interval; boundary samples can straddle the start.",
                        "Latency is weighted by distribution sample count across revisions; it is not p95 or p99."]}
    with _session() as session:
        for metric in ("request_count", "request_latencies"):
            try:
                rows, truncated, errors = _metric(session, project, region, service_name, metric,
                                                 start.isoformat(), end.isoformat())
            except GCPError as exc:
                result["errors"][metric] = {"code": exc.code, "message": str(exc)}
                continue
            result["truncated"] |= truncated
            if errors:
                result["errors"][metric] = {"code": "partial_execution", "message": "Monitoring reported incomplete query execution."}
            if metric == "request_count":
                total = server = client = samples = 0
                for row in rows:
                    labels = row.get("metric", {}).get("labels", {})
                    code = str(labels.get("response_code_class", ""))
                    if not code:
                        code = str(labels.get("response_code", ""))[:1] + "xx"
                    revision = row.get("resource", {}).get("labels", {}).get("revision_name", "unknown")
                    counts = result["by_revision"].setdefault(revision, {"request_count": 0, "server_error_count": 0})
                    for point in row.get("points", []):
                        if "int64Value" not in point.get("value", {}):
                            continue
                        value = int(point["value"]["int64Value"])
                        samples += 1
                        total += value
                        counts["request_count"] += value
                        if code == "5xx":
                            server += value
                            counts["server_error_count"] += value
                        if code == "4xx":
                            client += value
                if samples:
                    result.update(request_count=total, server_error_count=server, client_error_count=client,
                                  server_error_rate=server / total if total else None,
                                  client_error_rate=client / total if total else None)
            else:
                weighted_sum = 0.0
                count = 0
                for row in rows:
                    for point in row.get("points", []):
                        distribution = point.get("value", {}).get("distributionValue", {})
                        n = int(distribution.get("count", 0))
                        weighted_sum += n * float(distribution.get("mean", 0))
                        count += n
                result["latency_sample_count"] = count
                result["mean_latency_ms"] = weighted_sum / count if count else None
    result["partial"] = bool(result["errors"]) or result["truncated"]
    result["data_status"] = "partial" if result["partial"] else ("available" if result["request_count"] is not None else "no_request_data")
    return result
