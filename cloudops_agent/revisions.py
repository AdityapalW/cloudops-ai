"""Read-only revision configuration inspection and deterministic comparisons."""

import hashlib
import json
import re

from .gcp import GCPError, _context, _request, _segment, _service_name, _session, _tool


def revision_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?", value):
        raise GCPError(
            "invalid_argument",
            "Use a revision ID of 1–63 lowercase letters, digits, or hyphens, beginning with a letter and ending with a letter or digit.",
        )
    return value


def configuration(revision):
    """Allowlisted fields only: never include environment values, commands or secret refs."""
    containers = {}
    for index, container in enumerate(revision.get("containers", [])):
        containers[container.get("name") or f"container-{index}"] = {
            "image": container.get("image"),
            "resources": container.get("resources", {}),
            "ports": container.get("ports", []),
        }
    return {
        "containers": containers,
        "service_account": revision.get("serviceAccount"),
        "timeout": revision.get("timeout"),
        "concurrency": revision.get("maxInstanceRequestConcurrency"),
        "scaling": revision.get("scaling", {}),
        "execution_environment": revision.get("executionEnvironment"),
        "vpc_access": revision.get("vpcAccess", {}),
    }


def fingerprint(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def diff_configuration(before, after, path=""):
    changes = []
    for key in sorted(set(before) | set(after)):
        field = f"{path}.{key}" if path else key
        old, new = before.get(key), after.get(key)
        if isinstance(old, dict) and isinstance(new, dict):
            changes.extend(diff_configuration(old, new, field))
        elif old != new:
            changes.append({"field": field, "before": old, "after": new})
    return changes


@_tool
def get_gcp_revision(service_name: str, revision_name: str) -> dict:
    """Inspect a Cloud Run revision ID: readiness and allowlisted configuration, excluding environment values and secret references."""
    _service_name(service_name)
    revision_id(revision_name)
    project, region = _context()
    url = f"https://run.googleapis.com/v2/projects/{_segment(project)}/locations/{_segment(region)}/services/{service_name}/revisions/{revision_name}"
    with _session() as session:
        revision = _request(session, "GET", url)
    config = configuration(revision)
    return {
        "provider": "gcp",
        "project": project,
        "region": region,
        "revision": {
            "name": revision.get("name"),
            "created": revision.get("createTime"),
            "conditions": revision.get("conditions", []),
            "reconciling": revision.get("reconciling", False),
            "configuration": config,
            "configuration_fingerprint": fingerprint(config),
        },
    }


@_tool
def compare_gcp_revisions(service_name: str, before_revision: str, after_revision: str) -> dict:
    """Compare two explicit revision IDs belonging to a Cloud Run service. Reports configuration changes and readiness without inferring causation or exposing secrets."""
    _service_name(service_name)
    revision_id(before_revision)
    revision_id(after_revision)
    before = get_gcp_revision(service_name, before_revision)
    after = before if before_revision == after_revision else get_gcp_revision(service_name, after_revision)
    errors = {
        side: value["error"] for side, value in (("before", before), ("after", after)) if "error" in value
    }
    changes = (
        None
        if errors
        else diff_configuration(before["revision"]["configuration"], after["revision"]["configuration"])
    )
    return {
        "provider": "gcp",
        "service_name": service_name,
        "before": before,
        "after": after,
        "changes": changes,
        "change_count": len(changes) if changes is not None else None,
        "partial": bool(errors),
        "errors": errors,
        "notes": [
            "Configuration differences are observations; they do not establish the cause of an incident.",
            "Environment variables, command arguments, probes and secret references are excluded from this comparison.",
            "An omitted API field and an explicit default can appear different without changing runtime behavior.",
        ],
    }
