import os
import unittest
from unittest.mock import MagicMock, patch

from cloudops_agent import observability as obs


def series(values, code="2xx", revision="api-001"):
    return {
        "metric": {"labels": {"response_code_class": code}},
        "resource": {"labels": {"revision_name": revision}},
        "points": [{"value": {"int64Value": str(v)}} for v in values],
    }


class ObservabilityTest(unittest.TestCase):
    def setUp(self):
        env = patch.dict(
            os.environ, {"GOOGLE_CLOUD_PROJECT": "test-project", "GOOGLE_CLOUD_LOCATION": "us-central1"}
        )
        env.start()
        self.addCleanup(env.stop)
        session = patch.object(obs, "_session")
        self.session = session.start().return_value.__enter__.return_value
        self.addCleanup(session.stop)

    def pages(self, *pages):
        self.session.request.side_effect = [MagicMock(ok=True, json=MagicMock(return_value=p)) for p in pages]

    def test_weighted_latency_and_cross_revision_error_rate(self):
        self.pages(
            {"timeSeries": [series([90]), series([10], "5xx", "api-002"), series([5], "4xx")]},
            {
                "timeSeries": [
                    {
                        "points": [
                            {"value": {"distributionValue": {"count": "90", "mean": 100}}},
                            {"value": {"distributionValue": {"count": "10", "mean": 1000}}},
                        ]
                    }
                ]
            },
        )
        result = obs.get_gcp_service_metrics("api")
        self.assertEqual(result["request_count"], 105)
        self.assertAlmostEqual(result["server_error_rate"], 10 / 105)
        self.assertEqual(result["mean_latency_ms"], 190)
        self.assertEqual(result["by_revision"]["api-002"]["server_error_count"], 10)
        self.assertFalse(result["partial"])
        params = self.session.request.call_args.kwargs["params"]
        self.assertIn('resource.labels.project_id = "test-project"', params["filter"])
        self.assertIn('resource.labels.location = "us-central1"', params["filter"])

    def test_no_data_remains_unknown(self):
        self.pages({}, {})
        result = obs.get_gcp_service_metrics("api")
        self.assertIsNone(result["request_count"])
        self.assertIsNone(result["server_error_rate"])
        self.assertIsNone(result["mean_latency_ms"])
        self.assertEqual(result["data_status"], "no_request_data")

    def test_zero_traffic_has_no_error_rate(self):
        self.pages({"timeSeries": [series([0])]}, {})
        result = obs.get_gcp_service_metrics("api")
        self.assertEqual(result["request_count"], 0)
        self.assertIsNone(result["server_error_rate"])

    def test_monitoring_permission_failure_preserves_other_query(self):
        self.session.request.side_effect = [
            MagicMock(ok=False, status_code=403),
            MagicMock(ok=True, json=lambda: {}),
        ]
        result = obs.get_gcp_service_metrics("api")
        self.assertTrue(result["partial"])
        self.assertIn("request_count", result["errors"])

    def test_pagination_and_execution_error(self):
        self.pages(
            {"timeSeries": [series([2])], "nextPageToken": "next"},
            {"timeSeries": [series([3])], "executionErrors": [{"code": 13}]},
            {},
        )
        result = obs.get_gcp_service_metrics("api")
        self.assertEqual(result["request_count"], 5)
        self.assertTrue(result["partial"])
        self.assertEqual(self.session.request.call_args_list[1].kwargs["params"]["pageToken"], "next")

    def test_point_bound_marks_results_incomplete(self):
        with patch.object(obs, "_metric", return_value=([series([10])], True, [])):
            result = obs.get_gcp_service_metrics("api")
        self.assertTrue(result["truncated"])
        self.assertTrue(result["partial"])

    def test_revision_history_omits_secrets(self):
        self.pages(
            {
                "revisions": [
                    {
                        "name": "api-002",
                        "createTime": "2026-01-01T00:00:00Z",
                        "containers": [{"image": "image", "env": [{"value": "secret"}]}],
                    }
                ]
            }
        )
        result = obs.list_gcp_revisions("api")
        self.assertEqual(result["revisions"][0]["created"], "2026-01-01T00:00:00Z")
        self.assertNotIn("secret", str(result))

    def test_validation(self):
        self.assertIn("error", obs.list_gcp_revisions("bad/name"))
        self.assertIn("error", obs.get_gcp_service_metrics("api", hours=25))
        self.session.request.assert_not_called()
