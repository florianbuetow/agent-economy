"""MP-05 integration review: economic owner filtering and monotonic publication."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from db_gateway_service import task_time
from tests.fakes.mp05_publication_history import (
    assert_uniform_error,
    create_funded_poster,
    lock_intent,
    publication_intent,
)
from tests.unit import test_mp05_publication_contract as publication_contract
from tests.unit.test_mp05_publication_contract import (
    PublicationGateway,
    _assert_blocked,
    _assert_no_task_visibility,
    _post,
    _reconcile,
    _signed_request,
)

if TYPE_CHECKING:
    import httpx

    from db_gateway_service.services.db_writer import DbWriter

pytestmark = pytest.mark.unit
publication_gateway = publication_contract.publication_gateway


@pytest.mark.parametrize("published", [False, True])
def test_economic_owner_filter_hides_foreign_publication(
    publication_gateway: PublicationGateway,
    published: bool,
) -> None:
    poster = create_funded_poster(publication_gateway.client)
    other = create_funded_poster(publication_gateway.client)
    intent = publication_intent(poster)
    admitted = _post(publication_gateway, intent)
    assert admitted.status_code == 202, admitted.text
    expected = admitted.json()
    if published:
        lock_intent(publication_gateway.client, intent)
        committed = _reconcile(publication_gateway, intent.operation_id)
        assert committed.status_code == 201, committed.text
        expected = committed.json()

    def status(operation_id: str, actor_id: str) -> httpx.Response:
        return _signed_request(
            publication_gateway,
            "board",
            "GET",
            f"/board/task-publications/{operation_id}?poster_id={actor_id}",
        )

    own = status(intent.operation_id, poster)
    assert own.status_code == 200, own.text
    assert own.json() == expected
    foreign = status(intent.operation_id, other)
    missing = status(f"op-{uuid4()}", other)
    error = assert_uniform_error(foreign, 404, "publication_not_found")
    assert missing.status_code == 404, missing.text
    assert (
        error
        == missing.json()
        == {
            "error": "publication_not_found",
            "message": "Publication not found",
            "details": {},
        }
    )


@pytest.mark.parametrize("boundary", ["admission", "lock_commit"])
def test_clock_regression_blocks_before_publication(
    publication_gateway: PublicationGateway,
    db_writer: DbWriter,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    poster = create_funded_poster(publication_gateway.client)
    intent = publication_intent(poster)
    admitted = _post(publication_gateway, intent)
    assert admitted.status_code == 202, admitted.text
    admitted_us = task_time.parse_utc_text_to_epoch_us(admitted.json()["admitted_at"])
    _escrow_id, lock_response = lock_intent(publication_gateway.client, intent)
    lock_us = task_time.parse_utc_text_to_epoch_us(lock_response.json()["receipt"]["committed_at"])
    assert lock_us > admitted_us
    sampled_us = admitted_us - 1 if boundary == "admission" else lock_us - 1
    monkeypatch.setattr(task_time, "utc_now_us", lambda: sampled_us)

    response = _reconcile(publication_gateway, intent.operation_id)
    blocked = _assert_blocked(response, intent, "clock_out_of_range")
    _assert_no_task_visibility(db_writer, intent.task_id)
    replay = _reconcile(publication_gateway, intent.operation_id)
    assert replay.status_code == 409
    assert replay.json() == blocked
