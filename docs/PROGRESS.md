# Project progress

## Implemented

- Read-only GCP inventory for Cloud Run, Compute Engine and Cloud Storage, plus bounded log queries.
- Cloud Run revision history and raw Monitoring summaries: requests, 4xx/5xx rates, weighted mean latency.
- Deterministic diagnosis with evidence IDs, suggestions, explicit data gaps and common-secret redaction.
- Four offline scenarios, a live-check/diagnosis CLI, and a packaged command entry point.
- Stateless HTTP API, non-root container, IAM-protected deployment script and runbook.
- 102 automated tests, lint/format checks, Python version matrix and container startup CI.
- Read-only transport allowlist, credential-refresh timeout, and shared 120-second nested collection budget.

## Verification record

- October 22, 2026 (America/Vancouver): live read-only Cloud Run listing succeeded with existing ADC and configured project/region, returning zero services. This verifies that listing/authentication path only.
- The live Storage listing also succeeded with zero buckets. Compute returned HTTP 403 (permission denied or API disabled); no permissions or APIs were changed.
- Logging API access succeeded with an empty result; Monitoring queries also succeeded with no data for an explicitly nonexistent probe service. These validate query/authentication paths, not real service telemetry.
- Real loopback HTTP startup returned 200 from `/healthz` and the server was shut down after the check.
- Clean dependency installs and offline verification passed on Python 3.11 and 3.13, in addition to the existing Python 3.14 environment.
- Offline regression coverage includes pagination, partial telemetry, error rates, weighted latency, readiness transitions, sensitive-data reduction, CLI scenarios and HTTP validation.

## Still requires live environment work

- Select or create a sandbox Cloud Run service for revision/log/Monitoring validation. No service exists in the currently configured region according to the list check.
- Run the documented failure exercise and confirm the agent's chat narrative accurately cites tool evidence.
- The local machine has neither Docker nor `gcloud`, and no deployment runtime service account has been selected. Build/run the container and deploy with that identity; verify authenticated access and rejection of anonymous requests.
- Have initial users run the demo and record unclear findings or missing diagnostic signals.

## Added in the continued GCP development pass

- Revision configuration comparison and revision-specific log filters.
- CPU/memory pressure summaries and explicit request-based availability targets.
- Redacted recurring-error grouping and revision/error timelines.
- Cloud Run Jobs inventory and execution diagnostics.
- Portable configuration snapshots with offline drift comparison.
- JSON/Markdown report export, expanded CLI/API, and deeper offline demos.
- Specific disabled-API/IAM/scope failure classifications.
- Pub/Sub subscription metadata and latest-gauge backlog diagnostics.
- Deeper service investigation that reuses evidence and a bounded fleet overview.
- Offline ADK runner integration tests for tool-call/result handling.

Live probes additionally confirmed Jobs and Pub/Sub reads (empty inventories), plus CPU/memory query acceptance for an explicitly nonexistent service. The Compute failure was resolved diagnostically to `api_disabled`; no API or IAM changes were made.

## Next increments after the MVP

- Per-revision before/after traffic comparisons with matched windows; existing revision comparison covers configuration.
- Dependency mapping and regional Pub/Sub metrics when an incident warrants them.
- Reproducible model evaluations for evidence citation and prompt injection resistance.
- Optional persistent incident history with explicit retention controls.
- Keep infrastructure mutations and expanded AWS work outside this milestone.


## Commit milestones

- `d5ea01a`: GCP inventory, logs, configuration and initial tests.
- `a652efd`: Revision history and request/latency metrics.
- `19c205b`: Evidence-backed diagnosis and log redaction.
- `109ac0b`: Read-only transport guard and diagnosis completeness fixes.
- `003f0ab`: Packaged API/CLI, four demo fixtures, dependency locks and delivery tests.
- `44a7ea0`: Shared collection deadlines and timeout handling.
- CI, container, deployment runbook and this tracker are committed as the deployment-preparation milestone.

Commits are local. No remote is configured in this checkout, so the GitHub Actions workflow has not run on GitHub. Docker/container checks are configured in CI but were not executed locally. The installed package's CLI demo was also verified from outside the repository to confirm fixture packaging.


## Continued-pass commits

- `0d7b7de`: revision comparisons and revision log isolation.
- `362905d`: resource pressure and availability targets.
- `6f62c6f`: error grouping and incident timelines.
- `f360926`: Cloud Run Jobs diagnostics.
- `738c083`: configuration snapshots and drift.
- `44eeb1f`: CLI/API workflows and report export.
- `7c82af5`: actionable API error reasons.
- `9897c84`: Pub/Sub delivery diagnostics.
- `95c55b7`: deeper investigations and evidence fixes.
- `9d320d5`: fleet triage and richer demos.
- `12fba78`: ADK runner integration tests.

The account usage check reached 93% of the five-hour window during the final verification phase. This is a checkpoint, not a claim that deployment or real-model quality evaluation is complete.
