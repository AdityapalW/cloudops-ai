"""Container entry point; use loopback binding for local development."""

import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "cloudops_agent.api:app",
        host=os.getenv("CLOUDOPS_HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8080")),
    )
