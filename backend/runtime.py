"""Execution capabilities shared by API routes and deployment health checks."""

import os

from fastapi import HTTPException


def local_processing_enabled() -> bool:
    return processing_mode() == 'local'


def processing_mode() -> str:
    mode = os.environ.get('PPE_PROCESSING_MODE')
    if mode is None:
        return 'local' if os.environ.get('PPE_LOCAL_PROCESSING', 'true').lower() == 'true' else 'disabled'
    if mode not in {'local', 'remote', 'disabled'}:
        raise RuntimeError('Invalid PPE_PROCESSING_MODE')
    return mode


def require_media_processing() -> None:
    if processing_mode() == 'disabled':
        raise HTTPException(503, 'processing_worker_not_connected')


def require_local_processing() -> None:
    if not local_processing_enabled():
        raise HTTPException(503, "processing_worker_not_connected")
