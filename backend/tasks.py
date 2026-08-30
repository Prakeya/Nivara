"""
Async job queue using Celery + Redis.

Falls back to synchronous processing when Redis is not available.
Set CELERY_BROKER_URL=redis://localhost:6379/0 to enable async mode.
"""

from __future__ import annotations

import os
import time
from typing import Any, Optional

CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "")
USE_CELERY = bool(CELERY_BROKER_URL)

if USE_CELERY:
    try:
        from celery import Celery
        celery_app = Celery("nivara", broker=CELERY_BROKER_URL)
        celery_app.conf.update(
            task_serializer="json",
            result_serializer="json",
            accept_content=["json"],
            task_track_started=True,
            task_time_limit=300,
            task_soft_time_limit=240,
        )
    except ImportError:
        USE_CELERY = False
        celery_app = None
else:
    celery_app = None


def process_upload_sync(job_id: str, file_paths: list[str], upload_hash: str) -> dict[str, Any]:
    """Synchronous upload processing (used when Celery is unavailable)."""
    from backend.main import _process_upload
    return _process_upload(job_id, file_paths, upload_hash)


if USE_CELERY and celery_app is not None:
    @celery_app.task(name="nivara.process_upload", bind=True, max_retries=2)
    def process_upload_task(self, job_id: str, file_paths: list[str], upload_hash: str):
        """Celery task for async upload processing."""
        try:
            from backend.main import _process_upload
            return _process_upload(job_id, file_paths, upload_hash)
        except Exception as exc:
            self.retry(exc=exc, countdown=60)
