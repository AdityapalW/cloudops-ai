import unittest
from unittest.mock import MagicMock, patch

from cloudops_agent.jobs import diagnose_gcp_job, execution_state, execution_summary, list_gcp_jobs


class JobsTest(unittest.TestCase):
    def test_running_failed_task_is_not_finished_execution_failure(self):
        self.assertEqual(
            execution_state({"failedCount": 1, "runningCount": 1, "reconciling": True}), "running"
        )

    def test_completed_outcomes_and_unknown(self):
        self.assertEqual(
            execution_state(
                {
                    "completionTime": "now",
                    "conditions": [{"type": "Completed", "state": "CONDITION_SUCCEEDED"}],
                }
            ),
            "succeeded",
        )
        self.assertEqual(execution_state({"completionTime": "now", "cancelledCount": 1}), "cancelled")
        self.assertEqual(execution_state({"completionTime": "now", "failedCount": 1}), "failed")
        self.assertEqual(execution_state({"completionTime": "now"}), "completed_unknown")

    def test_summary_excludes_task_secrets(self):
        self.assertNotIn(
            "private", str(execution_summary({"template": {"containers": [{"env": [{"value": "private"}]}]}}))
        )

    def test_no_success_is_not_healthy(self):
        with patch("cloudops_agent.jobs._context", return_value=("p", "r")):
            with patch("cloudops_agent.jobs._session"):
                with patch(
                    "cloudops_agent.jobs._request",
                    return_value={"terminalCondition": {"state": "CONDITION_SUCCEEDED"}},
                ):
                    with patch(
                        "cloudops_agent.jobs.list_gcp_job_executions", return_value={"executions": []}
                    ):
                        result = diagnose_gcp_job("batch")
        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertIsNone(result["latest_sampled_success_at"])

    def test_job_inventory_uses_bounded_read_and_omits_environment(self):
        with patch("cloudops_agent.jobs._context", return_value=("p", "r")):
            with patch("cloudops_agent.jobs._session") as session:
                request = session.return_value.__enter__.return_value.request
                request.return_value = MagicMock(
                    ok=True,
                    json=lambda: {
                        "jobs": [
                            {"name": "batch", "template": {"taskCount": 5, "template": {"env": "private"}}}
                        ]
                    },
                )
                result = list_gcp_jobs()
        self.assertEqual(result["jobs"][0]["task_count"], 5)
        self.assertNotIn("private", str(result))
        self.assertEqual(request.call_args.args[0], "GET")

    def test_invalid_age_does_not_query(self):
        with patch("cloudops_agent.jobs._session") as session:
            self.assertIn("error", diagnose_gcp_job("batch", 0))
            session.assert_not_called()
