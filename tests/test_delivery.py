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


class ReportExportTest(unittest.TestCase):
    def test_export_does_not_overwrite_and_markdown_contains_evidence(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as directory:
            path = Path(directory, "report.md")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["demo", "--output", str(path), "--format", "markdown"]), 0)
                before = path.read_text()
                self.assertEqual(main(["demo", "--output", str(path), "--format", "markdown"]), 2)
            self.assertEqual(path.read_text(), before)
            self.assertIn("## Full evidence", before)
            self.assertIn("Synthetic fixture data", before)

    def test_evidence_fence_cannot_be_closed_by_log_text(self):
        from cloudops_agent.reports import render_report

        result = render_report({"evidence": [{"message": "```\n<script>alert(1)</script>\n```"}]}, "markdown")
        self.assertIn("````json", result)

    def test_snapshot_diff_runs_without_cloud(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from cloudops_agent.revisions import fingerprint

        snapshot = {
            "schema_version": 1,
            "kind": "cloudops.cloud_run_configuration",
            "project": "p",
            "region": "r",
            "complete": True,
            "resources": {"api": {"configuration": {}, "fingerprint": fingerprint({})}},
        }
        with TemporaryDirectory() as directory:
            before = Path(directory, "before.json")
            after = Path(directory, "after.json")
            before.write_text(json.dumps(snapshot))
            after.write_text(json.dumps(snapshot))
            with patch("cloudops_agent.gcp._session", side_effect=AssertionError("cloud access")):
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(main(["diff", str(before), str(after)]), 0)
            self.assertEqual(json.loads(output.getvalue())["changed"], [])


class ExtendedApiTest(unittest.TestCase):
    def test_new_routes_validate_before_cloud_access(self):
        with patch("cloudops_agent.gcp._session", side_effect=AssertionError("unexpected cloud request")):
            with TestClient(app) as client:
                for url in (
                    "/v1/services/api/comparison?before=bad%22&after=api-002",
                    "/v1/services/api/pressure?threshold=2",
                    "/v1/services/api/slo?target=1",
                    "/v1/services/api/errors?revision=bad%22",
                    "/v1/jobs/batch/diagnosis?stale_after_hours=0",
                    "/v1/configuration-snapshot?limit=21",
                ):
                    self.assertEqual(client.get(url).status_code, 422, url)

    def test_comparison_and_snapshot_routes_preserve_results(self):
        with patch("cloudops_agent.api.compare_gcp_revisions", return_value={"changes": []}):
            with patch(
                "cloudops_agent.api.snapshot_gcp_services", return_value={"complete": True, "resources": {}}
            ):
                with TestClient(app) as client:
                    self.assertEqual(
                        client.get("/v1/services/api/comparison?before=api-001&after=api-002").json(),
                        {"changes": []},
                    )
                    self.assertTrue(client.get("/v1/configuration-snapshot").json()["complete"])


class DeepDemoTest(unittest.TestCase):
    def test_deep_demo_has_resource_and_comparison_evidence_without_network(self):
        with patch("cloudops_agent.gcp._session", side_effect=AssertionError("unexpected cloud call")):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(["demo", "--deep"]), 0)
        report = json.loads(output.getvalue())
        self.assertEqual(report["workflow"], "cloud_run_investigation/v1")
        self.assertTrue(
            {"resource_pressure", "revision_comparison"} <= {e["source"] for e in report["evidence"]}
        )

    def test_deep_memory_demo_explains_pressure_signal(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["demo", "--deep", "--scenario", "memory-pressure"]), 0)
        report = json.loads(output.getvalue())
        self.assertIn("resource_pressure_signal", {f["code"] for f in report["findings"]})
