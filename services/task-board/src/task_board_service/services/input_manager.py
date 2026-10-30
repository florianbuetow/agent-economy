"""Poster input attachments: posting intent -> input upload -> frozen manifest -> publish.

Completion plan 2.4 (G03). The poster signs three Task Board tokens:

1. ``task_publication_admit`` on ``POST /task-publications`` admits a protocol-V2
   publication and its draft input manifest before any escrow lock.
2. ``task_publication_upload_input`` on ``POST /task-publications/{id}/inputs``
   streams one input file into the draft manifest (repeatable, idempotent).
3. ``task_publication_publish`` on ``POST /task-publications/{id}/publish``, together
   with the ordinary escrow-lock token, freezes the exact input manifest (names,
   sizes, sha256), authorizes publication bound to that manifest, locks escrow and
   reconciles the task into existence.

Every Gateway command identity after admission is derived from the publication
identity, so a retried publish replays instead of duplicating effects.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from service_auth.financial_identity import operation_id_for
from service_commons.exceptions import ServiceError

from task_board_service.task_operation_contract import validate_operation_id

if TYPE_CHECKING:
    from fastapi import Request

    from task_board_service.core.asset_upload import AssetStorageQuota, AssetUploadLimits


class PrepublicationStore(Protocol):
    """Gateway calls the poster-input flow needs (implemented by ``TaskDbClient``)."""

    def admit_prepublication(
        self, policy: dict[str, int], request: dict[str, Any]
    ) -> dict[str, Any]: ...

    def get_prepublication(
        self, publication_operation_id: str, poster_id: str
    ) -> dict[str, Any]: ...

    def freeze_prepublication_inputs(self, request: dict[str, Any]) -> dict[str, Any]: ...

    def authorize_prepublication_publish(self, request: dict[str, Any]) -> dict[str, Any]: ...

    def reconcile_prepublication(
        self, publication_operation_id: str
    ) -> tuple[int, dict[str, Any]]: ...

    def get_task_input_manifest(self, task_id: str) -> dict[str, Any]: ...


class AgentTokenValidator(Protocol):
    """Route-bound agent JWS verification."""

    async def validate_jws_token(
        self,
        token: str,
        expected_action: str | tuple[str, ...],
        *,
        expected_resource: str,
    ) -> dict[str, Any]: ...

    async def validate_escrow_jws_token(self, token: str) -> dict[str, Any]: ...


class EscrowLocker(Protocol):
    """Central Bank escrow lock transport."""

    async def escrow_lock(self, escrow_token: str) -> dict[str, Any]: ...


class InputUploader(Protocol):
    """Governed byte storage for one poster input (implemented by ``AssetManager``)."""

    def artifact_policy(self) -> dict[str, int]: ...

    async def upload_input_artifact(
        self,
        *,
        publication_operation_id: str,
        task_id: str,
        manifest_id: str,
        poster_id: str,
        request: Request,
        limits: AssetUploadLimits,
        quota: AssetStorageQuota,
        idempotency_key: str | None,
    ) -> dict[str, Any]: ...


_DURATION_FIELDS = (
    "bidding_deadline_seconds",
    "acceptance_window_seconds",
    "execution_deadline_seconds",
    "review_deadline_seconds",
)
_ADMIT_FIELDS = frozenset(
    {
        "action",
        "publication_operation_id",
        "task_id",
        "poster_id",
        "title",
        "spec",
        "reward",
        "expected_input_count",
        "_signer_id",
        *_DURATION_FIELDS,
    }
)
_UPLOAD_FIELDS = frozenset({"action", "publication_operation_id", "poster_id", "_signer_id"})
_PUBLISH_FIELDS = frozenset(
    {"action", "publication_operation_id", "task_id", "poster_id", "_signer_id"}
)
_ESCROW_FIELDS = frozenset(
    {"action", "operation_id", "task_id", "agent_id", "amount", "_signer_id"}
)


def _invalid(message: str) -> ServiceError:
    return ServiceError("invalid_payload", message, 400, {})


def _exact(payload: dict[str, Any], fields: frozenset[str]) -> None:
    if frozenset(payload) != fields:
        raise _invalid("Token must contain exactly the required fields")


def _poster(payload: dict[str, Any]) -> str:
    poster_id = payload["poster_id"]
    if not isinstance(poster_id, str) or payload["_signer_id"] != poster_id:
        raise ServiceError("forbidden", "Signer does not match poster_id", 403, {})
    return poster_id


def _path_match(payload: dict[str, Any], publication_operation_id: str) -> None:
    if payload["publication_operation_id"] != publication_operation_id:
        raise _invalid("publication_operation_id does not match the URL path")


def lock_operation_id(task_id: str, poster_id: str) -> str:
    """The escrow lock identity the poster's escrow token must carry."""
    return operation_id_for("task-lock", (task_id, poster_id))


class PosterInputCoordinator:
    """Drive one poster's input-attached publication through the Gateway."""

    def __init__(
        self,
        *,
        store: PrepublicationStore,
        token_validator: AgentTokenValidator,
        bank: EscrowLocker,
        uploader: InputUploader,
    ) -> None:
        self._store = store
        self._tokens = token_validator
        self._bank = bank
        self._uploader = uploader

    async def admit(self, token: str) -> dict[str, Any]:
        """Admit the signed posting intent; returns the draft input manifest id."""
        payload = await self._tokens.validate_jws_token(
            token, "task_publication_admit", expected_resource="POST /task-publications"
        )
        _exact(payload, _ADMIT_FIELDS)
        poster_id = _poster(payload)
        publication_id = validate_operation_id(payload["publication_operation_id"])
        task_id = payload["task_id"]
        if not isinstance(task_id, str):
            raise _invalid("task_id must be a string")
        request: dict[str, Any] = {
            "protocol_version": 2,
            "publication_operation_id": publication_id,
            "task_id": task_id,
            "actor_kind": "agent",
            "actor_id": poster_id,
            "action": "task_publication_admit",
            "resource": "POST /task-publications",
            "lock_operation_id": lock_operation_id(task_id, poster_id),
            "title": payload["title"],
            "spec": payload["spec"],
            "reward": payload["reward"],
            "expected_input_count": payload["expected_input_count"],
        }
        for field in _DURATION_FIELDS:
            request[field] = payload[field]
        return self._store.admit_prepublication(self._uploader.artifact_policy(), request)

    async def upload_input(
        self,
        publication_operation_id: str,
        *,
        token: str,
        request: Request,
        limits: AssetUploadLimits,
        quota: AssetStorageQuota,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        """Stream one input file into the poster's draft input manifest."""
        publication_id = validate_operation_id(publication_operation_id)
        payload = await self._tokens.validate_jws_token(
            token,
            "task_publication_upload_input",
            expected_resource=f"POST /task-publications/{publication_id}/inputs",
        )
        _exact(payload, _UPLOAD_FIELDS)
        _path_match(payload, publication_id)
        poster_id = _poster(payload)
        publication = self._store.get_prepublication(publication_id, poster_id)
        if publication["publication_state"] != "awaiting_inputs":
            raise ServiceError("invalid_status", "Publication is not accepting inputs", 409, {})
        return await self._uploader.upload_input_artifact(
            publication_operation_id=publication_id,
            task_id=str(publication["task_id"]),
            manifest_id=str(publication["input_manifest_id"]),
            poster_id=poster_id,
            request=request,
            limits=limits,
            quota=quota,
            idempotency_key=idempotency_key,
        )

    async def publish(
        self,
        publication_operation_id: str,
        *,
        task_token: str,
        escrow_token: str,
    ) -> tuple[int, dict[str, Any]]:
        """Freeze inputs, authorize, lock escrow and reconcile; 201 once published."""
        publication_id = validate_operation_id(publication_operation_id)
        payload = await self._tokens.validate_jws_token(
            task_token,
            "task_publication_publish",
            expected_resource=f"POST /task-publications/{publication_id}/publish",
        )
        _exact(payload, _PUBLISH_FIELDS)
        _path_match(payload, publication_id)
        poster_id = _poster(payload)
        task_id = payload["task_id"]
        escrow = await self._tokens.validate_escrow_jws_token(escrow_token)
        publication = self._store.get_prepublication(publication_id, poster_id)
        if publication["task_id"] != task_id:
            raise _invalid("task_id does not match the publication")
        self._validate_escrow(escrow, publication, poster_id)
        state = publication["publication_state"]
        if state == "awaiting_inputs":
            publication = self._store.freeze_prepublication_inputs(
                {
                    "protocol_version": 2,
                    "freeze_operation_id": operation_id_for("task-input-freeze", (publication_id,)),
                    "publication_operation_id": publication_id,
                    "task_id": task_id,
                    "actor_id": poster_id,
                    "action": "task_publication_inputs_freeze",
                    "resource": f"POST /task-publications/{publication_id}/inputs/freeze",
                    "input_manifest_id": publication["input_manifest_id"],
                }
            )
            state = publication["publication_state"]
        if state == "awaiting_publish":
            publication = self._store.authorize_prepublication_publish(
                {
                    "protocol_version": 2,
                    "publish_operation_id": operation_id_for("task-publish", (publication_id,)),
                    "publication_operation_id": publication_id,
                    "task_id": task_id,
                    "actor_id": poster_id,
                    "action": "task_publication_publish",
                    "resource": "POST /tasks",
                    "lock_operation_id": lock_operation_id(task_id, poster_id),
                    "publication_contract_sha256": publication["publication_contract_sha256"],
                }
            )
            state = publication["publication_state"]
        if state == "published":
            return 201, publication
        if state != "awaiting_lock":
            raise ServiceError(
                "publication_integrity_blocked",
                "Publication evidence requires reconciliation",
                409,
                {"publication": publication},
            )
        status, reconciled = self._store.reconcile_prepublication(publication_id)
        if status == 201:
            return status, reconciled
        try:
            await self._bank.escrow_lock(escrow_token)
        except ServiceError as bank_error:
            status, reconciled = self._store.reconcile_prepublication(publication_id)
            if status == 201 or bank_error.status_code >= 500:
                return status, reconciled
            raise
        return self._store.reconcile_prepublication(publication_id)

    @staticmethod
    def _validate_escrow(
        escrow: dict[str, Any], publication: dict[str, Any], poster_id: str
    ) -> None:
        if frozenset(escrow) != _ESCROW_FIELDS:
            raise _invalid("Escrow token must contain exactly the required fields")
        if escrow["_signer_id"] != poster_id:
            raise ServiceError("token_mismatch", "Escrow signer does not match poster", 400, {})
        task_id = str(publication["task_id"])
        expected = {
            "action": "escrow_lock",
            "operation_id": lock_operation_id(task_id, poster_id),
            "task_id": task_id,
            "agent_id": poster_id,
            "amount": publication["reward"],
        }
        if any(
            type(escrow[name]) is not type(value) or escrow[name] != value
            for name, value in expected.items()
        ):
            raise ServiceError(
                "token_mismatch", "Escrow token does not match the publication", 400, {}
            )


def public_input_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Drop storage keys; expose names, sizes, hashes and downloadable asset ids."""
    return {
        "manifest_id": manifest["manifest_id"],
        "state": manifest["state"],
        "manifest_sha256": manifest["manifest_sha256"],
        "publication_contract_sha256": manifest["publication_contract_sha256"],
        "accepted_publication_contract_sha256": manifest["accepted_publication_contract_sha256"],
        "files": [
            {
                "asset_id": f"asset-{item['artifact_id']}",
                "filename": item["filename"],
                "size_bytes": item["size_bytes"],
                "content_sha256": item["content_sha256"],
                "content_type": item["content_type"],
                "role": "input",
            }
            for item in manifest["files"]
        ],
    }


__all__ = ["PosterInputCoordinator", "lock_operation_id", "public_input_manifest"]
