# Project progress

## Implemented

- Read-only GCP inventory for Cloud Run, Compute Engine and Cloud Storage, plus bounded log queries.
- Cloud Run revision history and raw Monitoring summaries: requests, 4xx/5xx rates, weighted mean latency.
- Deterministic diagnosis with evidence IDs, suggestions, explicit data gaps and common-secret redaction.
- Four offline scenarios, a live-check/diagnosis CLI, and a packaged command entry point.
- Stateless HTTP API, non-root container, IAM-protected deployment script and runbook.
- 43 automated tests, lint/format checks, Python version matrix and container startup CI.
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

## Next increments after the MVP

- Incident-specific revision log filtering and per-revision before/after comparisons with matched time windows.
- Dependency checks and memory/CPU metrics where an incident warrants them.
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
