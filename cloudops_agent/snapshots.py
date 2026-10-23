"""Local, portable Cloud Run configuration snapshots and scope-safe drift comparison."""

from datetime import datetime, timezone

from .gcp import GCPError, _context, _limit, _tool, get_gcp_service, list_gcp_services
from .redaction import redact
from .revisions import diff_configuration, fingerprint

SCHEMA_VERSION = 1


def service_configuration(service):
    config = {
        key: service.get(key)
        for key in (
            "scaling",
            "service_scaling",
            "concurrency",
            "timeout",
            "service_account",
            "ingress",
            "vpc_access",
        )
    }
    config["containers"] = {
        container.get("name") or f"container-{index}": {
            key: container.get(key) for key in ("image", "resources")
        }
        for index, container in enumerate(service.get("containers", []))
    }
    return config


@_tool
def snapshot_gcp_services(limit: int = 20) -> dict:
    """Capture allowlisted Cloud Run configuration for up to 20 services in the configured region. Returns portable snapshot JSON; no files or cloud resources are changed. Includes explicit collection errors and completeness."""
    _limit(limit, 20)
    project, region = _context()
    inventory = list_gcp_services(limit)
    if "error" in inventory:
        return inventory
    resources, errors = {}, {}
    for item in inventory["services"]:
        service_id = item["name"].rsplit("/", 1)[-1]
        result = get_gcp_service(service_id)
        if "error" in result:
            errors[service_id] = result["error"]
            continue
        config = service_configuration(result["service"])
        resources[service_id] = {"configuration": config, "fingerprint": fingerprint(config)}
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "cloudops.cloud_run_configuration",
        "provider": "gcp",
        "project": project,
        "region": region,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "resources": resources,
        "errors": errors,
        "truncated": inventory["truncated"],
        "complete": not inventory["truncated"] and not errors,
        "notes": [
            "Snapshot excludes secret-bearing configuration and operational readiness/traffic state.",
            "Configuration differences may include explicit defaults versus omitted fields.",
        ],
    }


def validate_snapshot(snapshot):
    if (
        not isinstance(snapshot, dict)
        or snapshot.get("schema_version") != SCHEMA_VERSION
        or snapshot.get("kind") != "cloudops.cloud_run_configuration"
    ):
        raise GCPError("invalid_snapshot", "Expected a version 1 CloudOps Cloud Run configuration snapshot.")
    if (
        not snapshot.get("project")
        or not snapshot.get("region")
        or not isinstance(snapshot.get("resources"), dict)
    ):
        raise GCPError("invalid_snapshot", "Snapshot must include project, region, and a resources mapping.")
    for name, resource in snapshot["resources"].items():
        if not isinstance(resource, dict) or not isinstance(resource.get("configuration"), dict):
            raise GCPError("invalid_snapshot", f"Resource {name} has no configuration mapping.")
        if resource.get("fingerprint") != fingerprint(resource["configuration"]):
            raise GCPError("invalid_snapshot", f"Resource {name} does not match its stored fingerprint.")


def compare_snapshots(before, after):
    validate_snapshot(before)
    validate_snapshot(after)
    if (before["project"], before["region"]) != (after["project"], after["region"]):
        raise GCPError("scope_mismatch", "Snapshots must belong to the same project and region.")
    old, new = before["resources"], after["resources"]
    common = sorted(set(old) & set(new))
    changed = []
    for name in common:
        changes = diff_configuration(old[name]["configuration"], new[name]["configuration"])
        if changes:
            changed.append({"service": name, "changes": redact(changes)})
    before_complete = (
        before.get("complete") is True and not before.get("errors") and not before.get("truncated")
    )
    after_complete = after.get("complete") is True and not after.get("errors") and not after.get("truncated")
    added, removed = sorted(set(new) - set(old)), sorted(set(old) - set(new))
    return {
        "provider": "gcp",
        "project": after["project"],
        "region": after["region"],
        "before_captured_at": before.get("captured_at"),
        "after_captured_at": after.get("captured_at"),
        "changed": changed,
        "added": added if before_complete else [],
        "removed": removed if after_complete else [],
        "unconfirmed_additions": [] if before_complete else added,
        "unconfirmed_removals": [] if after_complete else removed,
        "partial": not (before_complete and after_complete),
        "notes": [
            "A missing resource is treated as an addition/removal only when the relevant inventory is complete.",
            "Drift describes observed configuration changes; it does not establish whether a change is intended.",
        ],
    }
