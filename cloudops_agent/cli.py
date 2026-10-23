"""Offline demos, GCP inspection and portable reports: python -m cloudops_agent.cli."""

import argparse
import json
import sys
from importlib.resources import files
from pathlib import Path

from dotenv import load_dotenv

from .diagnosis import build_report, diagnose_gcp_service
from .fleet import triage_gcp_services
from .gcp import GCPError, list_gcp_buckets, list_gcp_instances, list_gcp_services
from .incidents import build_gcp_incident_timeline, summarize_gcp_errors
from .investigation import enrich_investigation, investigate_gcp_service
from .jobs import diagnose_gcp_job, list_gcp_job_executions, list_gcp_jobs
from .observability import get_gcp_service_metrics, list_gcp_revisions
from .pubsub import diagnose_gcp_subscription, list_gcp_subscriptions
from .reports import render_report, write_report
from .revisions import compare_gcp_revisions
from .runtime import check_gcp_service_slo, get_gcp_resource_pressure
from .snapshots import compare_snapshots, snapshot_gcp_services


def _load_snapshot(path):
    if Path(path).stat().st_size > 10_000_000:
        raise ValueError("Snapshot exceeds the 10 MB input limit.")
    return json.loads(Path(path).read_text())


def _live_checks(service):
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
    if service:
        result = diagnose_gcp_service(service)
        checks["diagnosis"] = {
            "status": result.get("status"),
            "gaps": result.get("gaps", []),
            "error": result.get("error"),
            "partial": result.get("partial", False),
        }
    return {
        "mode": "live_read_only_check",
        "checks": checks,
        "partial": any(c.get("error") or c.get("partial") for c in checks.values()),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        default="cloudops_agent/.env",
        help="Configuration file; exported variables take precedence.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    commands = {}

    def command(name, help_text):
        item = sub.add_parser(name, help=help_text)
        item.add_argument("--output", help="Write a new report file; refuses to overwrite existing files.")
        item.add_argument("--format", choices=("json", "markdown"), default="json")
        commands[name] = item
        return item

    demo = command("demo", "Run a synthetic offline diagnosis; no credentials or model calls.")
    demo.add_argument(
        "--scenario",
        choices=("startup-failure", "memory-pressure", "healthy", "missing-permissions"),
        default="startup-failure",
    )
    demo.add_argument(
        "--deep",
        action="store_true",
        help="Include synthetic resource pressure and revision comparison evidence.",
    )
    for name in ("diagnose", "investigate", "metrics", "pressure", "errors", "timeline", "slo"):
        item = command(name, "Read-only Cloud Run " + name + ".")
        item.add_argument("service")
        item.add_argument("--hours", type=int, default=1)
    commands["pressure"].add_argument("--threshold", type=float, default=0.85)
    commands["errors"].add_argument("--revision", default="")
    commands["slo"].add_argument("--target", type=float, default=0.999)
    commands["slo"].add_argument("--minimum-requests", type=int, default=100)
    revisions = command("revisions", "List recent Cloud Run revisions.")
    revisions.add_argument("service")
    revisions.add_argument("--limit", type=int, default=10)
    compare = command("compare", "Compare two explicit Cloud Run revision IDs.")
    compare.add_argument("service")
    compare.add_argument("before")
    compare.add_argument("after")
    fleet = command("fleet", "Triage up to five regional services with coverage reporting.")
    fleet.add_argument("--limit", type=int, default=5)
    fleet.add_argument("--hours", type=int, default=1)
    jobs = command("jobs", "List regional Cloud Run Jobs.")
    jobs.add_argument("--limit", type=int, default=100)
    executions = command("executions", "Inspect a job's recent executions.")
    executions.add_argument("job")
    executions.add_argument("--limit", type=int, default=20)
    job = command("diagnose-job", "Triage job readiness and execution history.")
    job.add_argument("job")
    job.add_argument("--stale-after-hours", type=int, default=24)
    subscriptions = command("subscriptions", "List project Pub/Sub subscriptions.")
    subscriptions.add_argument("--limit", type=int, default=100)
    subscription = command("diagnose-subscription", "Inspect Pub/Sub backlog and acknowledgement age.")
    subscription.add_argument("subscription")
    subscription.add_argument("--backlog-threshold", type=int, default=1000)
    subscription.add_argument("--age-threshold-seconds", type=int, default=300)
    snapshot = command("snapshot", "Capture a portable Cloud Run configuration snapshot.")
    snapshot.add_argument("--limit", type=int, default=20)
    diff = command("diff", "Compare two local JSON snapshots without cloud access.")
    diff.add_argument("before")
    diff.add_argument("after")
    check = command("check", "Read-only live checks; prints counts/errors, not resource contents.")
    check.add_argument("--service")
    args = parser.parse_args(argv)
    try:
        if args.command == "demo":
            fixture = json.loads(
                files("cloudops_agent").joinpath("fixtures", args.scenario + ".json").read_text()
            )
            report = build_report("demo-api", "demo-project", "us-central1", fixture)
            if args.deep:
                memory = 0.95 if args.scenario == "memory-pressure" else 0.3
                pressure = {
                    "data_status": "available",
                    "partial": False,
                    "utilization": {
                        "memory": [
                            {"weighted_mean": memory, "highest_interval_mean": memory, "sample_count": 10}
                        ]
                    },
                    "pressure_signals": (
                        [
                            {
                                "resource": "memory",
                                "revision": "demo-api-001",
                                "highest_interval_mean": memory,
                                "threshold": 0.85,
                            }
                        ]
                        if memory >= 0.85
                        else []
                    ),
                }
                comparison = (
                    {
                        "changes": [
                            {
                                "field": "containers.container-0.image",
                                "before": "example.invalid/demo:v1",
                                "after": "example.invalid/demo:v2",
                            }
                        ],
                        "partial": False,
                    }
                    if args.scenario == "startup-failure"
                    else None
                )
                if args.scenario == "missing-permissions":
                    pressure = {
                        "error": {
                            "code": "permission_denied",
                            "message": "Synthetic missing Monitoring Viewer permission.",
                        }
                    }
                report = enrich_investigation(report, pressure, comparison)
            report.update(
                mode="offline_demo",
                notice="Synthetic fixture data; not live cloud infrastructure. observed_at is report generation time.",
            )
        elif args.command == "diff":
            report = compare_snapshots(_load_snapshot(args.before), _load_snapshot(args.after))
        else:
            load_dotenv(args.env_file, override=False)
            dispatch = {
                "investigate": lambda: investigate_gcp_service(args.service, args.hours),
                "diagnose": lambda: diagnose_gcp_service(args.service, args.hours),
                "metrics": lambda: get_gcp_service_metrics(args.service, args.hours),
                "pressure": lambda: get_gcp_resource_pressure(args.service, args.hours, args.threshold),
                "errors": lambda: summarize_gcp_errors(args.service, args.hours, args.revision),
                "timeline": lambda: build_gcp_incident_timeline(args.service, args.hours),
                "slo": lambda: check_gcp_service_slo(
                    args.service, args.target, args.hours, args.minimum_requests
                ),
                "revisions": lambda: list_gcp_revisions(args.service, args.limit),
                "compare": lambda: compare_gcp_revisions(args.service, args.before, args.after),
                "fleet": lambda: triage_gcp_services(args.limit, args.hours),
                "jobs": lambda: list_gcp_jobs(args.limit),
                "executions": lambda: list_gcp_job_executions(args.job, args.limit),
                "diagnose-job": lambda: diagnose_gcp_job(args.job, args.stale_after_hours),
                "subscriptions": lambda: list_gcp_subscriptions(args.limit),
                "diagnose-subscription": lambda: diagnose_gcp_subscription(
                    args.subscription, args.backlog_threshold, args.age_threshold_seconds
                ),
                "snapshot": lambda: snapshot_gcp_services(args.limit),
                "check": lambda: _live_checks(args.service),
            }
            report = dispatch[args.command]()
        text = render_report(report, args.format)
        if args.output:
            write_report(args.output, text)
            print(f"Saved report: {args.output}", file=sys.stderr)
        else:
            print(text, end="")
        incomplete = (
            report.get("partial")
            or report.get("complete") is False
            or report.get("slo", {}).get("status") == "insufficient_evidence"
        )
        # Demo scenarios intentionally include gaps but are successful offline runs.
        return 2 if args.command != "demo" and ("error" in report or incomplete) else 0
    except (OSError, ValueError, GCPError) as exc:
        print(f"cloudops: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
