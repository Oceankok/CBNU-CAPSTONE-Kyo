"""Execution capabilities shared by API routes and deployment health checks."""

import os

from fastapi import HTTPException


def local_processing_enabled() -> bool:
    return os.environ.get("PPE_LOCAL_PROCESSING", "true").lower() == "true"


def require_local_processing() -> None:
    if not local_processing_enabled():
        raise HTTPException(503, "processing_worker_not_connected")
