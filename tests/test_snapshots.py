import copy
import unittest
from unittest.mock import patch

from cloudops_agent.gcp import GCPError
from cloudops_agent.revisions import fingerprint
from cloudops_agent.snapshots import compare_snapshots, snapshot_gcp_services


def snapshot(resources, complete=True):
    return {
        "schema_version": 1,
        "kind": "cloudops.cloud_run_configuration",
        "project": "p",
        "region": "r",
        "complete": complete,
        "resources": {
            name: {"configuration": config, "fingerprint": fingerprint(config)}
            for name, config in resources.items()
        },
    }


class SnapshotTest(unittest.TestCase):
    def test_added_removed_and_nested_drift(self):
        before = snapshot({"api": {"memory": "256Mi"}, "old": {}})
        after = snapshot({"api": {"memory": "512Mi"}, "new": {}})
        result = compare_snapshots(before, after)
        self.assertEqual(result["added"], ["new"])
        self.assertEqual(result["removed"], ["old"])
        self.assertEqual(result["changed"][0]["changes"][0]["field"], "memory")

    def test_partial_inventory_never_claims_missing_resource_deleted(self):
        result = compare_snapshots(snapshot({"old": {}}, False), snapshot({"new": {}}, False))
        self.assertEqual(result["added"], [])
        self.assertEqual(result["removed"], [])
        self.assertEqual(result["unconfirmed_additions"], ["new"])
        self.assertEqual(result["unconfirmed_removals"], ["old"])

    def test_scope_mismatch_is_rejected(self):
        before = snapshot({})
        after = dict(before, project="other")
        with self.assertRaises(GCPError) as caught:
            compare_snapshots(before, after)
        self.assertEqual(caught.exception.code, "scope_mismatch")

    def test_corrupted_snapshot_is_rejected(self):
        before = snapshot({"api": {"image": "v1"}})
        after = copy.deepcopy(before)
        after["resources"]["api"]["configuration"]["image"] = "v2"
        with self.assertRaises(GCPError) as caught:
            compare_snapshots(before, after)
        self.assertEqual(caught.exception.code, "invalid_snapshot")

    def test_collection_failure_marks_incomplete_snapshot(self):
        with patch("cloudops_agent.snapshots._context", return_value=("p", "r")):
            with patch(
                "cloudops_agent.snapshots.list_gcp_services",
                return_value={"services": [{"name": "services/api"}], "truncated": False},
            ):
                with patch(
                    "cloudops_agent.snapshots.get_gcp_service", return_value={"error": {"code": "not_found"}}
                ):
                    result = snapshot_gcp_services()
        self.assertFalse(result["complete"])
        self.assertIn("api", result["errors"])
