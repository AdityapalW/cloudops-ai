import unittest
from unittest.mock import patch

from cloudops_agent.incidents import build_gcp_incident_timeline, error_groups, timestamp


class IncidentTest(unittest.TestCase):
    def test_normalizes_ids_preserves_status_and_redacts(self):
        entries = [
            {"textPayload": "HTTP 500 request_id=one password=private", "severity": "ERROR"},
            {"textPayload": "HTTP 500 request_id=two password=private", "severity": "ERROR"},
            {"textPayload": "HTTP 404 request_id=three password=private", "severity": "ERROR"},
        ]
        result = error_groups(entries)
        self.assertEqual(len(result["groups"]), 2)
        self.assertEqual(result["groups"][0]["count"], 2)
        self.assertNotIn("private", str(result))

    def test_insert_id_dedup_is_scoped_by_log_and_timestamp(self):
        entry = {
            "insertId": "id",
            "logName": "log",
            "timestamp": "2026-01-01T00:00:00Z",
            "textPayload": "failed",
        }
        result = error_groups(
            [entry, dict(entry), dict(entry, logName="other"), dict(entry, timestamp="2026-01-01T00:00:01Z")]
        )
        self.assertEqual(result["unique_entries"], 3)
        self.assertEqual(result["duplicates_removed"], 1)

    def test_timestamp_order_uses_timezone_and_fraction(self):
        result = error_groups(
            [
                {"textPayload": "failed", "timestamp": "2026-01-01T01:00:00+01:00"},
                {"textPayload": "failed", "timestamp": "2026-01-01T00:00:00.500Z"},
            ]
        )
        self.assertEqual(result["groups"][0]["first_seen"], "2026-01-01T00:00:00+00:00")
        self.assertEqual(result["groups"][0]["last_seen"], "2026-01-01T00:00:00.500000+00:00")
        self.assertIsNone(timestamp("2026-01-01T00:00:00"))

    def test_timeline_preserves_one_source_when_other_fails(self):
        with patch(
            "cloudops_agent.incidents.summarize_gcp_errors",
            return_value={"error": {"code": "permission_denied"}},
        ):
            with patch(
                "cloudops_agent.incidents.list_gcp_revisions",
                return_value={"revisions": [{"name": "api-1", "created": "2026-01-01T00:00:00Z"}]},
            ):
                result = build_gcp_incident_timeline("api")
        self.assertTrue(result["partial"])
        self.assertEqual(result["events"][0]["type"], "revision_created")

    def test_invalid_log_time_is_reported(self):
        result = error_groups([{"textPayload": "failed", "timestamp": "invalid"}])
        self.assertEqual(result["entries_without_valid_timestamp"], 1)
        self.assertIsNone(result["groups"][0]["first_seen"])
