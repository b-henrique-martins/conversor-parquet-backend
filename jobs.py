import datetime as dt
import uuid

import storage
from enums import JobStatus


def new_job_id() -> str:
    return uuid.uuid4().hex


def _job_key(job_id: str) -> str:
    return f"jobs/{job_id}.json"


def create(job_id: str, payload: dict):
    storage.put_json(_job_key(job_id), {
        "status": JobStatus.PENDING.value,
        "created_at": dt.datetime.utcnow().isoformat(),
        **payload,
    })


def update(job_id: str, **fields):
    key = _job_key(job_id)
    current = storage.get_json(key) or {}
    current.update(fields)
    current["updated_at"] = dt.datetime.utcnow().isoformat()
    storage.put_json(key, current)


def get(job_id: str) -> dict | None:
    return storage.get_json(_job_key(job_id))