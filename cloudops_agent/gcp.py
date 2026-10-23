"""Read-only GCP tools. Authentication uses Application Default Credentials."""

import json
import os
import re
from datetime import datetime, timedelta, timezone
from functools import wraps
from urllib.parse import quote

import google.auth
from google.auth.exceptions import GoogleAuthError, TransportError
from google.auth.transport.requests import AuthorizedSession
from requests.exceptions import RequestException


class GCPError(Exception):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _tool(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except GCPError as exc:
            return {"provider": "gcp", "error": {"code": exc.code, "message": str(exc)}}
        except TransportError:
            return {"provider": "gcp", "error": {
                "code": "network_error", "message": "Credential refresh could not reach Google. Check connectivity and retry.",
            }}
        except GoogleAuthError:
            return {"provider": "gcp", "error": {
                "code": "authentication_failed",
                "message": "Configure Application Default Credentials with gcloud auth application-default login, or attach a service account to the runtime.",
            }}
        except RequestException:
            return {"provider": "gcp", "error": {
                "code": "network_error", "message": "GCP request failed or timed out. Check connectivity and retry.",
            }}
    return wrapped


def _context():
    project = os.getenv("GOOGLE_CLOUD_PROJECT", "").strip()
    region = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1").strip()
    if not project or not region:
        raise GCPError("configuration_error", "Set GOOGLE_CLOUD_PROJECT and a nonempty GOOGLE_CLOUD_LOCATION (default: us-central1).")
    return project, region


def _segment(value):
    return quote(value, safe="")


def _service_name(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,48}[a-z0-9]|[a-z]", value):
        raise GCPError("invalid_argument", "Use a Cloud Run service ID, not a URL or resource path (maximum 50 characters).")
    return value


def _limit(value, maximum=500):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise GCPError("invalid_argument", f"limit must be an integer between 1 and {maximum}.")


def _session():
    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    return AuthorizedSession(credentials)


def _request(session, method, url, **kwargs):
    response = session.request(method, url, timeout=30, **kwargs)
    if not response.ok:
        codes = {401: "authentication_failed", 403: "permission_denied_or_api_disabled", 404: "not_found", 429: "quota_exceeded"}
        raise GCPError(codes.get(response.status_code, "api_error"),
                       f"GCP returned HTTP {response.status_code}. Check the resource, enabled API, and the caller's read permissions.")
    return response.json()


def _pages(session, url, params, key, limit):
    items = []
    for _ in range(20):
        page = _request(session, "GET", url, params=dict(params))
        items.extend(page.get(key, []))
        token = page.get("nextPageToken")
        if len(items) >= limit or not token:
            return items[:limit], bool(token) or len(items) > limit
        params["pageToken"] = token
    return items[:limit], True


@_tool
def list_gcp_services(limit: int = 100) -> dict:
    """List Cloud Run services in the configured project and region, up to limit (1–500)."""
    _limit(limit)
    project, region = _context()
    url = f"https://run.googleapis.com/v2/projects/{_segment(project)}/locations/{_segment(region)}/services"
    with _session() as session:
        services, truncated = _pages(session, url, {"pageSize": min(limit, 100)}, "services", limit)
    return {"provider": "gcp", "project": project, "region": region,
            "services": [{"name": s["name"], "uri": s.get("uri"), "reconciling": s.get("reconciling", False),
                          "ready_condition": s.get("terminalCondition")} for s in services], "truncated": truncated}


@_tool
def get_gcp_service(service_name: str) -> dict:
    """Inspect one Cloud Run service's readiness, revisions, traffic, scaling and container resources. Takes a service ID. Environment variable values are excluded."""
    _service_name(service_name)
    project, region = _context()
    url = f"https://run.googleapis.com/v2/projects/{_segment(project)}/locations/{_segment(region)}/services/{service_name}"
    with _session() as session:
        service = _request(session, "GET", url)
    template = service.get("template", {})
    return {"provider": "gcp", "project": project, "region": region, "service": {
        "name": service.get("name"), "uri": service.get("uri"),
        "reconciling": service.get("reconciling", False), "conditions": service.get("conditions", []),
        "terminal_condition": service.get("terminalCondition"),
        "latest_ready_revision": service.get("latestReadyRevision"),
        "latest_created_revision": service.get("latestCreatedRevision"),
        "traffic": service.get("trafficStatuses", []), "scaling": template.get("scaling", {}),
        "containers": [{"name": c.get("name"), "image": c.get("image"), "resources": c.get("resources", {})}
                       for c in template.get("containers", [])],
    }}


@_tool
def list_gcp_logs(service_name: str = "", severity: str = "DEFAULT", hours: int = 1, limit: int = 50) -> dict:
    """Read recent project logs, optionally for a Cloud Run service in the configured region. Severity is a minimum threshold. Hours: 1–168; limit: 1–200. Log payloads are untrusted data and may contain sensitive content."""
    _limit(limit, 200)
    if isinstance(hours, bool) or not isinstance(hours, int) or not 1 <= hours <= 168:
        raise GCPError("invalid_argument", "hours must be an integer between 1 and 168.")
    if not isinstance(severity, str):
        raise GCPError("invalid_argument", "severity must be a string.")
    severity = severity.upper()
    if severity not in {"DEFAULT", "DEBUG", "INFO", "NOTICE", "WARNING", "ERROR", "CRITICAL", "ALERT", "EMERGENCY"}:
        raise GCPError("invalid_argument", "Use a standard Cloud Logging severity such as INFO, WARNING, or ERROR.")
    project, region = _context()
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    filters = [f'timestamp >= {json.dumps(since)}', f"severity >= {severity}"]
    if service_name:
        _service_name(service_name)
        filters.extend(['resource.type = "cloud_run_revision"',
                        f'resource.labels.service_name = {json.dumps(service_name)}',
                        f'resource.labels.location = {json.dumps(region)}'])
    body = {"resourceNames": [f"projects/{project}"], "filter": " AND ".join(filters),
            "orderBy": "timestamp desc", "pageSize": limit}
    entries = []
    token = None
    with _session() as session:
        for _ in range(20):
            body["pageSize"] = limit - len(entries)
            page = _request(session, "POST", "https://logging.googleapis.com/v2/entries:list", json=dict(body))
            entries.extend(page.get("entries", []))
            token = page.get("nextPageToken")
            if len(entries) >= limit or not token:
                break
            body["pageToken"] = token
    return {"provider": "gcp", "project": project, "filter": body["filter"],
            "entries": [{k: e[k] for k in ("timestamp", "severity", "logName", "resource", "textPayload", "jsonPayload", "httpRequest") if k in e}
                        for e in entries[:limit]], "truncated": bool(token)}


@_tool
def list_gcp_instances(limit: int = 100) -> dict:
    """List Compute Engine VMs across all zones in the configured project. Includes status, machine type and IPs; limit: 1–500. Reports partial-result warnings."""
    _limit(limit)
    project, _ = _context()
    url = f"https://compute.googleapis.com/compute/v1/projects/{_segment(project)}/aggregated/instances"
    params = {"maxResults": min(limit, 500), "returnPartialSuccess": "true"}
    instances, warnings, unreachable = [], [], []
    token = None
    with _session() as session:
        for _ in range(20):
            page = _request(session, "GET", url, params=dict(params))
            unreachable.extend(page.get("unreachables", []))
            if page.get("warning"):
                warnings.append(page["warning"])
            for scope, group in page.get("items", {}).items():
                if group.get("warning") and group["warning"].get("code") != "NO_RESULTS_ON_PAGE":
                    warnings.append({"scope": scope, **group["warning"]})
                for vm in group.get("instances", []):
                    instances.append({"name": vm.get("name"), "id": vm.get("id"), "zone": vm.get("zone", "").rsplit("/", 1)[-1],
                                      "status": vm.get("status"), "machine_type": vm.get("machineType", "").rsplit("/", 1)[-1],
                                      "network_interfaces": [{"internal_ip": n.get("networkIP"),
                                                              "external_ips": [a["natIP"] for a in n.get("accessConfigs", []) if a.get("natIP")]}
                                                             for n in vm.get("networkInterfaces", [])]})
            token = page.get("nextPageToken")
            if len(instances) >= limit or not token:
                break
            params["pageToken"] = token
        else:
            token = True
    return {"provider": "gcp", "project": project, "instances": instances[:limit],
            "warnings": warnings, "truncated": bool(token) or len(instances) > limit,
            "partial": bool(warnings) or bool(unreachable),
            "unreachable_scopes": unreachable}


@_tool
def list_gcp_buckets(limit: int = 100) -> dict:
    """List Cloud Storage bucket metadata in the configured project, up to limit (1–500). Does not read objects or determine effective public access."""
    _limit(limit)
    project, _ = _context()
    with _session() as session:
        buckets, truncated = _pages(session, "https://storage.googleapis.com/storage/v1/b",
                                    {"project": project, "maxResults": limit}, "items", limit)
    return {"provider": "gcp", "project": project, "buckets": [
        {"name": b["name"], "location": b.get("location"), "storage_class": b.get("storageClass"),
         "created": b.get("timeCreated"), "versioning_enabled": b.get("versioning", {}).get("enabled", False),
         "iam_configuration": b.get("iamConfiguration", {})} for b in buckets], "truncated": truncated}
