"""T-148: malformed Gateway reads must not look like empty successful reads."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
import pytest
from service_commons.exceptions import ServiceError

from task_board_service.services.task_db_client import TaskDbClient
from tests.fakes.native_task_fixture import TaskTerms, native_task_context

if TYPE_CHECKING:
    from pathlib import Path


class ResponseClient(TaskDbClient):
    """Exercise public read methods with a controlled Gateway response."""

    def __init__(self, response: httpx.Response) -> None:
        self.response = response

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None,
        params: dict[str, Any] | None,
    ) -> httpx.Response:
        assert method == "GET"
        assert path.startswith("/board/")
        assert json is None
        assert params is None or isinstance(params, dict)
        return self.response


@pytest.mark.unit
@pytest.mark.parametrize("body", [b"not json", b"[]", b"null"])
@pytest.mark.parametrize("method", ["count_tasks_by_status", "get_assets_for_task"])
def test_malformed_success_is_a_dependency_error(body: bytes, method: str) -> None:
    client = ResponseClient(httpx.Response(200, content=body))
    with pytest.raises(ServiceError) as error:
        if method == "count_tasks_by_status":
            client.count_tasks_by_status()
        else:
            client.get_assets_for_task("t-contract")
    assert error.value.error == "invalid_gateway_response"


@pytest.mark.unit
@pytest.mark.parametrize("body", [{}, {"assets": None}, {"assets": [None]}])
def test_invalid_asset_collection_is_not_an_empty_success(body: dict[str, Any]) -> None:
    client = ResponseClient(httpx.Response(200, json=body))
    with pytest.raises(ServiceError) as error:
        client.get_assets_for_task("t-contract")
    assert error.value.error == "invalid_gateway_response"


@pytest.mark.unit
@pytest.mark.parametrize("count", [True, -1, "2", 1.5, None])
@pytest.mark.parametrize("method", ["count_tasks", "count_assets", "count_tasks_by_status"])
def test_counts_require_nonnegative_integers(count: Any, method: str) -> None:
    body = {"approved": count} if method == "count_tasks_by_status" else {"count": count}
    client = ResponseClient(httpx.Response(200, json=body))
    with pytest.raises(ServiceError) as error:
        if method == "count_tasks":
            client.count_tasks()
        elif method == "count_assets":
            client.count_assets("t-contract")
        else:
            client.count_tasks_by_status()
    assert error.value.error == "invalid_gateway_response"


@pytest.mark.unit
def test_empty_collections_and_zero_counts_are_valid() -> None:
    assert ResponseClient(httpx.Response(200, json={})).count_tasks_by_status() == {}
    assert ResponseClient(httpx.Response(200, json={"count": 0})).count_tasks() == 0
    assert ResponseClient(httpx.Response(200, json={"count": 0})).count_assets("t-contract") == 0
    assert (
        ResponseClient(httpx.Response(200, json={"assets": []})).get_assets_for_task("t-contract")
        == []
    )


@pytest.mark.unit
def test_task_reads_reject_missing_ordinary_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task_id = "t-00000000-0000-4000-8000-000000000148"
    with native_task_context(tmp_path, monkeypatch) as scenario:
        scenario.open_task(task_id, TaskTerms(100, 3600, 3600, 3600, 3600))
        original = scenario.task(task_id)
    for field in ("task_id", "poster_id", "title", "status", "reward", "created_at"):
        incomplete = {key: value for key, value in original.items() if key != field}
        client = ResponseClient(httpx.Response(200, json={"tasks": [incomplete]}))
        with pytest.raises(ServiceError) as error:
            client.list_tasks(None, None, None, None, None)
        assert error.value.error == "invalid_gateway_response"


@pytest.mark.unit
@pytest.mark.parametrize("method", ["count_tasks", "count_assets", "get_assets_for_task"])
def test_unstructured_gateway_failure_does_not_leak_body(method: str) -> None:
    secret_marker = "private-server-path-and-key"
    client = ResponseClient(httpx.Response(500, text=secret_marker))
    with pytest.raises(ServiceError) as error:
        if method == "count_tasks":
            client.count_tasks()
        elif method == "count_assets":
            client.count_assets("t-contract")
        else:
            client.get_assets_for_task("t-contract")
    assert error.value.error == "invalid_gateway_response"
    assert secret_marker not in str(error.value)


@pytest.mark.unit
@pytest.mark.parametrize("content_hash", [None, "a" * 64])
def test_asset_reads_preserve_current_nullable_hash_contract(content_hash: str | None) -> None:
    metadata = {
        "asset_id": "asset-contract",
        "task_id": "t-contract",
        "uploader_id": "a-worker",
        "filename": "answer.txt",
        "content_type": "text/plain",
        "size_bytes": 1,
        "storage_path": "live/aa/asset.blob",
        "content_hash": content_hash,
        "uploaded_at": "2026-11-04T00:00:00Z",
    }
    expected = {**metadata, "content_hash": "" if content_hash is None else content_hash}
    single = ResponseClient(httpx.Response(200, json=metadata))
    assert single.get_asset("asset-contract", "t-contract") == expected
    collection = ResponseClient(httpx.Response(200, json={"assets": [metadata]}))
    assert collection.get_assets_for_task("t-contract") == [
        {key: value for key, value in expected.items() if key != "storage_path"}
    ]
