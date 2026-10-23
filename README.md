# CloudOps AI

A read-only Google ADK assistant for inspecting GCP infrastructure. The existing AWS EC2 tool remains available.

## Setup

Use Python 3.11+ and install dependencies:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example cloudops_agent/.env
```

Edit `cloudops_agent/.env` with your project and region. The example uses Gemini through Vertex AI. Authenticate locally with Application Default Credentials:

```sh
gcloud auth application-default login
gcloud auth application-default set-quota-project YOUR_PROJECT_ID
adk web .
```

Select `cloudops_agent` in the ADK interface. In deployed environments, attach a service account rather than storing credential files. Direct Python calls require exporting the environment variables yourself; ADK loads the agent's `.env` file.

Enable the APIs needed for the tools you use: Cloud Run Admin (`run.googleapis.com`), Cloud Logging (`logging.googleapis.com`), Compute Engine (`compute.googleapis.com`), Cloud Storage JSON API (`storage.googleapis.com`), Cloud Monitoring (`monitoring.googleapis.com`), and Vertex AI (`aiplatform.googleapis.com`) for the model. API enablement is an administrator setup step; the agent does not enable APIs.

The caller needs the corresponding read permissions: `run.services.list`, `run.services.get`, `logging.logEntries.list`, `compute.instances.list`, and `storage.buckets.list`, `run.revisions.list`, and `monitoring.timeSeries.list`. Typical roles are Cloud Run Viewer, Logs Viewer, Compute Viewer, Storage Bucket Viewer, and Monitoring Viewer, scoped to the target project. Vertex AI model access also needs appropriate Vertex AI permissions. Grant only the roles needed for the tools you intend to use.

## GCP tools

| Tool | Scope | Returns |
| --- | --- | --- |
| `list_gcp_services` | Configured project and region | Cloud Run URLs and readiness conditions |
| `get_gcp_service` | One service ID in that region | Readiness, revisions, traffic, revision scaling, images, and CPU/memory resources |
| `list_gcp_revisions` | One regional service | Newest revisions, creation times, readiness, images, and limits |
| `get_gcp_service_metrics` | One regional service | Request volume, 4xx/5xx rates, and sample-weighted mean latency |
| `list_gcp_logs` | Project; optional regional Cloud Run service | Recent logs ordered newest first, with severity threshold |
| `list_gcp_instances` | All project zones | VM status, machine type, network IPs, and partial-result warnings |
| `list_gcp_buckets` | Project | Bucket location, storage class, versioning, and IAM configuration metadata |

Examples:

- “List my Cloud Run services.”
- “Why is the api service unhealthy? Check readiness and its ERROR logs from the last hour.”
- “Show all VMs in this project.”
- “List my storage buckets and whether versioning is enabled.”

Inventory tools default to 100 results (maximum 500). Logs default to the last hour and 50 results (maximum 200, up to 168 hours). Pagination is bounded to 20 requests per call and requests use a 30-second timeout. Check `truncated`, `partial`, and warnings before interpreting an inventory as complete. Rerun with narrower log filters or a higher limit when necessary. Errors are structured and distinct from empty results.

Service inspection excludes environment variables and secret references. Logs may still contain sensitive application data; use appropriately restricted log access. Bucket metadata does not establish effective public access, and service readiness alone does not establish application health. Tools make no infrastructure changes; the Logging POST endpoint is a read query.

## Verification

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Tests mock GCP APIs and require no cloud credentials. Live authentication, API permissions, and resources must be validated in your own project.

API references: [Cloud Run](https://docs.cloud.google.com/run/docs/reference/rest/v2/projects.locations.services), [Logging](https://docs.cloud.google.com/logging/docs/reference/v2/rest/v2/entries/list), [Compute Engine](https://docs.cloud.google.com/compute/docs/reference/rest/v1/instances/aggregatedList), [Storage](https://docs.cloud.google.com/storage/docs/json_api/v1/buckets/list).

Metrics use raw DELTA samples over 1–24 hours, capped at 10,000 points per metric and 20 pages. Missing data remains unknown. Mean latency is weighted by distribution sample count in milliseconds; percentiles are not inferred. OAuth uses the Cloud Platform scope required by these endpoints; IAM grants determine actual access, and tools issue read requests only.
