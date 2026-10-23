import unittest
from unittest.mock import patch

from cloudops_agent.runtime import (
    check_gcp_service_slo,
    distribution_summary,
    evaluate_slo,
    get_gcp_resource_pressure,
)


class RuntimeTest(unittest.TestCase):
    def test_distribution_weights_and_revision_isolation(self):
        rows = [
            {
                "resource": {"labels": {"revision_name": "r1"}},
                "metric": {"labels": {"container_name": "api"}},
                "points": [
                    {"value": {"distributionValue": {"count": "9", "mean": 0.2}}},
                    {"value": {"distributionValue": {"count": "1", "mean": 0.9}}},
                ],
            }
        ]
        groups = distribution_summary(rows)
        self.assertAlmostEqual(groups[0]["weighted_mean"], 0.27)
        self.assertEqual(groups[0]["highest_interval_mean"], 0.9)
        self.assertEqual(groups[0]["sample_count"], 10)

    def test_zero_distribution_is_unknown(self):
        self.assertIsNone(
            distribution_summary([{"points": [{"value": {"distributionValue": {"count": "0"}}}]}])[0][
                "weighted_mean"
            ]
        )

    def test_slo_burn_rate_and_threshold(self):
        report = evaluate_slo(
            {"request_count": 1000, "server_error_count": 10, "server_error_rate": 0.01}, 0.999, 100
        )
        self.assertEqual(report["status"], "outside_target")
        self.assertAlmostEqual(report["observed_burn_rate"], 10)
        self.assertEqual(report["observed_success_fraction"], 0.99)

    def test_partial_and_small_samples_never_pass_slo(self):
        for metrics in (
            {"request_count": 10, "server_error_count": 0, "server_error_rate": 0},
            {"request_count": 1000, "server_error_count": 0, "server_error_rate": 0, "partial": True},
            {"request_count": None},
        ):
            self.assertEqual(evaluate_slo(metrics, 0.999, 100)["status"], "insufficient_evidence")

    def test_zero_errors_can_meet_target(self):
        self.assertEqual(
            evaluate_slo(
                {"request_count": 1000, "server_error_count": 0, "server_error_rate": 0}, 0.999, 100
            )["status"],
            "within_target",
        )

    def test_invalid_slo_inputs_do_not_query(self):
        with patch("cloudops_agent.runtime.get_gcp_service_metrics") as query:
            for target in (1, 0, float("nan"), True):
                self.assertIn("error", check_gcp_service_slo("api", target=target))
            self.assertIn("error", check_gcp_service_slo("api", minimum_requests=0))
            query.assert_not_called()
        self.assertIn("error", get_gcp_resource_pressure("api", threshold=float("inf")))

    def test_pressure_query_preserves_partial_data(self):
        from cloudops_agent.gcp import GCPError

        rows = [
            {
                "resource": {"labels": {"revision_name": "api-001"}},
                "points": [{"value": {"distributionValue": {"count": "10", "mean": 0.9}}}],
            }
        ]
        with patch("cloudops_agent.runtime._context", return_value=("p", "r")):
            with patch("cloudops_agent.runtime._session"):
                with patch(
                    "cloudops_agent.runtime._metric",
                    side_effect=[(rows, False, []), GCPError("permission_denied", "denied")],
                ):
                    result = get_gcp_resource_pressure("api")
        self.assertTrue(result["partial"])
        self.assertEqual(result["pressure_signals"][0]["resource"], "cpu")
        self.assertIn("memory", result["errors"])

    def test_pressure_missing_data_does_not_report_low_utilization(self):
        with patch("cloudops_agent.runtime._context", return_value=("p", "r")):
            with patch("cloudops_agent.runtime._session"):
                with patch("cloudops_agent.runtime._metric", return_value=([], False, [])):
                    result = get_gcp_resource_pressure("api")
        self.assertEqual(result["data_status"], "no_data")
        self.assertEqual(result["pressure_signals"], [])
