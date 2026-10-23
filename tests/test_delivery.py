import contextlib
import io
import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from cloudops_agent.api import app
from cloudops_agent.cli import main


class DeliveryTest(unittest.TestCase):
    def test_all_demo_scenarios_run_without_network(self):
        for scenario, expected in (
            ("startup-failure", "issues_detected"),
            ("memory-pressure", "issues_detected"),
            ("healthy", "no_issue_detected"),
            ("missing-permissions", "insufficient_evidence"),
        ):
            output = io.StringIO()
            with patch("cloudops_agent.gcp._session", side_effect=AssertionError("demo made a cloud call")):
                with contextlib.redirect_stdout(output):
                    self.assertEqual(main(["demo", "--scenario", scenario]), 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result["status"], expected)
            self.assertEqual(result["mode"], "offline_demo")

    def test_process_health_needs_no_credentials(self):
        with TestClient(app) as client:
            self.assertEqual(client.get("/healthz").json(), {"status": "ok"})

    def test_api_validation_before_collection(self):
        with patch("cloudops_agent.api.diagnose_gcp_service") as diagnose:
            with TestClient(app) as client:
                for url in (
                    "/v1/services/Bad_Name/diagnosis",
                    "/v1/services/api/diagnosis?hours=0",
                    "/v1/services?limit=501",
                ):
                    self.assertEqual(client.get(url).status_code, 422)
            diagnose.assert_not_called()

    def test_api_diagnosis_preserves_evidence_and_does_not_cache(self):
        report = {
            "status": "issues_detected",
            "evidence": [{"id": "E1"}],
            "findings": [{"evidence_ids": ["E1"]}],
        }
        with patch("cloudops_agent.api.diagnose_gcp_service", return_value=report):
            with TestClient(app) as client:
                response = client.get("/v1/services/api/diagnosis")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), report)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_backend_auth_error_is_not_caller_auth_error(self):
        with patch(
            "cloudops_agent.api.list_gcp_services", return_value={"error": {"code": "authentication_failed"}}
        ):
            with TestClient(app) as client:
                self.assertEqual(client.get("/v1/services").status_code, 503)

    def test_live_check_exit_code_reports_partial_failure(self):
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch("cloudops_agent.cli.load_dotenv"))
            stack.enter_context(patch("cloudops_agent.cli.list_gcp_services", return_value={"services": []}))
            stack.enter_context(
                patch(
                    "cloudops_agent.cli.list_gcp_instances",
                    return_value={"error": {"code": "permission_denied_or_api_disabled"}},
                )
            )
            stack.enter_context(patch("cloudops_agent.cli.list_gcp_buckets", return_value={"buckets": []}))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            self.assertEqual(main(["check"]), 2)
