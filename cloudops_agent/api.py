"""Stateless read-only HTTP API. Cloud Run IAM protects deployed access."""

from typing import Annotated

from fastapi import FastAPI, Path, Query
from fastapi.responses import JSONResponse

from .diagnosis import diagnose_gcp_service
from .gcp import list_gcp_services
from .incidents import build_gcp_incident_timeline, summarize_gcp_errors
from .jobs import diagnose_gcp_job, list_gcp_job_executions, list_gcp_jobs
from .observability import get_gcp_service_metrics, list_gcp_revisions
from .revisions import compare_gcp_revisions
from .runtime import check_gcp_service_slo, get_gcp_resource_pressure
from .snapshots import snapshot_gcp_services

app = FastAPI(title="CloudOps AI", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None)
ServiceID = Annotated[str, Path(pattern=r"^[a-z](?:[a-z0-9-]{0,48}[a-z0-9])?$", max_length=50)]

ResourceID = Annotated[str, Path(pattern=r"^[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$", max_length=63)]
RevisionQuery = Annotated[str, Query(pattern=r"^[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$", max_length=63)]


def _response(result):
    # Findings are successful diagnostic responses. Only collection failures use HTTP errors.
    code = result.get("error", {}).get("code")
    status = {
        "invalid_argument": 422,
        "configuration_error": 503,
        "authentication_failed": 503,
        "permission_denied_or_api_disabled": 502,
        "not_found": 404,
        "quota_exceeded": 503,
        "network_error": 503,
        "deadline_exceeded": 504,
        "api_error": 502,
        "invalid_response": 502,
        "read_only_violation": 500,
    }.get(code, 200)
    return JSONResponse(result, status_code=status, headers={"Cache-Control": "no-store"})


@app.get("/healthz")
def health():
    """Process health only; does not claim GCP connectivity."""
    return {"status": "ok"}


@app.get("/v1/services")
def services(limit: Annotated[int, Query(ge=1, le=500)] = 100):
    return _response(list_gcp_services(limit))


@app.get("/v1/services/{service_name}/diagnosis")
def diagnosis(service_name: ServiceID, hours: Annotated[int, Query(ge=1, le=24)] = 1):
    return _response(diagnose_gcp_service(service_name, hours))


@app.get("/v1/services/{service_name}/revisions")
def revisions(service_name: ServiceID, limit: Annotated[int, Query(ge=1, le=100)] = 10):
    return _response(list_gcp_revisions(service_name, limit))


@app.get("/v1/services/{service_name}/comparison")
def comparison(service_name: ServiceID, before: RevisionQuery, after: RevisionQuery):
    return _response(compare_gcp_revisions(service_name, before, after))


@app.get("/v1/services/{service_name}/metrics")
def metrics(service_name: ServiceID, hours: Annotated[int, Query(ge=1, le=24)] = 1):
    return _response(get_gcp_service_metrics(service_name, hours))


@app.get("/v1/services/{service_name}/pressure")
def pressure(
    service_name: ServiceID,
    hours: Annotated[int, Query(ge=1, le=24)] = 1,
    threshold: Annotated[float, Query(gt=0, lt=1)] = 0.85,
):
    return _response(get_gcp_resource_pressure(service_name, hours, threshold))


@app.get("/v1/services/{service_name}/slo")
def slo(
    service_name: ServiceID,
    hours: Annotated[int, Query(ge=1, le=24)] = 1,
    target: Annotated[float, Query(gt=0, lt=1)] = 0.999,
    minimum_requests: Annotated[int, Query(ge=1)] = 100,
):
    return _response(check_gcp_service_slo(service_name, target, hours, minimum_requests))


@app.get("/v1/services/{service_name}/errors")
def errors(
    service_name: ServiceID,
    hours: Annotated[int, Query(ge=1, le=168)] = 1,
    revision: Annotated[str, Query(pattern=r"^$|^[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$", max_length=63)] = "",
):
    return _response(summarize_gcp_errors(service_name, hours, revision))


@app.get("/v1/services/{service_name}/timeline")
def timeline(service_name: ServiceID, hours: Annotated[int, Query(ge=1, le=168)] = 1):
    return _response(build_gcp_incident_timeline(service_name, hours))


@app.get("/v1/jobs")
def jobs(limit: Annotated[int, Query(ge=1, le=500)] = 100):
    return _response(list_gcp_jobs(limit))


@app.get("/v1/jobs/{job_name}/executions")
def executions(job_name: ResourceID, limit: Annotated[int, Query(ge=1, le=100)] = 20):
    return _response(list_gcp_job_executions(job_name, limit))


@app.get("/v1/jobs/{job_name}/diagnosis")
def job_diagnosis(job_name: ResourceID, stale_after_hours: Annotated[int, Query(ge=1, le=8760)] = 24):
    return _response(diagnose_gcp_job(job_name, stale_after_hours))


@app.get("/v1/configuration-snapshot")
def configuration_snapshot(limit: Annotated[int, Query(ge=1, le=20)] = 20):
    return _response(snapshot_gcp_services(limit))
