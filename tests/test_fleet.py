import unittest
from unittest.mock import patch

from cloudops_agent.fleet import triage_gcp_services


def inventory(services, truncated=False):
    return {"project": "p", "region": "r", "services": services, "truncated": truncated}


def report(status="no_issue_detected", partial=False):
    return {
        "status": status,
        "partial": partial,
        "observed_at": "now",
        "gaps": [],
        "findings": [],
        "console_url": "https://console.cloud.google.com",
    }


class FleetTest(unittest.TestCase):
    def test_failed_readiness_is_inspected_first(self):
        services = [
            {"name": "services/a", "ready_condition": {"state": "CONDITION_SUCCEEDED"}},
            {"name": "services/z", "ready_condition": {"state": "CONDITION_FAILED"}},
        ]
        with patch("cloudops_agent.fleet.list_gcp_services", return_value=inventory(services)):
            with patch("cloudops_agent.fleet.diagnose_gcp_service", return_value=report()) as diagnose:
                result = triage_gcp_services(limit=1)
        diagnose.assert_called_once_with("z", 1)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["uninspected_services"], ["a"])

    def test_issue_remains_visible_when_another_collection_fails(self):
        services = [{"name": "services/a"}, {"name": "services/b"}]
        with patch("cloudops_agent.fleet.list_gcp_services", return_value=inventory(services)):
            with patch(
                "cloudops_agent.fleet.diagnose_gcp_service",
                side_effect=[report("issues_detected"), {"error": {"code": "deadline_exceeded"}}],
            ):
                result = triage_gcp_services()
        self.assertEqual(result["status"], "attention_needed")
        self.assertTrue(result["partial"])

    def test_empty_inventory_distinct_from_healthy(self):
        with patch("cloudops_agent.fleet.list_gcp_services", return_value=inventory([])):
            self.assertEqual(triage_gcp_services()["status"], "no_services")
        with patch("cloudops_agent.fleet.list_gcp_services", return_value=inventory([], True)):
            self.assertEqual(triage_gcp_services()["status"], "incomplete")

    def test_invalid_limit_prevents_inventory_request(self):
        with patch("cloudops_agent.fleet.list_gcp_services") as fetch:
            self.assertIn("error", triage_gcp_services(6))
            fetch.assert_not_called()
