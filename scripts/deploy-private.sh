#!/usr/bin/env bash
# Deploy an IAM-protected diagnostic API using an existing runtime identity.
set -euo pipefail
if [[ $# -lt 3 || $# -gt 4 ]]; then
  echo "Usage: $0 PROJECT_ID REGION SERVICE_ACCOUNT_EMAIL [SERVICE_NAME]" >&2
  exit 2
fi
project="$1"
region="$2"
identity="$3"
service="${4:-cloudops-ai}"
command -v gcloud >/dev/null || { echo "Install the gcloud CLI first." >&2; exit 1; }
cd "$(dirname "$0")/.."
gcloud run deploy "$service" \
  --project "$project" \
  --region "$region" \
  --source . \
  --service-account "$identity" \
  --no-allow-unauthenticated \
  --invoker-iam-check \
  --ingress all \
  --set-env-vars "GOOGLE_CLOUD_PROJECT=$project,GOOGLE_CLOUD_LOCATION=$region" \
  --min-instances 0 \
  --max-instances 2 \
  --concurrency 4 \
  --timeout 300 \
  --memory 512Mi \
  --cpu 1
