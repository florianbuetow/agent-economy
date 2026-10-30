"""DB Gateway-backed task storage."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

import httpx
from pydantic import ValidationError
from service_auth.task_postings import (
    TaskPostingIntent,
    TaskPostingResponse,
    parse_task_posting_status_response,
    parse_task_posting_submit_response,
)
from service_commons.exceptions import ServiceError

from task_board_service.effect_recovery_contract import (
    DiagnosticEnvelope,
    EffectDeliveryDiagnostic,
    parse_lease_envelope,
    validate_recovery_worker_id,
)
from task_board_service.posting_recovery_contract import (
    validate_legacy_blocked_wrapper,
    validate_legacy_observation_id,
    validate_legacy_posting_envelope,
    validate_recovery_page,
    validate_recovery_request,
)
from task_board_service.services.errors import AssetInsertRejectedError, DuplicateBidError
from task_board_service.services.protocol import RecoveryFinalizeResponse
from task_board_service.task_contract_projection import (
    GatewayBid,
    TaskContractProjection,
    validate_task_projection,
)
from task_board_service.task_operation_contract import (
    GatewayError,
    invalid_gateway_response,
    validate_command,
    validate_envelope,
    validate_operation_id,
)

if TYPE_CHECKING:
    from service_auth.gateway import GatewayRequestSigner

    from task_board_service.services.financial_effect_recovery import RecoveryFinalizeResult

# Re-exported so callers outside the HTTP-client boundary (e.g. the deadline
# evaluator's platform-signed ruling trigger, GAP-A1) can catch transport errors
# from other services without importing httpx directly; only *DbClient modules
# are permitted to do that (tests/architecture/test_db_client_isolation.py).
PlatformHttpError = httpx.HTTPError
PlatformHttpTimeoutError = httpx.TimeoutException

_DEFINITE_ASSET_INSERT_REJECTION_STATUSES = frozenset({400, 401, 403, 404, 409, 410, 413, 415, 422})

_LIFECYCLE_EVENTS_BY_STATUS: dict[str, str] = {
    "accepted": "task.accepted",
    "submitted": "task.submitted",
    "approved": "task.approved",
    "disputed": "task.disputed",
    "ruled": "task.ruled",
    "cancelled": "task.cancelled",
    "expired": "task.expired",
}

_DELIVERY_STATES = frozenset(
    {"all", "pending", "leased", "dead_letter", "completed", "integrity_blocked"}
)
_WORKER_FAILURE_CODES = frozenset(
    {
        "central_bank_unavailable",
        "gateway_unavailable",
        "invalid_gateway_response",
        "request_timeout",
        "unexpected_recovery_error",
    }
)


class TaskDbClient:
    """Task storage backed by the DB Gateway HTTP API."""

    _TASK_COLUMNS: tuple[str, ...] = (
        "task_id",
        "poster_id",
        "title",
        "spec",
        "reward",
        "bidding_deadline_seconds",
        "deadline_seconds",
        "review_deadline_seconds",
        "status",
        "escrow_id",
        "bid_count",
        "worker_id",
        "accepted_bid_id",
        "created_at",
        "accepted_at",
        "submitted_at",
        "approved_at",
        "cancelled_at",
        "disputed_at",
        "dispute_reason",
        "dispute_id",
        "rebuttal_deadline",
        "rebuttal_submitted_at",
        "ruling_id",
        "ruled_at",
        "worker_pct",
        "ruling_summary",
        "expired_at",
        "escrow_pending",
        *TaskContractProjection.model_fields,
    )

    def __init__(
        self,
        base_url: str,
        timeout_seconds: int,
        signer: GatewayRequestSigner,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url,
            timeout=httpx.Timeout(timeout_seconds),
        )
        self._signer = signer

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None,
        params: dict[str, Any] | None,
    ) -> httpx.Response:
        request = self._client.build_request(method, path, json=json, params=params)
        private_publication_read = method == "GET" and (
            path.startswith("/board/task-publications/")
            or path.startswith("/board/posting-recovery")
            or path.startswith("/board/legacy-posting-observations/")
            or path.startswith("/board/task-effect-deliveries")
            or path.startswith("/board/artifact-uploads/")
            or path.startswith("/board/artifact-recovery")
            or path.startswith("/board/artifact-archive/")
            or path.startswith("/board/prepublications")
        )
        action = (
            "gateway.board.write"
            if method != "GET" or private_publication_read
            else "gateway.board.read"
        )
        signed_request = self._signer.sign_request(request, action=action)
        return self._client.send(signed_request)

    def _now(self) -> str:
        return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")

    def _json(self, response: httpx.Response) -> dict[str, Any]:
        try:
            data = response.json()
            if isinstance(data, dict):
                return data
            return {}
        except Exception:
            return {}

    def _build_event(
        self,
        *,
        event_type: str,
        summary: str,
        payload: dict[str, Any],
        task_id: str,
        agent_id: str | None,
    ) -> dict[str, Any]:
        event: dict[str, Any] = {
            "event_source": "board",
            "event_type": event_type,
            "timestamp": self._now(),
            "task_id": task_id,
            "summary": summary,
            "payload": json.dumps(payload),
        }
        if agent_id is not None:
            event["agent_id"] = agent_id
        return event

    def _normalize_task(self, data: dict[str, Any]) -> dict[str, Any]:
        return {column: data.get(column) for column in self._TASK_COLUMNS}

    def _strict_json(self, response: httpx.Response) -> dict[str, Any]:
        try:
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("Expected an object")
        except (ValueError, UnicodeError) as exc:
            raise invalid_gateway_response() from exc
        return data

    def _raise_gateway_error(self, response: httpx.Response) -> None:
        if not 400 <= response.status_code <= 599:
            raise invalid_gateway_response()
        try:
            error = GatewayError.model_validate(self._strict_json(response))
        except ValidationError as exc:
            raise invalid_gateway_response() from exc
        raise ServiceError(error.error, error.message, response.status_code, error.details)

    def _parse_publication_submit(
        self,
        response: httpx.Response,
        intent: TaskPostingIntent,
    ) -> TaskPostingResponse:
        try:
            return parse_task_posting_submit_response(response, intent)
        except httpx.HTTPStatusError:
            self._raise_gateway_error(response)
            raise AssertionError("unreachable") from None
        except (RuntimeError, TypeError, ValueError) as exc:
            raise invalid_gateway_response() from exc

    def _parse_publication_status(
        self,
        response: httpx.Response,
        operation_id: str,
    ) -> TaskPostingResponse:
        if response.status_code != 200:
            self._raise_gateway_error(response)
        try:
            return parse_task_posting_status_response(response, operation_id)
        except (RuntimeError, TypeError, ValueError) as exc:
            raise invalid_gateway_response() from exc

    def admit_task_publication(self, intent: TaskPostingIntent) -> TaskPostingResponse:
        """Durably reserve one exact posting intent before any Bank transport."""
        response = self._request(
            "POST",
            "/board/task-publications",
            json=intent.as_json(),
            params=None,
        )
        return self._parse_publication_submit(response, intent)

    def reconcile_task_publication(
        self,
        operation_id: str,
        intent: TaskPostingIntent | None,
    ) -> TaskPostingResponse:
        """Reconcile persisted financial evidence without accepting caller evidence."""
        validated_id = validate_operation_id(operation_id)
        response = self._request(
            "POST",
            f"/board/task-publications/{validated_id}/reconcile",
            json={},
            params=None,
        )
        if intent is not None:
            return self._parse_publication_submit(response, intent)
        if response.status_code in {201, 202}:
            body = self._strict_json(response)
            status_response = httpx.Response(200, json=body, request=response.request)
            parsed = self._parse_publication_status(status_response, validated_id)
            expected_state = "published" if response.status_code == 201 else "awaiting_lock"
            if parsed.body["publication_state"] != expected_state:
                raise invalid_gateway_response()
            return TaskPostingResponse(response.status_code, parsed.body)
        if response.status_code == 409:
            body = self._strict_json(response)
            details = body.get("details")
            if (
                set(body) != {"error", "message", "details"}
                or body.get("error") != "publication_integrity_blocked"
                or body.get("message") != "Publication evidence requires reconciliation"
                or not isinstance(details, dict)
                or set(details) != {"publication"}
            ):
                raise invalid_gateway_response()
            status_response = httpx.Response(
                200,
                json=details["publication"],
                request=response.request,
            )
            parsed = self._parse_publication_status(status_response, validated_id)
            if parsed.body["publication_state"] != "blocked_integrity":
                raise invalid_gateway_response()
            return TaskPostingResponse(response.status_code, body)
        if response.status_code not in {201, 202, 409}:
            self._raise_gateway_error(response)
        raise AssertionError("unreachable")

    def _get_task_publication(
        self,
        operation_id: str,
        *,
        poster_id: str | None,
    ) -> TaskPostingResponse:
        validated_id = validate_operation_id(operation_id)
        params = None if poster_id is None else {"poster_id": poster_id}
        response = self._request(
            "GET",
            f"/board/task-publications/{validated_id}",
            json=None,
            params=params,
        )
        return self._parse_publication_status(response, validated_id)

    def get_task_publication(
        self,
        operation_id: str,
        poster_id: str,
    ) -> TaskPostingResponse:
        """Read one publication through the required economic-owner filter."""
        return self._get_task_publication(operation_id, poster_id=poster_id)

    def list_posting_recovery(self, after: str, limit: int) -> dict[str, Any]:
        """Return one strict bounded page of this Board writer's pending recovery work."""
        validated_after, validated_limit = validate_recovery_request(after, limit)
        response = self._request(
            "GET",
            "/board/posting-recovery",
            json=None,
            params={"after": validated_after, "limit": validated_limit},
        )
        if response.status_code != 200:
            self._raise_gateway_error(response)
        return validate_recovery_page(
            self._strict_json(response),
            after=validated_after,
            limit=validated_limit,
        )

    def get_legacy_posting_observation(self, observation_id: str) -> dict[str, Any]:
        """Read persisted legacy refund diagnostics without reconciliation."""
        validated_id = validate_legacy_observation_id(observation_id)
        response = self._request(
            "GET",
            f"/board/legacy-posting-observations/{validated_id}",
            json=None,
            params=None,
        )
        if response.status_code != 200:
            self._raise_gateway_error(response)
        return validate_legacy_posting_envelope(
            self._strict_json(response),
            expected_observation_id=validated_id,
        )

    def reconcile_legacy_posting_observation(self, observation_id: str) -> dict[str, Any]:
        """Reconcile one stored legacy refund intent without accepting fresh terms."""
        validated_id = validate_legacy_observation_id(observation_id)
        response = self._request(
            "POST",
            f"/board/legacy-posting-observations/{validated_id}/reconcile",
            json={},
            params=None,
        )
        if response.status_code == 409:
            body = self._strict_json(response)
            if body.get("error") != "posting_refund_integrity_blocked":
                self._raise_gateway_error(response)
            raise validate_legacy_blocked_wrapper(
                body,
                expected_observation_id=validated_id,
            )
        if response.status_code not in {200, 202}:
            self._raise_gateway_error(response)
        envelope = validate_legacy_posting_envelope(
            self._strict_json(response),
            expected_observation_id=validated_id,
        )
        expected_state = "refunded" if response.status_code == 200 else "refund_pending"
        if envelope["observation_state"] != expected_state:
            raise invalid_gateway_response()
        return envelope

    def claim_task_operation(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Claim or replay one exact native command without replacing its identity."""
        command = validate_command(payload)
        response = self._request(
            "POST", "/board/task-operations", json=command.model_dump(), params=None
        )
        if response.status_code not in {200, 202}:
            self._raise_gateway_error(response)
        return validate_envelope(
            self._strict_json(response),
            status_code=response.status_code,
            operation_id=command.operation_id,
            command=command,
        )

    def finalize_task_operation(self, operation_id: str) -> dict[str, Any]:
        """Ask Gateway to validate its persisted evidence; never forward a success body."""
        validated_id = validate_operation_id(operation_id)
        response = self._request(
            "POST", f"/board/task-operations/{validated_id}/finalize", json={}, params=None
        )
        if response.status_code not in {200, 202}:
            self._raise_gateway_error(response)
        return validate_envelope(
            self._strict_json(response),
            status_code=response.status_code,
            operation_id=validated_id,
            command=None,
        )

    @staticmethod
    def _validate_effect_diagnostic(
        value: object,
        *,
        effect_operation_id: str,
        expected_states: frozenset[str],
    ) -> dict[str, Any]:
        try:
            envelope = DiagnosticEnvelope.model_validate(value)
        except (ValidationError, ValueError, TypeError, UnicodeError) as exc:
            raise invalid_gateway_response() from exc
        diagnostic = envelope.delivery
        if (
            diagnostic.effect_operation_id != effect_operation_id
            or diagnostic.delivery_state not in expected_states
        ):
            raise invalid_gateway_response()
        return envelope.model_dump()

    def lease_task_effect_delivery(self, worker_id: str) -> dict[str, Any]:
        """Lease at most one due delivery for the exact recovery process identity."""
        try:
            validated_worker_id = validate_recovery_worker_id(worker_id)
        except ValueError as exc:
            raise ServiceError("invalid_payload", str(exc), 400, {}) from exc
        response = self._request(
            "POST",
            "/board/task-effect-deliveries/lease",
            json={"protocol_version": 1, "worker_id": validated_worker_id},
            params=None,
        )
        if response.status_code != 200:
            self._raise_gateway_error(response)
        body = self._strict_json(response)
        envelope = parse_lease_envelope(body)
        if envelope.delivery is not None and envelope.delivery.worker_id != validated_worker_id:
            raise invalid_gateway_response()
        return body

    def finalize_task_effect_delivery(self, effect_operation_id: str) -> dict[str, Any]:
        """Adopt exact native receipt evidence through one atomic diagnostic route."""
        validated_id = validate_operation_id(effect_operation_id)
        response = self._request(
            "POST",
            f"/board/task-effect-deliveries/{validated_id}/finalize",
            json={},
            params=None,
        )
        if response.status_code not in {200, 202}:
            self._raise_gateway_error(response)
        expected_states = (
            frozenset({"completed"})
            if response.status_code == 200
            else frozenset({"pending", "leased", "dead_letter"})
        )
        return self._validate_effect_diagnostic(
            self._strict_json(response),
            effect_operation_id=validated_id,
            expected_states=expected_states,
        )

    def finalize_legacy_effect_observation(
        self,
        observation_id: str,
    ) -> RecoveryFinalizeResult:
        """Adopt one supported legacy settlement receipt without a native command."""
        validated_observation_id = validate_legacy_observation_id(observation_id)
        response = self._request(
            "POST",
            f"/board/legacy-effect-observations/{validated_observation_id}/finalize",
            json={},
            params=None,
        )
        if response.status_code not in {200, 202}:
            self._raise_gateway_error(response)
        try:
            envelope = DiagnosticEnvelope.model_validate(self._strict_json(response))
        except (ValidationError, ValueError, TypeError, UnicodeError) as exc:
            raise invalid_gateway_response() from exc
        expected_states = (
            frozenset({"completed"})
            if response.status_code == 200
            else frozenset({"pending", "leased", "dead_letter"})
        )
        if (
            envelope.delivery.source_kind != "legacy_settlement_observation"
            or envelope.delivery.source_id != validated_observation_id
            or envelope.delivery.delivery_state not in expected_states
        ):
            raise invalid_gateway_response()
        return cast(
            "RecoveryFinalizeResult",
            RecoveryFinalizeResponse(
                status_code=response.status_code,
                body=envelope.model_dump(),
            ),
        )

    def fail_task_effect_delivery(
        self,
        effect_operation_id: str,
        lease_generation: int,
        worker_id: str,
        error_code: str,
    ) -> dict[str, Any]:
        """Report one fenced worker failure using the finite public code set."""
        validated_id = validate_operation_id(effect_operation_id)
        if type(lease_generation) is not int or lease_generation <= 0:
            raise ServiceError(
                "invalid_payload", "lease_generation must be a positive integer", 400, {}
            )
        try:
            validated_worker_id = validate_recovery_worker_id(worker_id)
        except ValueError as exc:
            raise ServiceError("invalid_payload", str(exc), 400, {}) from exc
        if error_code not in _WORKER_FAILURE_CODES:
            raise ServiceError("invalid_payload", "error_code is not worker-reportable", 400, {})
        response = self._request(
            "POST",
            f"/board/task-effect-deliveries/{validated_id}/attempts/{lease_generation}/fail",
            json={
                "protocol_version": 1,
                "worker_id": validated_worker_id,
                "error_code": error_code,
            },
            params=None,
        )
        if response.status_code != 200:
            self._raise_gateway_error(response)
        return self._validate_effect_diagnostic(
            self._strict_json(response),
            effect_operation_id=validated_id,
            expected_states=frozenset({"pending", "dead_letter", "completed"}),
        )

    def get_task_effect_delivery(self, effect_operation_id: str) -> dict[str, Any]:
        """Read one strict protected scheduling diagnostic."""
        validated_id = validate_operation_id(effect_operation_id)
        response = self._request(
            "GET",
            f"/board/task-effect-deliveries/{validated_id}",
            json=None,
            params=None,
        )
        if response.status_code != 200:
            self._raise_gateway_error(response)
        return self._validate_effect_diagnostic(
            self._strict_json(response),
            effect_operation_id=validated_id,
            expected_states=frozenset(_DELIVERY_STATES - {"all"}),
        )

    def list_task_effect_deliveries(
        self,
        state: str,
        after: str,
        limit: int,
    ) -> dict[str, Any]:
        """Read one strict, bounded page of protected scheduling diagnostics."""
        if state not in _DELIVERY_STATES:
            raise ServiceError("invalid_payload", "state is invalid", 400, {})
        if after:
            validate_operation_id(after)
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ServiceError("invalid_payload", "limit must be an integer in 1..1000", 400, {})
        response = self._request(
            "GET",
            "/board/task-effect-deliveries",
            json=None,
            params={"state": state, "after": after, "limit": limit},
        )
        if response.status_code != 200:
            self._raise_gateway_error(response)
        body = self._strict_json(response)
        if set(body) != {"protocol_version", "items", "next_after"}:
            raise invalid_gateway_response()
        protocol_version = body.get("protocol_version")
        if (
            type(protocol_version) is not int
            or protocol_version != 1
            or not isinstance(body.get("items"), list)
        ):
            raise invalid_gateway_response()
        items: list[dict[str, Any]] = []
        try:
            for item in body["items"]:
                diagnostic = EffectDeliveryDiagnostic.model_validate(item)
                if state not in {"all", diagnostic.delivery_state}:
                    raise ValueError("Gateway page contains a delivery outside its state filter")
                items.append(diagnostic.model_dump())
            operation_ids = [str(item["effect_operation_id"]) for item in items]
            if operation_ids != sorted(set(operation_ids)):
                raise ValueError("Gateway page is not strictly ordered")
            if after and any(operation_id <= after for operation_id in operation_ids):
                raise ValueError("Gateway page did not honor its exclusive cursor")
            next_after = body["next_after"]
            if next_after is not None:
                next_after = validate_operation_id(next_after)
                if len(items) != limit or next_after != items[-1]["effect_operation_id"]:
                    raise ValueError("Page cursor does not match the final item")
            if len(items) > limit:
                raise ValueError("Gateway page exceeds the requested limit")
        except (ValidationError, ServiceError, ValueError, TypeError, UnicodeError) as exc:
            raise invalid_gateway_response() from exc
        return {"protocol_version": 1, "items": items, "next_after": next_after}

    def retry_task_effect_delivery(self, effect_operation_id: str) -> dict[str, Any]:
        """Requeue one dead letter without changing its immutable financial identity."""
        validated_id = validate_operation_id(effect_operation_id)
        response = self._request(
            "POST",
            f"/board/task-effect-deliveries/{validated_id}/retry",
            json={},
            params=None,
        )
        if response.status_code != 200:
            self._raise_gateway_error(response)
        return self._validate_effect_diagnostic(
            self._strict_json(response),
            effect_operation_id=validated_id,
            expected_states=frozenset({"pending"}),
        )

    def _event_type_for_updates(self, updates: dict[str, Any]) -> str:
        status = updates.get("status")
        if status == "approved" and updates.get("escrow_pending") == 1:
            return "task.auto_approved"
        if isinstance(status, str):
            return _LIFECYCLE_EVENTS_BY_STATUS.get(status, "task.updated")
        return "task.updated"

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        response = self._request("GET", f"/board/tasks/{task_id}", json=None, params=None)
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            self._raise_gateway_error(response)
        data = self._strict_json(response)
        validate_task_projection(data)
        if data.get("task_id") != task_id:
            raise invalid_gateway_response()
        return self._normalize_task(data)

    def update_task(
        self,
        task_id: str,
        updates: dict[str, Any],
        *,
        expected_status: str | None,
    ) -> int:
        constraints: dict[str, Any] | None = None
        if expected_status is not None:
            constraints = {"status": expected_status}

        payload: dict[str, Any] = {
            "updates": updates,
            "constraints": constraints,
            "event": self._build_event(
                event_type=self._event_type_for_updates(updates),
                summary=f"Task updated: {task_id}",
                payload={"updates": updates},
                task_id=task_id,
                agent_id=None,
            ),
        }

        response = self._request(
            "POST", f"/board/tasks/{task_id}/status", json=payload, params=None
        )
        if response.status_code in {404, 409}:
            return 0
        if response.status_code != 200:
            msg = f"Gateway error: {response.status_code} {response.text}"
            raise RuntimeError(msg)
        return 1

    def list_tasks(
        self,
        status: str | None,
        poster_id: str | None,
        worker_id: str | None,
        limit: int | None,
        offset: int | None,
    ) -> list[dict[str, Any]]:
        params: dict[str, str | int | float | bool | None] = {}
        if status is not None:
            params["status"] = status
        if poster_id is not None:
            params["poster_id"] = poster_id
        if worker_id is not None:
            params["worker_id"] = worker_id
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset

        response = self._request("GET", "/board/tasks", json=None, params=params)
        if response.status_code != 200:
            msg = f"Gateway error: {response.status_code} {response.text}"
            raise RuntimeError(msg)
        items = self._strict_json(response).get("tasks")
        if not isinstance(items, list):
            raise invalid_gateway_response()
        tasks: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                raise invalid_gateway_response()
            validate_task_projection(item)
            tasks.append(self._normalize_task(item))
        return tasks

    def count_tasks(self) -> int:
        response = self._request("GET", "/board/tasks/count", json=None, params=None)
        if response.status_code != 200:
            msg = f"Gateway error: {response.status_code} {response.text}"
            raise RuntimeError(msg)
        return int(self._json(response)["count"])

    def count_tasks_by_status(self) -> dict[str, int]:
        response = self._request("GET", "/board/tasks/count-by-status", json=None, params=None)
        if response.status_code != 200:
            msg = f"Gateway error: {response.status_code} {response.text}"
            raise RuntimeError(msg)
        data = self._json(response)
        return {str(key): int(value) for key, value in data.items()}

    def insert_bid(self, bid_data: dict[str, Any]) -> None:
        amount = int(bid_data["amount"])
        payload: dict[str, Any] = {
            "bid_id": bid_data["bid_id"],
            "task_id": bid_data["task_id"],
            "bidder_id": bid_data["bidder_id"],
            "proposal": str(bid_data.get("proposal", amount)),
            "amount": amount,
            "submitted_at": bid_data["submitted_at"],
            "constraints": {"status": "open"},
            "event": self._build_event(
                event_type="bid.submitted",
                summary="Bid submitted",
                payload={},
                task_id=str(bid_data["task_id"]),
                agent_id=str(bid_data["bidder_id"]),
            ),
        }

        response = self._request("POST", "/board/bids", json=payload, params=None)
        if response.status_code == 409:
            raise DuplicateBidError("This agent already bid on this task")
        if response.status_code not in (200, 201):
            msg = f"Gateway error: {response.status_code} {response.text}"
            raise RuntimeError(msg)

    def get_bid(self, bid_id: str, task_id: str) -> dict[str, Any] | None:
        response = self._request(
            "GET", f"/board/bids/{bid_id}", json=None, params={"task_id": task_id}
        )
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            self._raise_gateway_error(response)
        try:
            bid = GatewayBid.model_validate(self._strict_json(response))
        except ValidationError as exc:
            raise invalid_gateway_response() from exc
        if bid.bid_id != bid_id or bid.task_id != task_id:
            raise invalid_gateway_response()
        return bid.model_dump(exclude={"proposal"})

    def get_bids_for_task(self, task_id: str) -> list[dict[str, Any]]:
        response = self._request("GET", f"/board/tasks/{task_id}/bids", json=None, params=None)
        if response.status_code != 200:
            self._raise_gateway_error(response)

        data = self._strict_json(response)
        if set(data) != {"bids"} or not isinstance(data["bids"], list):
            raise invalid_gateway_response()
        bids: list[dict[str, Any]] = []
        for item in data["bids"]:
            try:
                bid = GatewayBid.model_validate(item)
            except ValidationError as exc:
                raise invalid_gateway_response() from exc
            if bid.task_id != task_id:
                raise invalid_gateway_response()
            bids.append(bid.model_dump(exclude={"proposal"}))
        return bids

    def insert_asset(
        self,
        asset_data: dict[str, Any],
        *,
        expected_lifecycle_version: int,
        max_files_per_task: int,
    ) -> None:
        task_id = str(asset_data["task_id"])
        asset_id = str(asset_data["asset_id"])
        filename = str(asset_data["filename"])
        storage_path_value = asset_data.get("storage_path")
        if not isinstance(storage_path_value, str) or not storage_path_value.strip():
            msg = "storage_path must be a non-blank string"
            raise ValueError(msg)
        storage_path = storage_path_value
        payload: dict[str, Any] = {
            "asset_id": asset_id,
            "task_id": task_id,
            "uploader_id": asset_data["uploader_id"],
            "filename": filename,
            "content_type": asset_data["content_type"],
            "size_bytes": asset_data["size_bytes"],
            "storage_path": storage_path,
            "content_hash": asset_data.get("content_hash"),
            "uploaded_at": asset_data["uploaded_at"],
            "admission": {
                "expected_status": "accepted",
                "expected_lifecycle_version": expected_lifecycle_version,
                "max_files_per_task": max_files_per_task,
            },
            "event": self._build_event(
                event_type="asset.uploaded",
                summary=f"Asset uploaded for task {task_id}: {filename}",
                payload={"filename": filename, "size_bytes": asset_data["size_bytes"]},
                task_id=task_id,
                agent_id=str(asset_data["uploader_id"]),
            ),
        }

        response = self._request("POST", "/board/assets", json=payload, params=None)
        if response.status_code in _DEFINITE_ASSET_INSERT_REJECTION_STATUSES:
            try:
                error = GatewayError.model_validate(self._strict_json(response))
            except (ServiceError, ValidationError):
                raise AssetInsertRejectedError(
                    response.status_code,
                    None,
                    f"Gateway definitively rejected asset metadata with HTTP "
                    f"{response.status_code}",
                    {},
                ) from None
            raise AssetInsertRejectedError(
                response.status_code,
                error.error,
                error.message,
                error.details,
            )
        if response.status_code not in (200, 201):
            msg = f"Gateway error: {response.status_code} {response.text}"
            raise RuntimeError(msg)

    def get_asset(self, asset_id: str, task_id: str) -> dict[str, Any] | None:
        response = self._request(
            "GET", f"/board/assets/{asset_id}", json=None, params={"task_id": task_id}
        )
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            msg = f"Gateway error: {response.status_code} {response.text}"
            raise RuntimeError(msg)

        data = self._json(response)
        return {
            "asset_id": str(data["asset_id"]),
            "task_id": str(data["task_id"]),
            "uploader_id": str(data["uploader_id"]),
            "filename": str(data["filename"]),
            "content_type": str(data["content_type"]),
            "size_bytes": int(data["size_bytes"]),
            "storage_path": str(data["storage_path"]),
            "content_hash": (
                str(data["content_hash"]) if data.get("content_hash") is not None else ""
            ),
            "uploaded_at": str(data["uploaded_at"]),
        }

    def get_assets_for_task(self, task_id: str) -> list[dict[str, Any]]:
        response = self._request("GET", f"/board/tasks/{task_id}/assets", json=None, params=None)
        if response.status_code != 200:
            msg = f"Gateway error: {response.status_code} {response.text}"
            raise RuntimeError(msg)

        items = self._json(response).get("assets", [])
        if not isinstance(items, list):
            return []
        return [
            {
                "asset_id": str(item["asset_id"]),
                "task_id": str(item["task_id"]),
                "uploader_id": str(item["uploader_id"]),
                "filename": str(item["filename"]),
                "content_type": str(item["content_type"]),
                "size_bytes": int(item["size_bytes"]),
                "content_hash": str(item["content_hash"])
                if item.get("content_hash") is not None
                else "",
                "uploaded_at": str(item["uploaded_at"]),
            }
            for item in items
            if isinstance(item, dict)
        ]

    def count_assets(self, task_id: str) -> int:
        response = self._request(
            "GET", f"/board/tasks/{task_id}/assets/count", json=None, params=None
        )
        if response.status_code != 200:
            msg = f"Gateway error: {response.status_code} {response.text}"
            raise RuntimeError(msg)
        return int(self._json(response)["count"])

    # ------------------------------------------------------------------
    # V6 artifact uploads, manifests, recovery and archive (ArtifactStore)
    # ------------------------------------------------------------------

    def _artifact_call(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None,
        params: dict[str, Any] | None,
    ) -> dict[str, Any]:
        response = self._request(method, path, json=json, params=params)
        if response.status_code != 200:
            self._raise_gateway_error(response)
        return self._strict_json(response)

    def get_artifact_context(self, task_id: str) -> dict[str, Any]:
        """Return governance, binding and submission-manifest facts for one task."""
        return self._artifact_call(
            "GET", f"/board/tasks/{task_id}/artifact-context", json=None, params=None
        )

    def reserve_artifact_upload(
        self, policy: dict[str, int], request: dict[str, Any]
    ) -> dict[str, Any]:
        """Reserve, replay or take over one upload operation."""
        return self._artifact_call(
            "POST",
            "/board/artifact-uploads/reserve",
            json={"policy": policy, "request": request},
            params=None,
        )

    def finalize_artifact_upload(
        self,
        upload_operation_id: str,
        policy: dict[str, int],
        fence: dict[str, Any],
        *,
        observed_size_bytes: int,
        observed_sha256: str,
    ) -> dict[str, Any]:
        """Mark the exact observed bytes ready under the attempt fence."""
        return self._artifact_call(
            "POST",
            f"/board/artifact-uploads/{upload_operation_id}/finalize",
            json={
                "policy": policy,
                "owner_id": fence["owner_id"],
                "generation": fence["generation"],
                "attempt_nonce": fence["attempt_nonce"],
                "observed_size_bytes": observed_size_bytes,
                "observed_sha256": observed_sha256,
            },
            params=None,
        )

    def orphan_artifact_upload(
        self,
        upload_operation_id: str,
        policy: dict[str, int],
        fence: dict[str, Any],
        *,
        failure_code: str,
    ) -> dict[str, Any]:
        """Durably abandon the current attempt."""
        return self._artifact_call(
            "POST",
            f"/board/artifact-uploads/{upload_operation_id}/orphan",
            json={
                "policy": policy,
                "owner_id": fence["owner_id"],
                "generation": fence["generation"],
                "attempt_nonce": fence["attempt_nonce"],
                "failure_code": failure_code,
            },
            params=None,
        )

    def get_artifact_upload(self, upload_operation_id: str) -> dict[str, Any] | None:
        """Return one upload's durable state, or None when unknown."""
        response = self._request(
            "GET", f"/board/artifact-uploads/{upload_operation_id}", json=None, params=None
        )
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            self._raise_gateway_error(response)
        return self._strict_json(response)

    def list_task_artifacts(self, task_id: str) -> dict[str, Any]:
        """Return ready/archived artifacts and manifests of one task."""
        return self._artifact_call(
            "GET", f"/board/tasks/{task_id}/artifacts", json=None, params=None
        )

    def get_artifact_recovery(self) -> dict[str, Any]:
        """Return reserved-upload fences and every storage key that must be kept."""
        return self._artifact_call("GET", "/board/artifact-recovery", json=None, params=None)

    def list_archive_candidates(self, archive_after_seconds: int, limit: int) -> dict[str, Any]:
        """Return live bytes of tasks terminal for longer than the configured age."""
        return self._artifact_call(
            "GET",
            "/board/artifact-archive/candidates",
            json=None,
            params={"archive_after_seconds": archive_after_seconds, "limit": limit},
        )

    def finalize_archive(self, candidate: dict[str, Any]) -> dict[str, Any]:
        """Point one artifact at its already-written archive copy."""
        return self._artifact_call(
            "POST", "/board/artifact-archive/finalize", json=candidate, params=None
        )

    # ------------------------------------------------------------------
    # Protocol-V2 poster-input publications (lane U, G03)
    # ------------------------------------------------------------------

    def _prepublication_call(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None,
        params: dict[str, Any] | None,
        ok_statuses: frozenset[int],
    ) -> tuple[int, dict[str, Any]]:
        response = self._request(method, path, json=json, params=params)
        if response.status_code not in ok_statuses:
            self._raise_gateway_error(response)
        return response.status_code, self._strict_json(response)

    def admit_prepublication(
        self, policy: dict[str, int], request: dict[str, Any]
    ) -> dict[str, Any]:
        """Reserve a V2 posting identity and its draft input manifest before escrow."""
        return self._prepublication_call(
            "POST",
            "/board/prepublications",
            json={"request": request, "policy": policy},
            params=None,
            ok_statuses=frozenset({202}),
        )[1]

    def get_prepublication(self, publication_operation_id: str, poster_id: str) -> dict[str, Any]:
        """Read one V2 publication owned by ``poster_id``."""
        validated = validate_operation_id(publication_operation_id)
        return self._prepublication_call(
            "GET",
            f"/board/prepublications/{validated}",
            json=None,
            params={"poster_id": poster_id},
            ok_statuses=frozenset({200}),
        )[1]

    def freeze_prepublication_inputs(self, request: dict[str, Any]) -> dict[str, Any]:
        """Freeze the ready input set into the publication's manifest."""
        validated = validate_operation_id(str(request["publication_operation_id"]))
        return self._prepublication_call(
            "POST",
            f"/board/prepublications/{validated}/inputs/freeze",
            json=request,
            params=None,
            ok_statuses=frozenset({200}),
        )[1]

    def authorize_prepublication_publish(self, request: dict[str, Any]) -> dict[str, Any]:
        """Authorize the escrow lock for the frozen publication contract."""
        validated = validate_operation_id(str(request["publication_operation_id"]))
        return self._prepublication_call(
            "POST",
            f"/board/prepublications/{validated}/publish",
            json=request,
            params=None,
            ok_statuses=frozenset({202}),
        )[1]

    def reconcile_prepublication(self, publication_operation_id: str) -> tuple[int, dict[str, Any]]:
        """Publish from persisted lock evidence; 201 published, 202 still awaiting lock."""
        validated = validate_operation_id(publication_operation_id)
        return self._prepublication_call(
            "POST",
            f"/board/prepublications/{validated}/reconcile",
            json={},
            params=None,
            ok_statuses=frozenset({201, 202}),
        )

    def get_task_input_manifest(self, task_id: str) -> dict[str, Any]:
        """Return the task's frozen input manifest (``input_manifest`` null for V1 tasks)."""
        return self._prepublication_call(
            "GET",
            f"/board/tasks/{task_id}/input-manifest",
            json=None,
            params=None,
            ok_statuses=frozenset({200}),
        )[1]

    def close(self) -> None:
        self._client.close()


__all__ = [
    "PlatformHttpError",
    "PlatformHttpTimeoutError",
    "RecoveryFinalizeResponse",
    "TaskDbClient",
]
