"""Board domain endpoints — tasks, bids, task status, assets."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from service_commons.exceptions import ServiceError

from db_gateway_service.asset_admission import parse_asset_admission
from db_gateway_service.board_authority import BoardAuthority
from db_gateway_service.core.state import get_app_state
from db_gateway_service.effect_delivery_requests import (
    DELIVERY_STATES,
    parse_effect_operation_id,
    parse_empty_body,
    parse_failure_request,
    parse_generation,
    parse_lease_request,
    parse_observation_id,
)
from db_gateway_service.routers.helpers import (
    parse_json_body,
    validate_constraints,
    validate_event,
    validate_non_negative_integer,
    validate_positive_integer,
    validate_required_fields,
)
from db_gateway_service.task_operation_requests import parse_task_operation_request
from db_gateway_service.task_publication_requests import parse_task_publication_request

if TYPE_CHECKING:
    from db_gateway_service.services.db_writer import DbWriter

router = APIRouter(prefix="/board", tags=["Board"])


def _publication_response(result: dict[str, object]) -> JSONResponse:
    status = 201 if result["publication_state"] == "published" else 202
    return JSONResponse(status_code=status, content=result)


def _legacy_posting_response(result: dict[str, object]) -> JSONResponse:
    status = 200 if result["observation_state"] == "refunded" else 202
    return JSONResponse(status_code=status, content=result)


def _delivery_status(result: dict[str, object]) -> int:
    delivery = result.get("delivery")
    if not isinstance(delivery, dict):
        raise ServiceError("invalid_gateway_result", "Delivery result is invalid", 500, {})
    return 200 if delivery.get("delivery_state") == "completed" else 202


def _operation_context(request: Request) -> tuple[DbWriter, str, BoardAuthority]:
    """Obtain identities from verified request and receiver configuration only."""
    caller_kid: object = getattr(request.state, "gateway_caller_kid", None)
    if not isinstance(caller_kid, str) or not caller_kid:
        raise ServiceError("forbidden", "Authenticated Gateway caller is required", 403, {})
    authority: object = getattr(request.app.state, "board_authority", None)
    writer = get_app_state().db_writer
    if writer is None or not isinstance(authority, BoardAuthority):
        raise ServiceError("service_not_ready", "Task operation authority is not ready", 503, {})
    return writer, caller_kid, authority


@router.post("/task-publications")
async def admit_task_publication(request: Request) -> JSONResponse:
    """Reserve the complete posting identity before any external effect."""
    command = parse_task_publication_request(parse_json_body(await request.body()))
    writer, caller_kid, _authority = _operation_context(request)
    return _publication_response(writer.admit_task_publication(command, caller_kid))


@router.post("/task-publications/{operation_id}/reconcile")
async def reconcile_task_publication(operation_id: str, request: Request) -> JSONResponse:
    """Reconcile only from stored evidence, without accepting forwarded receipts."""
    if parse_json_body(await request.body()) != {}:
        raise ServiceError(
            "invalid_payload", "Reconciliation body must be an empty object", 400, {}
        )
    writer, caller_kid, _authority = _operation_context(request)
    return _publication_response(writer.reconcile_task_publication(operation_id, caller_kid))


@router.get("/task-publications/{operation_id}")
async def get_task_publication(operation_id: str, request: Request) -> JSONResponse:
    """Read publication state using the authenticated writer capability."""
    query = request.query_params
    if set(query) - {"poster_id"} or len(query.getlist("poster_id")) > 1:
        raise ServiceError("invalid_payload", "Only one optional poster_id is supported", 400, {})
    expected_actor_id = query.get("poster_id")
    if expected_actor_id == "":
        raise ServiceError("invalid_payload", "poster_id must be a nonempty string", 400, {})
    writer, caller_kid, _authority = _operation_context(request)
    return JSONResponse(
        status_code=200,
        content=writer.get_task_publication(
            operation_id, caller_kid, expected_actor_id=expected_actor_id
        ),
    )


@router.get("/posting-recovery")
async def list_posting_recovery(request: Request) -> JSONResponse:
    """Read an explicit bounded cursor page without broad Board read authority."""
    query = request.query_params
    if set(query) != {"after", "limit"} or any(
        len(query.getlist(name)) != 1 for name in ("after", "limit")
    ):
        raise ServiceError("invalid_payload", "Exactly one after and limit are required", 400, {})
    limit_text = query["limit"]
    if re.fullmatch(r"[1-9][0-9]{0,3}", limit_text) is None:
        raise ServiceError("invalid_payload", "limit must be an integer in 1..1000", 400, {})
    writer, caller_kid, _authority = _operation_context(request)
    result = writer.list_posting_recovery(query["after"], int(limit_text), caller_kid)
    return JSONResponse(status_code=200, content=result)


@router.get("/legacy-posting-observations/{observation_id}")
async def get_legacy_posting_observation(observation_id: str, request: Request) -> JSONResponse:
    """Read one verified caller's persisted refund status without effects."""
    writer, caller_kid, _authority = _operation_context(request)
    return JSONResponse(
        status_code=200,
        content=writer.get_legacy_posting_observation(observation_id, caller_kid),
    )


@router.post("/legacy-posting-observations/{observation_id}/reconcile")
async def reconcile_legacy_posting_observation(
    observation_id: str, request: Request
) -> JSONResponse:
    """Finalize one migrated orphan refund from persisted evidence only."""
    if parse_json_body(await request.body()) != {}:
        raise ServiceError(
            "invalid_payload", "Reconciliation body must be an empty object", 400, {}
        )
    writer, caller_kid, _authority = _operation_context(request)
    return _legacy_posting_response(
        writer.reconcile_legacy_posting_observation(observation_id, caller_kid)
    )


@router.post("/task-operations")
async def claim_task_operation(request: Request) -> JSONResponse:
    """Atomically execute or claim a versioned task command."""
    command = parse_task_operation_request(parse_json_body(await request.body()))
    writer, caller_kid, authority = _operation_context(request)
    result = writer.claim_task_operation(command, caller_kid, authority)
    status = 200 if result["operation_state"] == "completed" else 202
    return JSONResponse(status_code=status, content=result)


@router.post("/task-operations/{operation_id}/finalize")
async def finalize_task_operation(operation_id: str, request: Request) -> JSONResponse:
    """Finalize using Gateway evidence; no forwarded result or actor is accepted."""
    if parse_json_body(await request.body()) != {}:
        raise ServiceError("invalid_payload", "Finalization body must be an empty object", 400, {})
    writer, caller_kid, authority = _operation_context(request)
    result = writer.finalize_task_operation(operation_id, caller_kid, authority)
    status = 200 if result["operation_state"] == "completed" else 202
    return JSONResponse(status_code=status, content=result)


@router.post("/task-effect-deliveries/lease")
async def lease_task_effect_delivery(request: Request) -> JSONResponse:
    """Lease one due financial effect after the bounded evidence scan."""
    command = parse_lease_request(parse_json_body(await request.body()))
    writer, caller_kid, authority = _operation_context(request)
    result = writer.lease_effect_delivery(
        command.worker_id,
        caller_kid,
        authority,
        get_app_state().board_effect_recovery,
    )
    return JSONResponse(status_code=200, content=result)


@router.get("/task-effect-deliveries/{operation_id}")
async def get_task_effect_delivery(operation_id: str, request: Request) -> JSONResponse:
    """Return one safe caller-owned delivery diagnostic."""
    parsed = parse_effect_operation_id(operation_id)
    writer, caller_kid, authority = _operation_context(request)
    result = writer.get_effect_delivery(parsed, caller_kid, authority)
    return JSONResponse(status_code=200, content={"protocol_version": 1, "delivery": result})


@router.get("/task-effect-deliveries")
async def list_task_effect_deliveries(request: Request) -> JSONResponse:
    """Return one exact bounded lexical diagnostic page."""
    query = request.query_params
    if set(query) != {"state", "after", "limit"} or any(
        len(query.getlist(name)) != 1 for name in ("state", "after", "limit")
    ):
        raise ServiceError(
            "invalid_payload",
            "Exactly one state, after and limit are required",
            400,
            {},
        )
    state = query["state"]
    if state != "all" and state not in DELIVERY_STATES:
        raise ServiceError("invalid_payload", "state is not supported", 400, {})
    after = query["after"]
    if after:
        after = parse_effect_operation_id(after)
    limit_text = query["limit"]
    if re.fullmatch(r"[1-9][0-9]{0,3}", limit_text) is None:
        raise ServiceError("invalid_payload", "limit must be an integer in 1..1000", 400, {})
    limit = int(limit_text)
    if limit > 1000:
        raise ServiceError("invalid_payload", "limit must be an integer in 1..1000", 400, {})
    writer, caller_kid, authority = _operation_context(request)
    return JSONResponse(
        status_code=200,
        content=writer.list_effect_deliveries(
            state=state,
            after=after,
            limit=limit,
            caller_kid=caller_kid,
            authority=authority,
        ),
    )


@router.post("/task-effect-deliveries/{operation_id}/attempts/{generation}/fail")
async def fail_task_effect_delivery(
    operation_id: str,
    generation: str,
    request: Request,
) -> JSONResponse:
    """Record one exact fenced recovery failure."""
    parsed_operation = parse_effect_operation_id(operation_id)
    parsed_generation = parse_generation(generation)
    command = parse_failure_request(parse_json_body(await request.body()))
    writer, caller_kid, authority = _operation_context(request)
    return JSONResponse(
        status_code=200,
        content=writer.fail_effect_delivery(
            parsed_operation,
            parsed_generation,
            command.worker_id,
            command.error_code,
            caller_kid,
            authority,
            get_app_state().board_effect_recovery,
        ),
    )


@router.post("/task-effect-deliveries/{operation_id}/retry")
async def retry_task_effect_delivery(operation_id: str, request: Request) -> JSONResponse:
    """Requeue one caller-owned dead letter."""
    parsed = parse_effect_operation_id(operation_id)
    parse_empty_body(parse_json_body(await request.body()))
    writer, caller_kid, authority = _operation_context(request)
    return JSONResponse(
        status_code=200,
        content=writer.retry_effect_delivery(
            parsed,
            caller_kid,
            authority,
            get_app_state().board_effect_recovery,
        ),
    )


@router.post("/task-effect-deliveries/{operation_id}/finalize")
async def finalize_task_effect_delivery(operation_id: str, request: Request) -> JSONResponse:
    """Adopt a native financial receipt and task projection atomically."""
    parsed = parse_effect_operation_id(operation_id)
    parse_empty_body(parse_json_body(await request.body()))
    writer, caller_kid, authority = _operation_context(request)
    result = writer.finalize_effect_delivery(parsed, caller_kid, authority)
    return JSONResponse(status_code=_delivery_status(result), content=result)


@router.post("/legacy-effect-observations/{observation_id}/finalize")
async def finalize_legacy_effect_observation(
    observation_id: str,
    request: Request,
) -> JSONResponse:
    """Adopt one supported migrated settlement observation."""
    parsed = parse_observation_id(observation_id)
    parse_empty_body(parse_json_body(await request.body()))
    writer, caller_kid, _authority = _operation_context(request)
    result = writer.finalize_legacy_effect_observation(parsed, caller_kid)
    return JSONResponse(status_code=_delivery_status(result), content=result)


@router.post("/tasks", status_code=201)
async def create_task(request: Request) -> JSONResponse:
    """Create a new task."""
    body = await request.body()
    data = parse_json_body(body)
    validate_required_fields(
        data,
        [
            "task_id",
            "poster_id",
            "title",
            "spec",
            "reward",
            "status",
            "bidding_deadline_seconds",
            "deadline_seconds",
            "review_deadline_seconds",
            "bidding_deadline",
            "escrow_id",
            "created_at",
        ],
    )
    validate_event(data)
    validate_positive_integer(data, "reward")
    if "bid_count" not in data:
        data["bid_count"] = 0
    else:
        validate_non_negative_integer(data, "bid_count")
    if "escrow_pending" not in data:
        data["escrow_pending"] = 0
    else:
        validate_non_negative_integer(data, "escrow_pending")

    state = get_app_state()
    if state.db_writer is None:
        raise ServiceError(
            error="service_not_ready",
            message="DbWriter not initialized",
            status_code=503,
            details={},
        )

    result = state.db_writer.create_task(data)
    return JSONResponse(status_code=201, content=result)


@router.post("/bids", status_code=201)
async def submit_bid(request: Request) -> JSONResponse:
    """Submit a bid on a task."""
    body = await request.body()
    data = parse_json_body(body)
    validate_required_fields(
        data,
        ["bid_id", "task_id", "bidder_id", "proposal", "submitted_at"],
    )
    if "amount" not in data:
        data["amount"] = 0
    else:
        validate_positive_integer(data, "amount")
    constraints = validate_constraints(data)
    validate_event(data)

    state = get_app_state()
    if state.db_writer is None:
        raise ServiceError(
            error="service_not_ready",
            message="DbWriter not initialized",
            status_code=503,
            details={},
        )

    result = state.db_writer.submit_bid(data, constraints)
    return JSONResponse(status_code=201, content=result)


@router.post("/tasks/{task_id}/status")
async def update_task_status(task_id: str, request: Request) -> JSONResponse:
    """Update a task's status and associated fields."""
    body = await request.body()
    data = parse_json_body(body)

    # Validate updates object
    updates = data.get("updates")
    if updates is None:
        raise ServiceError(
            "missing_field",
            "Missing required field: updates",
            400,
            {"field": "updates"},
        )
    if not isinstance(updates, dict):
        raise ServiceError(
            "missing_field",
            "Field 'updates' must be an object",
            400,
            {"field": "updates"},
        )
    if len(updates) == 0:
        raise ServiceError(
            "empty_updates",
            "updates object contains no fields",
            400,
            {},
        )
    constraints = validate_constraints(data)
    validate_event(data)

    state = get_app_state()
    if state.db_writer is None:
        raise ServiceError(
            error="service_not_ready",
            message="DbWriter not initialized",
            status_code=503,
            details={},
        )

    result = state.db_writer.update_task_status(task_id, data, constraints)
    return JSONResponse(status_code=200, content=result)


@router.post("/assets", status_code=201)
async def record_asset(request: Request) -> JSONResponse:
    """Record an asset upload (metadata only)."""
    body = await request.body()
    data = parse_json_body(body)
    validate_required_fields(
        data,
        [
            "asset_id",
            "task_id",
            "uploader_id",
            "filename",
            "content_type",
            "size_bytes",
            "storage_path",
            "uploaded_at",
        ],
    )
    parse_asset_admission(data.get("admission"))
    constraints = validate_constraints(data)
    validate_event(data)

    state = get_app_state()
    if state.db_writer is None:
        raise ServiceError(
            error="service_not_ready",
            message="DbWriter not initialized",
            status_code=503,
            details={},
        )

    result = state.db_writer.record_asset(data, constraints)
    return JSONResponse(status_code=201, content=result)


@router.get("/tasks/count")
async def count_tasks() -> JSONResponse:
    """Count total tasks."""
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(
            error="service_not_ready",
            message="DbReader not initialized",
            status_code=503,
            details={},
        )

    count = state.db_reader.count_tasks()
    return JSONResponse(status_code=200, content={"count": count})


@router.get("/tasks/count-by-status")
async def count_tasks_by_status() -> JSONResponse:
    """Count tasks grouped by status."""
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(
            error="service_not_ready",
            message="DbReader not initialized",
            status_code=503,
            details={},
        )

    counts = state.db_reader.count_tasks_by_status()
    return JSONResponse(status_code=200, content=counts)


@router.get("/tasks")
async def list_tasks(request: Request) -> JSONResponse:
    """List tasks with optional status/poster/worker filters."""
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(
            error="service_not_ready",
            message="DbReader not initialized",
            status_code=503,
            details={},
        )

    params = request.query_params
    status = params.get("status")
    poster_id = params.get("poster_id")
    worker_id = params.get("worker_id")
    limit_str = params.get("limit")
    offset_str = params.get("offset")

    limit: int | None = None
    if limit_str is not None:
        try:
            limit = int(limit_str)
        except ValueError as exc:
            raise ServiceError("invalid_payload", "limit must be an integer", 400, {}) from exc

    offset: int | None = None
    if offset_str is not None:
        try:
            offset = int(offset_str)
        except ValueError as exc:
            raise ServiceError("invalid_payload", "offset must be an integer", 400, {}) from exc

    tasks = state.db_reader.list_tasks(status, poster_id, worker_id, limit, offset)
    return JSONResponse(status_code=200, content={"tasks": tasks})


@router.get("/tasks/{task_id}")
async def get_task(task_id: str) -> JSONResponse:
    """Get a task by ID."""
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(
            error="service_not_ready",
            message="DbReader not initialized",
            status_code=503,
            details={},
        )

    task = state.db_reader.get_task(task_id)
    if task is None:
        raise ServiceError(
            error="task_not_found",
            message="Task not found",
            status_code=404,
            details={},
        )
    return JSONResponse(status_code=200, content=task)


@router.get("/tasks/{task_id}/bids")
async def get_bids_for_task(task_id: str) -> JSONResponse:
    """Get bids for a task."""
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(
            error="service_not_ready",
            message="DbReader not initialized",
            status_code=503,
            details={},
        )

    bids = state.db_reader.get_bids_for_task(task_id)
    return JSONResponse(status_code=200, content={"bids": bids})


@router.get("/tasks/{task_id}/assets/count")
async def count_assets(task_id: str) -> JSONResponse:
    """Count assets for a task."""
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(
            error="service_not_ready",
            message="DbReader not initialized",
            status_code=503,
            details={},
        )

    count = state.db_reader.count_assets(task_id)
    return JSONResponse(status_code=200, content={"count": count})


@router.get("/tasks/{task_id}/assets")
async def get_assets_for_task(task_id: str) -> JSONResponse:
    """Get assets for a task."""
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(
            error="service_not_ready",
            message="DbReader not initialized",
            status_code=503,
            details={},
        )

    assets = state.db_reader.get_assets_for_task(task_id)
    return JSONResponse(status_code=200, content={"assets": assets})


@router.get("/bids/{bid_id}")
async def get_bid(bid_id: str, request: Request) -> JSONResponse:
    """Get a bid by ID with required task_id query parameter."""
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(
            error="service_not_ready",
            message="DbReader not initialized",
            status_code=503,
            details={},
        )

    task_id = request.query_params.get("task_id", "")
    bid = state.db_reader.get_bid(bid_id, task_id)
    if bid is None:
        raise ServiceError(
            error="bid_not_found",
            message="Bid not found",
            status_code=404,
            details={},
        )
    return JSONResponse(status_code=200, content=bid)


@router.get("/assets/{asset_id}")
async def get_asset(asset_id: str, request: Request) -> JSONResponse:
    """Get an asset by ID with required task_id query parameter."""
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(
            error="service_not_ready",
            message="DbReader not initialized",
            status_code=503,
            details={},
        )

    task_id = request.query_params.get("task_id", "")
    asset = state.db_reader.get_asset(asset_id, task_id)
    if asset is None:
        raise ServiceError(
            error="asset_not_found",
            message="Asset not found",
            status_code=404,
            details={},
        )
    return JSONResponse(status_code=200, content=asset)
