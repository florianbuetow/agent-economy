"""Response models for poster input attachments (completion plan 2.4, G03)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from task_board_service.schemas import TaskResponse


class TaskInputFile(BaseModel):
    """One frozen input file; download it via GET /tasks/{task_id}/assets/{asset_id}."""

    model_config = ConfigDict(extra="forbid")
    asset_id: str
    filename: str
    size_bytes: int
    content_sha256: str
    content_type: str
    role: str


class TaskInputManifest(BaseModel):
    """The input manifest frozen before publication and bound to the accepted terms."""

    model_config = ConfigDict(extra="forbid")
    manifest_id: str
    state: str
    manifest_sha256: str
    publication_contract_sha256: str
    accepted_publication_contract_sha256: str | None
    files: list[TaskInputFile]


class TaskDetailResponse(TaskResponse):
    """Task detail plus its frozen input manifest (null for tasks posted without one)."""

    input_manifest: TaskInputManifest | None


__all__ = ["TaskDetailResponse", "TaskInputFile", "TaskInputManifest"]
