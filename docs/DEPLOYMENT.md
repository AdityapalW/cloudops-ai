# Private deployment

The container runs the stateless diagnostic HTTP API, with no chat/model calls. The ADK web interface remains a local development tool. Production API access is protected by Cloud Run IAM; the application itself does not verify identity tokens. Do not expose the container directly on a public network or grant `allUsers`/`allAuthenticatedUsers` the invoker role.

## Runtime identity and prerequisites

Install the Google Cloud CLI and Docker if building locally. Choose a project/region and create or select a dedicated runtime service account. It needs only the relevant read roles on the inspected project:

- `roles/run.viewer` for services and revisions.
- `roles/logging.viewer` for non-private logs.
- `roles/monitoring.viewer` for time series.
- `roles/compute.viewer` and `roles/storage.bucketViewer` only if using those inventory tools locally; the deployed API currently exposes Cloud Run diagnosis only.

Cloud Run, Logging, Monitoring, Artifact Registry, and Cloud Build APIs must be enabled for source deployment. The deployer needs source-deployment permissions, permission to act as the runtime identity, and permission to configure the service's IAM. Source builds also require a build identity with `roles/run.builder`; configure it according to your organization's setup. Build and runtime identities serve different purposes. Never bake ADC files, `.env`, or service account keys into the image.

Source deployment creates billable build/artifact/service resources. The supplied script scales to zero, caps instances at two, and never changes the runtime service account's project roles. Inspect it before running:

```sh
scripts/deploy-private.sh YOUR_PROJECT_ID us-central1 cloudops-runtime@YOUR_PROJECT_ID.iam.gserviceaccount.com
```

`--invoker-iam-check` explicitly enables the platform identity check, and `--no-allow-unauthenticated` requests IAM-protected access. Review existing service IAM if reusing a service name, especially inherited/public access. The service has network ingress enabled for authenticated external callers; IAM is the access boundary. Organizational policies may require more restrictive ingress.

After deployment, explicitly grant individual users `roles/run.invoker` on this service, then use the authenticated local proxy:

```sh
gcloud run services add-iam-policy-binding cloudops-ai \
  --project YOUR_PROJECT_ID --region us-central1 \
  --member user:YOUR_EMAIL --role roles/run.invoker
gcloud run services proxy cloudops-ai --project YOUR_PROJECT_ID --region us-central1 --port 8080
```

In another terminal:

```sh
curl --fail http://127.0.0.1:8080/healthz
curl --fail http://127.0.0.1:8080/v1/services
curl --fail 'http://127.0.0.1:8080/v1/services/YOUR_SERVICE_ID/diagnosis?hours=1'
```

Test the direct service URL without credentials too; it should be rejected by Cloud Run. `/healthz` verifies process health only. Reports use `Cache-Control: no-store`; do not publish full reports or proxy output without reviewing sensitive data.

## Local API and container

```sh
# Export environment variables first; the HTTP server does not load .env files.
python -m cloudops_agent.server
# Or bind the container only to loopback:
docker build -t cloudops-ai .
docker run --rm -p 127.0.0.1:8080:8080 \
  -e GOOGLE_CLOUD_PROJECT=YOUR_PROJECT_ID \
  -e GOOGLE_CLOUD_LOCATION=us-central1 \
  cloudops-ai
```

Container health can be tested without credentials. Live container diagnostics need an attached runtime identity (on Cloud Run), or an explicitly mounted local ADC configuration for local testing. CLI and API diagnosis do not require Vertex AI permissions. No sessions, reports, or credentials are stored by the API.

API endpoints:

| Method and route | Purpose |
| --- | --- |
| GET `/healthz` | Process health, no cloud calls |
| GET `/v1/services?limit=100` | Regional Cloud Run inventory |
| GET `/v1/services/{id}/diagnosis?hours=1` | Evidence-backed read-only triage |

Partial diagnosis is a 200 response with explicit gaps. Backend auth/network/configuration failures use 5xx responses; invalid query/path values use 422. A not-ready service is a successful diagnostic finding, not an API error.

References: [Cloud Run source deployment](https://docs.cloud.google.com/run/docs/deploying-source-code), [authenticated access](https://docs.cloud.google.com/run/docs/authenticating/developers).
