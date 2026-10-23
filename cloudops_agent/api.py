"""Stateless read-only HTTP API. Cloud Run IAM protects deployed access."""

from typing import Annotated

from fastapi import FastAPI, Path, Query
from fastapi.responses import JSONResponse

from .diagnosis import diagnose_gcp_service
from .gcp import list_gcp_services

app = FastAPI(title="CloudOps AI", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None)
ServiceID = Annotated[str, Path(pattern=r"^[a-z](?:[a-z0-9-]{0,48}[a-z0-9])?$", max_length=50)]


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
