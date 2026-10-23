import os
import unittest
from unittest.mock import MagicMock, patch

from google.auth.exceptions import DefaultCredentialsError
from requests.exceptions import Timeout

from cloudops_agent import gcp


class GCPToolsTest(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(
            os.environ, {"GOOGLE_CLOUD_PROJECT": "test-project", "GOOGLE_CLOUD_LOCATION": "us-central1"}
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        self.session_patch = patch.object(gcp, "_session")
        self.session = self.session_patch.start().return_value.__enter__.return_value
        self.addCleanup(self.session_patch.stop)

    def responses(self, *pages):
        self.session.request.side_effect = [MagicMock(ok=True, json=MagicMock(return_value=p)) for p in pages]

    def test_services_pagination_and_truncation(self):
        self.responses(
            {"services": [{"name": "one"}], "nextPageToken": "next"},
            {"services": [{"name": "two"}, {"name": "three"}]},
        )
        result = gcp.list_gcp_services(limit=2)
        self.assertEqual([s["name"] for s in result["services"]], ["one", "two"])
        self.assertTrue(result["truncated"])
        self.assertEqual(self.session.request.call_args.kwargs["params"]["pageToken"], "next")

    def test_detail_excludes_environment_values(self):
        self.responses(
            {
                "name": "app",
                "terminalCondition": {"state": "CONDITION_SUCCEEDED"},
                "template": {"containers": [{"image": "image", "env": [{"value": "private"}]}]},
            }
        )
        result = gcp.get_gcp_service("app")
        self.assertNotIn("private", str(result))
        self.assertEqual(result["service"]["terminal_condition"]["state"], "CONDITION_SUCCEEDED")

    def test_logs_follow_empty_page_and_keep_region_filter(self):
        self.responses(
            {"nextPageToken": "next"}, {"entries": [{"severity": "ERROR", "textPayload": "failure"}]}
        )
        result = gcp.list_gcp_logs("app", "ERROR")
        self.assertEqual(len(result["entries"]), 1)
        self.assertIn('resource.labels.location = "us-central1"', result["filter"])
        self.assertIn("severity >= ERROR", result["filter"])
        self.assertEqual(self.session.request.call_args.kwargs["json"]["pageToken"], "next")
        self.assertEqual(self.session.request.call_args.args[0], "POST")

    def test_partial_vm_results_across_pages(self):
        self.responses(
            {"items": {}, "unreachables": ["zones/a"], "nextPageToken": "next"},
            {
                "items": {
                    "zones/b": {
                        "instances": [{"name": "vm", "zone": "zones/b", "machineType": "types/e2-micro"}]
                    }
                }
            },
        )
        result = gcp.list_gcp_instances()
        self.assertTrue(result["partial"])
        self.assertEqual(result["unreachable_scopes"], ["zones/a"])
        self.assertEqual(result["instances"][0]["machine_type"], "e2-micro")

    def test_bucket_metadata(self):
        self.responses({"items": [{"name": "bucket", "location": "US", "versioning": {"enabled": True}}]})
        result = gcp.list_gcp_buckets()
        self.assertTrue(result["buckets"][0]["versioning_enabled"])
        self.assertEqual(self.session.request.call_args.kwargs["params"]["project"], "test-project")

    def test_invalid_arguments_do_not_call_cloud(self):
        for result in (
            gcp.list_gcp_logs(service_name='bad" OR severity=DEFAULT'),
            gcp.list_gcp_logs(hours=0),
            gcp.list_gcp_logs(severity="WRONG"),
            gcp.list_gcp_instances(limit=501),
            gcp.list_gcp_services(limit=True),
            gcp.get_gcp_service("projects/other/services/app"),
        ):
            self.assertEqual(result["error"]["code"], "invalid_argument")
        self.session.request.assert_not_called()

    def test_missing_project_is_explicit_error(self):
        with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": ""}):
            self.assertEqual(gcp.list_gcp_services()["error"]["code"], "configuration_error")
        self.session.request.assert_not_called()

    def test_permission_error_is_not_empty_inventory(self):
        self.session.request.return_value = MagicMock(ok=False, status_code=403)
        result = gcp.list_gcp_buckets()
        self.assertEqual(result["error"]["code"], "permission_denied_or_api_disabled")
        self.assertNotIn("buckets", result)

    def test_authentication_and_timeout(self):
        with patch.object(gcp, "_session", side_effect=DefaultCredentialsError("private details")):
            result = gcp.list_gcp_instances()
            self.assertEqual(result["error"]["code"], "authentication_failed")
            self.assertNotIn("private details", str(result))
        self.session.request.side_effect = Timeout("private details")
        self.assertEqual(gcp.list_gcp_services()["error"]["code"], "network_error")

    def test_agent_exposes_all_tools(self):
        from cloudops_agent.agent import root_agent

        self.assertTrue(
            {
                "list_gcp_services",
                "get_gcp_service",
                "list_gcp_logs",
                "list_gcp_instances",
                "list_gcp_buckets",
            }
            <= {t.__name__ for t in root_agent.tools}
        )


if __name__ == "__main__":
    unittest.main()


class TransportGuardTest(unittest.TestCase):
    def test_mutations_and_unknown_hosts_are_blocked_before_network(self):
        session = MagicMock()
        for method, url in (
            ("DELETE", "https://run.googleapis.com/v2/projects/p"),
            ("POST", "https://run.googleapis.com/v2/projects/p/locations/r/services"),
            ("GET", "https://example.com/"),
            ("GET", "http://run.googleapis.com/v2/projects/p"),
        ):
            with self.assertRaises(gcp.GCPError) as caught:
                gcp._request(session, method, url)
            self.assertEqual(caught.exception.code, "read_only_violation")
        session.request.assert_not_called()

    def test_invalid_json_is_structured_error(self):
        session = MagicMock()
        session.request.return_value.ok = True
        session.request.return_value.json.side_effect = ValueError("invalid")
        with self.assertRaises(gcp.GCPError) as caught:
            gcp._request(session, "GET", "https://run.googleapis.com/v2/projects/p")
        self.assertEqual(caught.exception.code, "invalid_response")

    def test_credential_network_failure_is_not_auth_failure(self):
        from google.auth.exceptions import TransportError

        with patch.object(gcp, "_session", side_effect=TransportError("private network detail")):
            with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "test-project"}):
                result = gcp.list_gcp_services()
        self.assertEqual(result["error"]["code"], "network_error")
