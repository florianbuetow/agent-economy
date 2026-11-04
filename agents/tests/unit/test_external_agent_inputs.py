"""Public example contract for protocol-2 task inputs."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

EXAMPLE_PATH = Path(__file__).resolve().parents[3] / "examples" / "external_agent.py"
WINDOWS = {
    "bidding_deadline_seconds": 120,
    "acceptance_window_seconds": 120,
    "execution_deadline_seconds": 300,
    "review_deadline_seconds": 120,
}


def _load_example():
    spec = importlib.util.spec_from_file_location("external_agent_inputs_unit", EXAMPLE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.unit
async def test_input_posting_uses_the_public_protocol_2_sdk() -> None:
    example = _load_example()
    publication_id = "op-00000000-0000-0000-0000-000000000001"

    def new_publication(**kwargs: str) -> dict[str, str]:
        return {"publication_operation_id": publication_id, "task_id": kwargs["task_id"]}

    async def publish(intent: dict[str, str]) -> tuple[int, dict[str, str]]:
        return 201, {"publication_state": "published", "task_id": intent["task_id"]}

    agent = SimpleNamespace(
        new_task_publication=Mock(side_effect=new_publication),
        admit_task_publication=AsyncMock(return_value={"input_manifest_id": "manifest-input"}),
        upload_task_input=AsyncMock(return_value={"asset_id": "asset-input"}),
        publish_task_publication=AsyncMock(side_effect=publish),
    )
    task_input = example.TaskInput(
        filename="request.txt",
        content=b"classify this",
        content_type="text/plain",
        idempotency_key="input-request-v1",
    )

    task_id = await example.post_task_with_inputs(
        agent,
        "Input task",
        "Return the classified input.",
        100,
        WINDOWS,
        (task_input,),
        example.Timing(poll_seconds=0.001, timeout_seconds=1.0),
    )

    assert task_id.startswith("t-")
    agent.new_task_publication.assert_called_once_with(
        task_id=task_id,
        title="Input task",
        spec="Return the classified input.",
        reward=100,
        bidding_deadline_seconds=120,
        acceptance_window_seconds=120,
        execution_deadline_seconds=300,
        review_deadline_seconds=120,
        expected_input_count=1,
    )
    intent = {"publication_operation_id": publication_id, "task_id": task_id}
    agent.admit_task_publication.assert_awaited_once_with(intent)
    agent.upload_task_input.assert_awaited_once_with(
        publication_id,
        "request.txt",
        b"classify this",
        content_type="text/plain",
        idempotency_key="input-request-v1",
    )
    agent.publish_task_publication.assert_awaited_once_with(intent)


@pytest.mark.unit
async def test_input_download_verifies_manifest_bytes() -> None:
    example = _load_example()
    content = b"trusted input"
    digest = hashlib.sha256(content).hexdigest()
    agent = SimpleNamespace(
        get_task_inputs=AsyncMock(
            return_value={
                "task_id": "t-input",
                "input_manifest": {
                    "files": [
                        {
                            "asset_id": "asset-input",
                            "filename": "request.txt",
                            "size_bytes": len(content),
                            "content_sha256": digest,
                        }
                    ]
                },
            }
        ),
        download_asset=AsyncMock(return_value=content),
    )

    inputs = await example.download_task_inputs(agent, "t-input")

    assert inputs == {"request.txt": content}
    agent.download_asset.assert_awaited_once_with("t-input", "asset-input")


@pytest.mark.unit
async def test_input_download_rejects_bytes_that_do_not_match_the_manifest() -> None:
    example = _load_example()
    agent = SimpleNamespace(
        get_task_inputs=AsyncMock(
            return_value={
                "task_id": "t-input",
                "input_manifest": {
                    "files": [
                        {
                            "asset_id": "asset-input",
                            "filename": "request.txt",
                            "size_bytes": 7,
                            "content_sha256": hashlib.sha256(b"correct").hexdigest(),
                        }
                    ]
                },
            }
        ),
        download_asset=AsyncMock(return_value=b"altered"),
    )

    with pytest.raises(RuntimeError, match="input manifest"):
        await example.download_task_inputs(agent, "t-input")
