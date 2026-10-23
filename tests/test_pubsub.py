import unittest
from unittest.mock import patch

from cloudops_agent.pubsub import diagnose_gcp_subscription, latest_gauge, subscription_summary


class PubSubTest(unittest.TestCase):
    def test_metadata_omits_push_credentials_path_and_query(self):
        result = subscription_summary(
            {
                "pushConfig": {
                    "pushEndpoint": "https://user:private@example.com/private?token=secret",
                    "oidcToken": {"audience": "private"},
                }
            }
        )
        self.assertEqual(result["push_endpoint_host"], "example.com")
        self.assertEqual(result["delivery_type"], "push")
        self.assertNotIn("private", str(result))
        self.assertNotIn("secret", str(result))

    def test_latest_gauge_is_not_a_time_sum(self):
        result = latest_gauge(
            [
                {
                    "points": [
                        {"interval": {"endTime": "2026-01-01T00:00:00Z"}, "value": {"int64Value": "100"}},
                        {"interval": {"endTime": "2026-01-01T00:01:00Z"}, "value": {"int64Value": "5"}},
                    ]
                }
            ]
        )
        self.assertEqual(result["value"], 5)

    def test_multiple_series_are_not_silently_aggregated(self):
        row = {"points": [{"interval": {"endTime": "2026-01-01T00:00:00Z"}, "value": {"int64Value": "5"}}]}
        self.assertEqual(latest_gauge([row, row])["reason"], "ambiguous_series")
        self.assertIsNone(latest_gauge([])["value"])

    def test_missing_metrics_is_not_no_pressure(self):
        with patch("cloudops_agent.pubsub._context", return_value=("p", "r")):
            with patch("cloudops_agent.pubsub._session"):
                with patch("cloudops_agent.pubsub._request", return_value={"name": "sub", "topic": "topic"}):
                    with patch("cloudops_agent.pubsub._time_series", return_value=([], False, [])):
                        result = diagnose_gcp_subscription("sub")
        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertTrue(result["partial"])

    def test_invalid_names_and_thresholds_do_not_query(self):
        with patch("cloudops_agent.pubsub._session") as session:
            self.assertIn("error", diagnose_gcp_subscription("projects/p/subscriptions/sub"))
            self.assertIn("error", diagnose_gcp_subscription("sub", backlog_threshold=0))
            session.assert_not_called()

    def test_pull_and_sink_types(self):
        self.assertEqual(subscription_summary({})["delivery_type"], "pull")
        self.assertEqual(
            subscription_summary({"bigqueryConfig": {"table": "table"}})["delivery_type"], "bigquery"
        )
