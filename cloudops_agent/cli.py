"""Offline demo and explicit read-only live checks: python -m cloudops_agent.cli."""

import argparse
import json
import sys
from importlib.resources import files

from dotenv import load_dotenv

from .diagnosis import build_report, diagnose_gcp_service
from .gcp import list_gcp_buckets, list_gcp_instances, list_gcp_services


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        default="cloudops_agent/.env",
        help="Optional configuration file; exported variables take precedence.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("demo", help="Run a synthetic offline diagnosis; no credentials or model calls.")
    demo.add_argument(
        "--scenario",
        choices=("startup-failure", "memory-pressure", "healthy", "missing-permissions"),
        default="startup-failure",
    )
    diagnose = sub.add_parser("diagnose", help="Collect a live, read-only Cloud Run diagnosis.")
    diagnose.add_argument("service")
    diagnose.add_argument("--hours", type=int, default=1)
    check = sub.add_parser(
        "check", help="Read-only live checks; prints counts/errors, not resource contents."
    )
    check.add_argument("--service", help="Also validate diagnosis for this Cloud Run service ID.")
    args = parser.parse_args(argv)
    if args.command == "demo":
        fixture = json.loads(
            files("cloudops_agent").joinpath("fixtures", args.scenario + ".json").read_text()
        )
        report = build_report("demo-api", "demo-project", "us-central1", fixture)
        report["mode"] = "offline_demo"
        report["notice"] = (
            "Synthetic fixture data; not live cloud infrastructure. observed_at is report generation time."
        )
        print(json.dumps(report, indent=2))
        return 0
    load_dotenv(args.env_file, override=False)
    if args.command == "diagnose":
        report = diagnose_gcp_service(args.service, args.hours)
        print(json.dumps(report, indent=2))
        return 2 if "error" in report or report.get("partial") else 0
    checks = {}
    for name, function, key in (
        ("cloud_run", list_gcp_services, "services"),
        ("compute", list_gcp_instances, "instances"),
        ("storage", list_gcp_buckets, "buckets"),
    ):
        result = function(limit=5)
        checks[name] = (
            {"error": result["error"]}
            if "error" in result
            else {
                "ok": True,
                "returned": len(result[key]),
                "truncated": result.get("truncated", False),
                "partial": result.get("partial", False),
            }
        )
    if args.service:
        result = diagnose_gcp_service(args.service)
        checks["diagnosis"] = {
            "status": result.get("status"),
            "gaps": result.get("gaps", []),
            "error": result.get("error"),
            "partial": result.get("partial", False),
        }
    print(json.dumps({"mode": "live_read_only_check", "checks": checks}, indent=2))
    return 2 if any(c.get("error") or c.get("partial") for c in checks.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
