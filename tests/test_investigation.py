import copy
import unittest
from unittest.mock import patch

from cloudops_agent.investigation import enrich_investigation, investigate_gcp_service


def base():
    return {
        "provider": "gcp",
        "observed_at": "2026-01-01T00:00:00Z",
        "status": "no_issue_detected",
        "findings": [],
        "gaps": [],
        "evidence": [
            {
                "id": "E1",
                "source": "service",
                "data": {
                    "service": {"latest_ready_revision": "api-001", "latest_created_revision": "api-002"}
                },
            }
        ],
        "partial": False,
        "limitations": [],
    }


class InvestigationTest(unittest.TestCase):
    def test_new_findings_reference_evidence_and_preserve_input(self):
        diagnosis = base()
        original = copy.deepcopy(diagnosis)
        pressure = {
            "pressure_signals": [
                {
                    "resource": "memory",
                    "revision": "api-002",
                    "highest_interval_mean": 0.95,
                    "threshold": 0.85,
                }
            ]
        }
        result = enrich_investigation(
            diagnosis, pressure, {"changes": [{"field": "image", "before": "v1", "after": "v2"}]}
        )
        self.assertEqual(result["status"], "issues_detected")
        ids = {e["id"] for e in result["evidence"]}
        self.assertTrue(all(set(f["evidence_ids"]) <= ids for f in result["findings"]))
        self.assertEqual(diagnosis, original)

    def test_missing_pressure_does_not_claim_complete_health(self):
        result = enrich_investigation(base(), {"data_status": "no_data", "pressure_signals": []})
        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertTrue(result["partial"])

    def test_configuration_difference_alone_is_not_incident(self):
        result = enrich_investigation(
            base(), {"data_status": "available", "pressure_signals": []}, {"changes": [{"field": "image"}]}
        )
        self.assertEqual(result["status"], "no_issue_detected")
        self.assertEqual(result["findings"][0]["severity"], "info")

    def test_workflow_compares_only_distinct_ready_and_created_revisions(self):
        with patch("cloudops_agent.investigation.diagnose_gcp_service", return_value=base()):
            with patch(
                "cloudops_agent.investigation.get_gcp_resource_pressure",
                return_value={"data_status": "available"},
            ):
                with patch(
                    "cloudops_agent.investigation.compare_gcp_revisions", return_value={"changes": []}
                ) as compare:
                    investigate_gcp_service("api")
                    compare.assert_called_once_with("api", "api-001", "api-002")
        same = base()
        same["evidence"][0]["data"]["service"]["latest_created_revision"] = "api-001"
        with patch("cloudops_agent.investigation.diagnose_gcp_service", return_value=same):
            with patch("cloudops_agent.investigation.get_gcp_resource_pressure", return_value={}):
                with patch("cloudops_agent.investigation.compare_gcp_revisions") as compare:
                    investigate_gcp_service("api")
                    compare.assert_not_called()

    def test_base_error_stops_deeper_queries(self):
        with patch(
            "cloudops_agent.investigation.diagnose_gcp_service",
            return_value={"error": {"code": "invalid_argument"}},
        ):
            with patch("cloudops_agent.investigation.get_gcp_resource_pressure") as pressure:
                self.assertIn("error", investigate_gcp_service("bad/name"))
                pressure.assert_not_called()
