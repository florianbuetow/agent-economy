"""Proxy route handlers — task lifecycle via UserAgent."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from service_commons.exceptions import ServiceError

from ui_service.config import get_settings
from ui_service.core.deps import DbConn, TaskBoardRead
from ui_service.schemas import (
    BidItem,
    FeedbackRequest,
    FileDisputeRequest,
    NativeMutationRequest,
    OperatorBidListResponse,
    ProxyIdentityResponse,
    RebuttalRequest,
    SubmitBidRequest,
    TaskPostingPrepareRequest,
    TaskPostingRecord,
)
from ui_service.services import proxy as proxy_service
from ui_service.services import tasks as tasks_service
from ui_service.services.task_board_reads import get_authoritative_task

router = APIRouter()


@router.get("/proxy/browser-config")
async def get_browser_config() -> dict[str, str]:
    """Return the non-secret same-origin marker required for browser writes."""
    security = get_settings().browser_security
    return {
        "csrf_header_name": security.csrf_header_name,
        "csrf_header_value": security.csrf_header_value,
    }


@router.get("/proxy/identity")
async def get_identity() -> ProxyIdentityResponse:
    """Return the UserAgent's agent_id."""
    agent_id = await proxy_service.get_identity()
    return ProxyIdentityResponse(agent_id=agent_id)


@router.get("/proxy/tasks/{task_id}/assets/{asset_id}")
async def download_asset(task_id: str, asset_id: str) -> Response:
    """Return exact retained bytes through the configured operator authority."""
    upstream = await proxy_service.download_asset(task_id=task_id, asset_id=asset_id)
    return Response(
        content=upstream.content,
        media_type=upstream.headers["content-type"],
        headers={
            "Content-Disposition": upstream.headers["content-disposition"],
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/proxy/task-postings/prepare")
async def prepare_task_posting(body: TaskPostingPrepareRequest) -> dict[str, object]:
    """Allocate an effect-free immutable task-posting record."""
    return proxy_service.prepare_task_posting(
        title=body.title,
        spec=body.spec,
        reward=body.reward,
        bidding_deadline_seconds=body.bidding_deadline_seconds,
        acceptance_window_seconds=body.acceptance_window_seconds,
        execution_deadline_seconds=body.execution_deadline_seconds,
        review_deadline_seconds=body.review_deadline_seconds,
    )


@router.post("/proxy/task-postings/{operation_id}/submit")
async def submit_task_posting(operation_id: str, body: TaskPostingRecord) -> JSONResponse:
    """Submit one previously persisted task-posting record."""
    result = await proxy_service.submit_task_posting(
        operation_id=operation_id,
        record=body.model_dump(),
    )
    return JSONResponse(status_code=result[0], content=result[1])


@router.post("/proxy/task-postings/{operation_id}/status")
async def get_task_posting_status(operation_id: str, body: TaskPostingRecord) -> JSONResponse:
    """Reconcile one previously persisted task-posting record."""
    result = await proxy_service.get_task_posting_status(
        operation_id=operation_id,
        record=body.model_dump(),
    )
    return JSONResponse(status_code=result[0], content=result[1])


@router.get("/proxy/tasks/{task_id}/bids")
async def list_bids(task_id: str, db: DbConn) -> JSONResponse:
    """Return the operator poster's authoritative view of sealed bids."""
    authorized_bids = await proxy_service.list_bids(task_id=task_id)
    bids = await tasks_service.enrich_authorized_bids(db, authorized_bids)
    response = OperatorBidListResponse(
        task_id=task_id,
        bids=[BidItem(**bid) for bid in bids],
    )
    return JSONResponse(
        content=response.model_dump(by_alias=True),
        headers={"Cache-Control": "private, no-store"},
    )


@router.post("/proxy/tasks/{task_id}/bids/{bid_id}/accept")
async def accept_bid(
    task_id: str,
    bid_id: str,
    body: NativeMutationRequest,
) -> JSONResponse:
    """Accept a bid on a task."""
    result = await proxy_service.accept_bid(
        task_id=task_id,
        bid_id=bid_id,
        operation_id=body.operation_id,
        expected_lifecycle_version=body.expected_lifecycle_version,
    )
    return JSONResponse(status_code=result.status_code, content=result.body)


@router.post("/proxy/tasks/{task_id}/approve")
async def approve_task(
    task_id: str,
    body: NativeMutationRequest,
) -> JSONResponse:
    """Approve a submitted task."""
    result = await proxy_service.approve_task(
        task_id=task_id,
        operation_id=body.operation_id,
        expected_lifecycle_version=body.expected_lifecycle_version,
    )
    return JSONResponse(status_code=result.status_code, content=result.body)


@router.post("/proxy/tasks/{task_id}/dispute")
async def file_dispute(
    task_id: str,
    body: FileDisputeRequest,
) -> JSONResponse:
    """File a dispute on a task."""
    result = await proxy_service.file_dispute(
        task_id=task_id,
        reason=body.reason,
        operation_id=body.operation_id,
        expected_lifecycle_version=body.expected_lifecycle_version,
    )
    return JSONResponse(status_code=result.status_code, content=result.body)


@router.post("/proxy/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, body: NativeMutationRequest) -> JSONResponse:
    """Cancel the operator's own open task."""
    result = await proxy_service.cancel_task(
        task_id=task_id,
        operation_id=body.operation_id,
        expected_lifecycle_version=body.expected_lifecycle_version,
    )
    return JSONResponse(status_code=result.status_code, content=result.body)


@router.post("/proxy/tasks/{task_id}/bids")
async def submit_bid(task_id: str, body: SubmitBidRequest) -> JSONResponse:
    """Place a binding bid as the operator."""
    result = await proxy_service.submit_bid(
        task_id=task_id,
        amount=body.amount,
        operation_id=body.operation_id,
        expected_lifecycle_version=body.expected_lifecycle_version,
    )
    return JSONResponse(status_code=201, content=result)


@router.post("/proxy/tasks/{task_id}/assets")
async def upload_asset(task_id: str, request: Request) -> JSONResponse:
    """Forward one multipart deliverable upload as the assigned operator."""
    content_type = request.headers.get("content-type", "")
    if not content_type.lower().startswith("multipart/form-data"):
        raise ServiceError(
            "unsupported_media_type",
            "Content-Type must be multipart/form-data",
            415,
            {},
        )
    result = await proxy_service.upload_asset(
        task_id,
        content=await request.body(),
        content_type=content_type,
        idempotency_key=request.headers.get(proxy_service.IDEMPOTENCY_KEY_HEADER),
    )
    return JSONResponse(status_code=201, content=result)


@router.post("/proxy/tasks/{task_id}/submit")
async def submit_deliverable(task_id: str, body: NativeMutationRequest) -> JSONResponse:
    """Submit uploaded deliverables for review as the assigned operator."""
    result = await proxy_service.submit_deliverable(
        task_id=task_id,
        operation_id=body.operation_id,
        expected_lifecycle_version=body.expected_lifecycle_version,
    )
    return JSONResponse(status_code=result.status_code, content=result.body)


@router.post("/proxy/tasks/{task_id}/rebuttal")
async def submit_rebuttal(
    task_id: str, body: RebuttalRequest, task_board_reader: TaskBoardRead
) -> JSONResponse:
    """Rebut the dispute on a task the operator worked."""
    task = await get_authoritative_task(task_board_reader, task_id=task_id)
    result = await proxy_service.submit_rebuttal(task, body.rebuttal)
    return JSONResponse(status_code=200, content=result)


@router.post("/proxy/tasks/{task_id}/feedback")
async def submit_feedback(
    task_id: str, body: FeedbackRequest, task_board_reader: TaskBoardRead
) -> JSONResponse:
    """Rate the counterpart of an approved or ruled task."""
    task = await get_authoritative_task(task_board_reader, task_id=task_id)
    result = await proxy_service.submit_feedback(task, body.rating, body.comment)
    return JSONResponse(status_code=201, content=result)
