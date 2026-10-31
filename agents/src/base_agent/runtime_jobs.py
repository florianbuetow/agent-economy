"""Durable per-task worker job records under the runtime identity lease."""

from __future__ import annotations

import hashlib
import os
import re
from typing import TYPE_CHECKING, Any

from base_agent.runtime_intent_files import (
    decode_record,
    open_private_child,
    publish_record_exclusive,
    read_record_bytes,
    remove_private_staging_record,
    replace_record,
    validate_record_admission,
)
from base_agent.runtime_intent_models import (
    RuntimeIntentError,
    canonical_json_bytes,
    canonical_sha256,
    require_task_id,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from base_agent.runtime_intent_files import SafeDirectory
    from base_agent.runtime_intents import FileTaskIntentStore

JOB_DIRECTORY = "jobs"
_RECORD_NAME = re.compile(r"^[0-9a-f]{64}\.json$")
_STAGING_NAME = re.compile(r"^\.[0-9a-f]{64}\.json\.[0-9a-f]{24}\.tmp$")
_SCHEMA_VERSION = 1


def reconcile_job_directory(identity: SafeDirectory) -> None:
    """Remove crash-staging files and reject unknown entries in the job directory."""
    jobs = open_private_child(identity, JOB_DIRECTORY)
    try:
        for name in _scan(jobs):
            if _STAGING_NAME.fullmatch(name) is not None:
                remove_private_staging_record(jobs, name)
            elif _RECORD_NAME.fullmatch(name) is not None:
                validate_record_admission(jobs, name)
            else:
                raise RuntimeIntentError(
                    "corrupt_record", "job directory contains an unknown entry"
                )
    finally:
        jobs.close()


def _scan(directory: SafeDirectory) -> list[str]:
    try:
        with os.scandir(directory.descriptor) as entries:
            return sorted(entry.name for entry in entries)
    except OSError as exc:
        raise RuntimeIntentError("unsafe_path", "job directory cannot be scanned") from exc


class WorkerJobJournal:
    """One mutable JSON record per task, borrowing a parent intent store's lease.

    The journal owns only the envelope (schema version, owner binding, task
    identity); the worker owns the job fields inside it.
    """

    def __init__(self, parent: FileTaskIntentStore) -> None:
        self._parent = parent
        self._owner_sha256 = canonical_sha256(parent.owner.as_json())

    @staticmethod
    def _name(task_id: str) -> str:
        digest = hashlib.sha256(canonical_json_bytes({"task_id": task_id})).hexdigest()
        return f"{digest}.json"

    def _open(self) -> SafeDirectory:
        return self._parent.open_job_directory()

    def _decode(self, content: bytes, name: str) -> tuple[str, dict[str, Any]]:
        value = decode_record(content, name=name)
        if not isinstance(value, dict):
            raise RuntimeIntentError("corrupt_record", "job record is not an object")
        if canonical_json_bytes(value) != content:
            raise RuntimeIntentError("corrupt_record", "job record is not canonical JSON")
        if value.get("schema_version") != _SCHEMA_VERSION:
            raise RuntimeIntentError("corrupt_record", "job record has an unknown schema")
        if value.get("owner_sha256") != self._owner_sha256:
            raise RuntimeIntentError("corrupt_record", "job record belongs to another owner")
        task_id = value.get("task_id")
        if not isinstance(task_id, str) or self._name(task_id) != name:
            raise RuntimeIntentError("corrupt_record", "job filename and task identity conflict")
        fields = value.get("job")
        if not isinstance(fields, dict):
            raise RuntimeIntentError("corrupt_record", "job record has no job fields")
        return task_id, fields

    def load(self, task_id: str) -> dict[str, Any] | None:
        """Return the stored job fields for one task, or None."""
        task_id = require_task_id(task_id)
        name = self._name(task_id)
        jobs = self._open()
        try:
            content = read_record_bytes(jobs, name, max_record_bytes=self._parent.max_record_bytes)
        finally:
            jobs.close()
        if content is None:
            return None
        return self._decode(content, name)[1]

    def save(self, task_id: str, job: Mapping[str, Any]) -> None:
        """Durably create or replace the job fields for one task."""
        task_id = require_task_id(task_id)
        name = self._name(task_id)
        content = canonical_json_bytes(
            {
                "schema_version": _SCHEMA_VERSION,
                "owner_sha256": self._owner_sha256,
                "task_id": task_id,
                "job": dict(job),
            }
        )
        if len(content) > self._parent.max_record_bytes:
            raise RuntimeIntentError("record_too_large", "job record exceeds configured limit")
        jobs = self._open()
        try:
            if not publish_record_exclusive(jobs, name, content):
                replace_record(jobs, name, content)
        finally:
            jobs.close()

    def all(self) -> tuple[tuple[str, dict[str, Any]], ...]:
        """Return every stored job as ``(task_id, fields)`` sorted by task id."""
        jobs = self._open()
        records: list[tuple[str, dict[str, Any]]] = []
        try:
            for name in _scan(jobs):
                if _RECORD_NAME.fullmatch(name) is None:
                    continue
                content = read_record_bytes(
                    jobs, name, max_record_bytes=self._parent.max_record_bytes
                )
                if content is None:
                    raise RuntimeIntentError("corrupt_record", "job disappeared during scan")
                records.append(self._decode(content, name))
        finally:
            jobs.close()
        return tuple(sorted(records, key=lambda item: item[0]))
