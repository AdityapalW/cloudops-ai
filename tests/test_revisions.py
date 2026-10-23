import os
import unittest
from unittest.mock import MagicMock, patch

from cloudops_agent import gcp, revisions


class RevisionTest(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "p", "GOOGLE_CLOUD_LOCATION": "us-central1"})
        env.start()
        self.addCleanup(env.stop)

    def test_configuration_never_exposes_secrets(self):
        config = revisions.configuration(
            {
                "containers": [
                    {
                        "name": "api",
                        "image": "v1",
                        "env": [{"value": "secret"}],
                        "args": ["password=private"],
                        "startupProbe": {"httpGet": {"httpHeaders": [{"value": "private"}]}},
                    }
                ]
            }
        )
        self.assertNotIn("secret", str(config))
        self.assertNotIn("private", str(config))

    def test_nested_changes_and_stable_fingerprint(self):
        before = {"containers": {"api": {"image": "v1", "resources": {"limits": {"memory": "256Mi"}}}}}
        after = {"containers": {"api": {"image": "v2", "resources": {"limits": {"memory": "512Mi"}}}}}
        changes = revisions.diff_configuration(before, after)
        self.assertEqual(
            {c["field"] for c in changes}, {"containers.api.image", "containers.api.resources.limits.memory"}
        )
        self.assertEqual(
            revisions.fingerprint(before), revisions.fingerprint(dict(reversed(list(before.items()))))
        )
        self.assertNotEqual(revisions.fingerprint(before), revisions.fingerprint(after))

    def test_named_container_order_does_not_create_changes(self):
        containers = [{"name": "api", "image": "v1"}, {"name": "sidecar", "image": "v2"}]
        self.assertEqual(
            revisions.configuration({"containers": containers}),
            revisions.configuration({"containers": containers[::-1]}),
        )

    def test_comparison_failure_is_not_no_changes(self):
        with patch.object(
            revisions,
            "get_gcp_revision",
            side_effect=[{"error": {"code": "not_found"}}, {"revision": {"configuration": {}}}],
        ):
            result = revisions.compare_gcp_revisions("api", "api-001", "api-002")
        self.assertTrue(result["partial"])
        self.assertIsNone(result["changes"])

    def test_same_revision_uses_one_fetch(self):
        with patch.object(
            revisions, "get_gcp_revision", return_value={"revision": {"configuration": {}}}
        ) as fetch:
            result = revisions.compare_gcp_revisions("api", "api-001", "api-001")
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(result["changes"], [])

    def test_revision_log_filter_and_validation(self):
        with patch.object(gcp, "_session") as session:
            request = session.return_value.__enter__.return_value.request
            request.return_value = MagicMock(ok=True, json=lambda: {})
            result = gcp.list_gcp_logs("api", revision_name="api-001")
            self.assertIn('resource.labels.revision_name = "api-001"', result["filter"])
            self.assertIn('resource.labels.service_name = "api"', result["filter"])
            request.reset_mock()
            self.assertIn("error", gcp.list_gcp_logs(revision_name="api-001"))
            self.assertIn("error", gcp.list_gcp_logs("api", revision_name='bad" OR true'))
            request.assert_not_called()
