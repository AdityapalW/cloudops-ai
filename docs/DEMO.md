# Cloud Run troubleshooting demo

## Offline: no GCP project or model required

```sh
python -m cloudops_agent.cli demo --scenario startup-failure
python -m cloudops_agent.cli demo --scenario memory-pressure
python -m cloudops_agent.cli demo --scenario healthy
python -m cloudops_agent.cli demo --scenario missing-permissions
```

The fixtures are clearly marked synthetic. They run through the same report builder as live diagnosis. Timestamps in fixture evidence are illustrative; the report's `observed_at` is generation time.

Expected outcomes:

| Scenario | Status | Evidence to inspect |
| --- | --- | --- |
| startup-failure | issues_detected | New revision differs from ready revision; startup failure log signature. Older revision still receives traffic. |
| memory-pressure | issues_detected | Memory-limit log signature and observed 5xx responses; root cause is a hypothesis. |
| healthy | no_issue_detected | Explicit readiness success, nonzero traffic, no sampled errors; no end-to-end health guarantee. |
| missing-permissions | insufficient_evidence | Logs and metrics are unavailable; readiness alone cannot establish health. |

## Live: existing service only

```sh
python -m cloudops_agent.cli check --service YOUR_SERVICE_ID
python -m cloudops_agent.cli diagnose YOUR_SERVICE_ID --hours 1
```

`check` prints counts and collection errors rather than resource payloads. `diagnose` prints a full report with evidence and best-effort log redaction. Export project/region variables or configure `cloudops_agent/.env`. `--env-file PATH` goes before the subcommand. Shell variables take precedence.

Exit code 2 means configuration/collection failure or incomplete evidence. A complete diagnosis with issues still returns 0: infrastructure findings are valid report results, not execution errors.

For chat, start `adk web .` locally, select `cloudops_agent`, and ask: “Why is YOUR_SERVICE_ID failing? Check the last hour and cite your evidence.” Chat also requires Vertex AI/model access and can incur model usage charges.

## Optional end-to-end failure exercise

Use an isolated sandbox project/service you control. Deploy a known-good container, generate a few requests, then deploy a revision whose process never listens on the configured port. Do not change a production service for this exercise. Wait for readiness/logging/Monitoring updates before collecting evidence.

Expected behavior: the report identifies that the newest revision differs from the ready revision, includes startup-related evidence where present, and shows which revision still receives traffic. A failed deployment can leave the older service healthy; the report must not assume a user-visible outage or blame all 5xx responses on the new revision.

No failure exercise is created automatically. Demo fixtures provide a repeatable exercise without infrastructure changes.

## Isolate a rollout

Ask the agent to list revisions, then compare two explicit revision IDs with `compare_gcp_revisions`. It reports nested image, resource, concurrency, timeout, identity, scaling and VPC differences. Sensitive configuration is excluded; omitted fields versus explicit defaults can differ without changing behavior.

`list_gcp_logs` also accepts `revision_name` together with `service_name`, so an error investigation can focus on one revision while retaining project/region/service scope.

## Check pressure and an availability target

- `get_gcp_resource_pressure("api", hours=1)` summarizes CPU/memory distributions and highlights interval means at or above a configurable 0.85 fraction. This is a diagnostic heuristic, not an autoscaling action. The underlying metrics aggregate across instances; container labels are reported only when available.
- `check_gcp_service_slo("api", target=0.999, hours=1, minimum_requests=100)` evaluates HTTP 5xx against an explicit target. Missing, partial, or low-volume data returns `insufficient_evidence`. The burn rate describes observed error fraction divided by allowed error fraction.

Cloud Run's request counter excludes requests that never reach a container. This is therefore a container-response availability proxy, not a complete external uptime measure. The reported request latency excludes container startup time. These definitions follow the [Cloud Run metric catalog](https://docs.cloud.google.com/monitoring/api/metrics_gcp_p_z#run).
