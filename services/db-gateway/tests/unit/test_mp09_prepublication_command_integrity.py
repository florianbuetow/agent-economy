"""Behavioral reds for persisted V2 command identity integrity."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Any

import pytest
from service_commons.exceptions import ServiceError

from tests.fakes.mp09_input_publication_fixture import (
    BOARD_KID,
    POSTER_ID,
    PUBLICATION_ID,
    admission_payload,
    canonical_json,
    freeze_payload,
    full_snapshot,
    make_writer,
    open_prototype_database,
    publish_payload,
    require_prepublication_api,
    seed_poster,
)

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable
    from pathlib import Path

pytestmark = pytest.mark.unit


def _open_writer(tmp_path: Path) -> tuple[Any, sqlite3.Connection, Any]:
    api = require_prepublication_api()
    connection = open_prototype_database(tmp_path / "prepublication-command-integrity.sqlite3")
    seed_poster(connection)
    return api, connection, make_writer(api, connection)


def _admit(api: Any, writer: Any) -> tuple[Any, dict[str, Any]]:
    request = api.requests.parse_publication_admission_v2(admission_payload(expected_input_count=0))
    return request, writer.admit(request, verified_caller_kid=BOARD_KID)


def _freeze(api: Any, writer: Any, manifest_id: str) -> tuple[Any, dict[str, Any]]:
    request = api.requests.parse_input_manifest_freeze_v2(freeze_payload(manifest_id))
    return request, writer.freeze_inputs(request, verified_caller_kid=BOARD_KID)


def _publish(api: Any, writer: Any, contract_sha256: str) -> tuple[Any, dict[str, Any]]:
    request = api.requests.parse_publication_publish_v2(publish_payload(contract_sha256))
    return request, writer.authorize_publish(request, verified_caller_kid=BOARD_KID)


def _manifest_id(admitted: dict[str, Any]) -> str:
    value = admitted["input_manifest_id"]
    assert isinstance(value, str) and value
    return value


def _assert_state_corrupt(call: Callable[[], object], connection: sqlite3.Connection) -> None:
    before = full_snapshot(connection)
    with pytest.raises(ServiceError) as raised:
        call()
    assert (raised.value.status_code, raised.value.error) == (500, "publication_state_corrupt")
    assert full_snapshot(connection) == before


@pytest.mark.parametrize("operation", ["admit_replay", "freeze", "publish", "get", "reconcile"])
def test_base_hash_only_tamper_is_detected_before_every_command_or_read(
    tmp_path: Path,
    operation: str,
) -> None:
    api, connection, writer = _open_writer(tmp_path)
    try:
        admission_request, admitted = _admit(api, writer)
        manifest_id = _manifest_id(admitted)
        freeze_request = api.requests.parse_input_manifest_freeze_v2(freeze_payload(manifest_id))
        publish_request = api.requests.parse_publication_publish_v2(publish_payload("a" * 64))
        connection.execute(
            "UPDATE board_task_publications SET base_request_sha256=? WHERE operation_id=?",
            ("f" * 64, PUBLICATION_ID),
        )
        connection.commit()

        if operation == "admit_replay":
            call = partial(writer.admit, admission_request, verified_caller_kid=BOARD_KID)
        elif operation == "freeze":
            call = partial(
                writer.freeze_inputs,
                freeze_request,
                verified_caller_kid=BOARD_KID,
            )
        elif operation == "publish":
            call = partial(
                writer.authorize_publish,
                publish_request,
                verified_caller_kid=BOARD_KID,
            )
        elif operation == "get":
            call = partial(
                writer.get,
                PUBLICATION_ID,
                verified_caller_kid=BOARD_KID,
                expected_actor_id=POSTER_ID,
            )
        else:
            call = partial(writer.reconcile, PUBLICATION_ID, verified_caller_kid=BOARD_KID)
        _assert_state_corrupt(call, connection)
    finally:
        connection.close()


@pytest.mark.parametrize("tamper", ["canonical_only", "hash_only"])
@pytest.mark.parametrize("operation", ["freeze_replay", "publish", "get", "reconcile"])
def test_persisted_freeze_command_tamper_is_detected_everywhere(
    tmp_path: Path,
    tamper: str,
    operation: str,
) -> None:
    api, connection, writer = _open_writer(tmp_path)
    try:
        _admission_request, admitted = _admit(api, writer)
        freeze_request, frozen = _freeze(api, writer, _manifest_id(admitted))
        publish_request = api.requests.parse_publication_publish_v2(
            publish_payload(str(frozen["publication_contract_sha256"]))
        )
        if tamper == "canonical_only":
            connection.execute(
                "UPDATE board_task_publications SET canonical_freeze_request_json=? "
                "WHERE operation_id=?",
                (canonical_json({"tampered": "freeze"}), PUBLICATION_ID),
            )
        else:
            connection.execute(
                "UPDATE board_task_publications SET freeze_request_sha256=? WHERE operation_id=?",
                ("f" * 64, PUBLICATION_ID),
            )
        connection.commit()

        if operation == "freeze_replay":
            call = partial(
                writer.freeze_inputs,
                freeze_request,
                verified_caller_kid=BOARD_KID,
            )
        elif operation == "publish":
            call = partial(
                writer.authorize_publish,
                publish_request,
                verified_caller_kid=BOARD_KID,
            )
        elif operation == "get":
            call = partial(
                writer.get,
                PUBLICATION_ID,
                verified_caller_kid=BOARD_KID,
                expected_actor_id=POSTER_ID,
            )
        else:
            call = partial(writer.reconcile, PUBLICATION_ID, verified_caller_kid=BOARD_KID)
        _assert_state_corrupt(call, connection)
    finally:
        connection.close()


@pytest.mark.parametrize("tamper", ["canonical_only", "hash_only"])
@pytest.mark.parametrize("operation", ["publish_replay", "get", "reconcile"])
def test_persisted_publish_command_tamper_is_detected_everywhere(
    tmp_path: Path,
    tamper: str,
    operation: str,
) -> None:
    api, connection, writer = _open_writer(tmp_path)
    try:
        _admission_request, admitted = _admit(api, writer)
        _freeze_request, frozen = _freeze(api, writer, _manifest_id(admitted))
        publish_request, _authorized = _publish(
            api,
            writer,
            str(frozen["publication_contract_sha256"]),
        )
        if tamper == "canonical_only":
            connection.execute(
                "UPDATE board_task_publications SET canonical_publish_request_json=? "
                "WHERE operation_id=?",
                (canonical_json({"tampered": "publish"}), PUBLICATION_ID),
            )
        else:
            connection.execute(
                "UPDATE board_task_publications SET publish_request_sha256=? WHERE operation_id=?",
                ("f" * 64, PUBLICATION_ID),
            )
        connection.commit()

        if operation == "publish_replay":
            call = partial(
                writer.authorize_publish,
                publish_request,
                verified_caller_kid=BOARD_KID,
            )
        elif operation == "get":
            call = partial(
                writer.get,
                PUBLICATION_ID,
                verified_caller_kid=BOARD_KID,
                expected_actor_id=POSTER_ID,
            )
        else:
            call = partial(writer.reconcile, PUBLICATION_ID, verified_caller_kid=BOARD_KID)
        _assert_state_corrupt(call, connection)
    finally:
        connection.close()
