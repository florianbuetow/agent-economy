"""Test-only transparent HTTP proxy with durable request/response crash gates."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from service_auth.gateway import verify_gateway_request
from service_auth.jws import (
    TokenPolicy,
    require_context,
    require_signer,
    verify_jws_for_signer,
)
from service_auth.signing import load_public_key
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route


_RECOVERY_WORKER_ID = re.compile(
    r"^recovery-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


def _required_text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field_name} must be a non-blank string")
    return value


def _exact_json_equal(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict) and isinstance(right, dict):
        return set(left) == set(right) and all(
            _exact_json_equal(left[key], right[key]) for key in left
        )
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _exact_json_equal(left_value, right_value)
            for left_value, right_value in zip(left, right, strict=True)
        )
    return left == right


def _reject_duplicate_json(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON field: {key}")
        result[key] = value
    return result


def _reject_nonfinite_json(value: str) -> object:
    raise ValueError(f"Non-finite JSON number: {value}")


def _parse_json_object(body: bytes) -> dict[str, object]:
    try:
        value = json.loads(
            body,
            object_pairs_hook=_reject_duplicate_json,
            parse_constant=_reject_nonfinite_json,
        )
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("Authenticated request body must be strict JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("Authenticated request body must be a JSON object")
    return value


def canonical_headers_sha256(headers: list[tuple[bytes, bytes]]) -> str:
    """Hash ordered raw header pairs without retaining credential-bearing values."""
    normalized: list[list[str]] = []
    for name, value in headers:
        try:
            normalized_name = name.decode("ascii").lower()
            normalized_value = value.decode("latin-1")
        except UnicodeDecodeError as exc:
            raise ValueError(
                "HTTP header names must have an ASCII wire representation"
            ) from exc
        normalized.append([normalized_name, normalized_value])
    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class AuthenticationConfig:
    """Explicit test-only receiver policy for a dynamic signed request."""

    mode: str
    public_key_path: Path
    expected_kid: str
    audience: str
    action: str
    max_lifetime_seconds: int
    clock_skew_seconds: int
    expected_payload: dict[str, object]

    @classmethod
    def from_mapping(cls, value: object) -> AuthenticationConfig:
        expected_fields = {
            "mode",
            "public_key_path",
            "expected_kid",
            "audience",
            "action",
            "max_lifetime_seconds",
            "clock_skew_seconds",
            "expected_payload",
        }
        if not isinstance(value, dict) or set(value) != expected_fields:
            raise ValueError("authentication must contain exactly the required fields")
        mode = value["mode"]
        if mode not in {"bank_token", "gateway_lease", "gateway_request"}:
            raise ValueError("authentication mode is unsupported")
        public_key_value = value["public_key_path"]
        if not isinstance(public_key_value, str) or not public_key_value.strip():
            raise ValueError("authentication public_key_path must be explicit")
        public_key_path = Path(public_key_value)
        expected_kid = _required_text(value["expected_kid"], field_name="expected_kid")
        if public_key_path.suffix != ".pub" or public_key_path.stem != expected_kid:
            raise ValueError(
                "authentication public key must be the expected KID .pub file"
            )
        audience = _required_text(value["audience"], field_name="audience")
        action = _required_text(value["action"], field_name="action")
        max_lifetime_seconds = value["max_lifetime_seconds"]
        clock_skew_seconds = value["clock_skew_seconds"]
        policy = TokenPolicy(
            max_lifetime_seconds=max_lifetime_seconds,
            clock_skew_seconds=clock_skew_seconds,
        )
        payload = value["expected_payload"]
        if not isinstance(payload, dict) or any(
            not isinstance(key, str) for key in payload
        ):
            raise ValueError(
                "authentication expected_payload must be an explicit object"
            )
        if "action" in payload:
            raise ValueError(
                "authentication action must not be duplicated in expected_payload"
            )
        json.dumps(payload, allow_nan=False, sort_keys=True, separators=(",", ":"))
        if mode == "gateway_lease" and not _exact_json_equal(
            payload, {"protocol_version": 1}
        ):
            raise ValueError(
                "gateway_lease expected_payload must be protocol version one"
            )
        return cls(
            mode=mode,
            public_key_path=public_key_path,
            expected_kid=expected_kid,
            audience=audience,
            action=action,
            max_lifetime_seconds=policy.max_lifetime_seconds,
            clock_skew_seconds=policy.clock_skew_seconds,
            expected_payload=dict(payload),
        )

    def authenticate(
        self,
        *,
        method: str,
        path: str,
        query: bytes,
        body: bytes,
        headers: list[tuple[bytes, bytes]],
    ) -> str | None:
        public_key = load_public_key(self.public_key_path)
        policy = TokenPolicy(
            max_lifetime_seconds=self.max_lifetime_seconds,
            clock_skew_seconds=self.clock_skew_seconds,
        )
        if self.mode == "bank_token":
            body_payload = _parse_json_object(body)
            if set(body_payload) != {"token"} or not isinstance(
                body_payload["token"], str
            ):
                raise ValueError(
                    "Authenticated bank body must contain exactly one token"
                )
            token = body_payload["token"]
            verified = verify_jws_for_signer(
                token,
                public_key,
                policy=policy,
                expected_kid=self.expected_kid,
            )
            require_context(
                verified,
                audience=self.audience,
                resource=f"{method} {path}",
                allowed_action=self.action,
            )
            expected = {"action": self.action, **self.expected_payload}
            if not _exact_json_equal(verified.payload, expected):
                raise ValueError(
                    "Authenticated bank payload is not the exact expected payload"
                )
            return None

        authorization_values = [
            value for name, value in headers if name.lower() == b"authorization"
        ]
        if len(authorization_values) != 1:
            raise ValueError(
                "Authenticated gate requires exactly one Authorization header"
            )
        try:
            authorization = authorization_values[0].decode("ascii")
        except UnicodeDecodeError as exc:
            raise ValueError("Authorization header must be ASCII") from exc
        if not authorization.startswith("Bearer ") or not authorization.removeprefix(
            "Bearer "
        ):
            raise ValueError("Authenticated gate requires one Bearer token")
        token = authorization.removeprefix("Bearer ")
        verified = verify_gateway_request(
            token,
            public_key,
            policy=policy,
            audience=self.audience,
            method=method,
            path=path,
            body=body,
            query=query,
            allowed_actions=self.action,
        )
        require_signer(verified, expected_kid=self.expected_kid)
        request_payload = _parse_json_object(body)
        if self.mode == "gateway_request":
            if not _exact_json_equal(request_payload, self.expected_payload):
                raise ValueError(
                    "Gateway body does not match the exact expected payload"
                )
            return None
        if set(request_payload) != {*self.expected_payload, "worker_id"}:
            raise ValueError(
                "Gateway lease body must contain exactly the required fields"
            )
        if not all(
            _exact_json_equal(request_payload[key], expected)
            for key, expected in self.expected_payload.items()
        ):
            raise ValueError("Gateway lease body does not match the expected payload")
        worker_id = request_payload["worker_id"]
        if (
            not isinstance(worker_id, str)
            or _RECOVERY_WORKER_ID.fullmatch(worker_id) is None
        ):
            raise ValueError(
                "Gateway lease worker_id must be a canonical recovery UUID4"
            )
        return worker_id


@dataclass
class FaultGate:
    """One exact test request boundary; contains no production configuration."""

    method: str
    path: str
    request_sha256: str | None
    occurrence: int
    phase: str
    expected_status: int
    expected_response: dict[str, Any]
    artifact_path: Path
    authentication: AuthenticationConfig | None = None
    reached: asyncio.Event = field(default_factory=asyncio.Event, init=False)
    release: asyncio.Event = field(default_factory=asyncio.Event, init=False)
    matching_requests: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        if self.phase not in {"before_forward", "after_response"}:
            raise ValueError("Unknown crash phase")
        if type(self.occurrence) is not int or self.occurrence < 1:
            raise ValueError("Gate occurrence must be a positive integer")
        if self.request_sha256 is None:
            if self.authentication is None:
                raise ValueError("Dynamic gate requires explicit authentication")
        elif len(self.request_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in self.request_sha256
        ):
            raise ValueError("Gate request hash must be canonical SHA-256")
        if not self.path.startswith("/") or self.method != self.method.upper():
            raise ValueError(
                "Gate requires an exact absolute target and uppercase method"
            )
        if (
            type(self.expected_status) is not int
            or not 100 <= self.expected_status <= 599
        ):
            raise ValueError("Gate expected status must be an HTTP status")
        if not isinstance(self.expected_response, dict):
            raise ValueError("Gate expected response must be an explicit object")

    def matches(
        self,
        method: str,
        target: str,
        query: bytes,
        body: bytes,
        headers: list[tuple[bytes, bytes]],
    ) -> tuple[bool, str | None]:
        if method != self.method or target != self.path:
            return False, None
        body_hash = hashlib.sha256(body).hexdigest()
        if self.request_sha256 is not None and body_hash != self.request_sha256:
            return False, None
        worker_id = None
        if self.authentication is not None:
            worker_id = self.authentication.authenticate(
                method=method,
                path=target.split("?", maxsplit=1)[0],
                query=query,
                body=body,
                headers=headers,
            )
        self.matching_requests += 1
        return self.matching_requests == self.occurrence, worker_id

    async def hold(
        self,
        response: httpx.Response | None,
        *,
        request_sha256: str,
        request_headers_sha256: str,
        worker_id: str | None,
    ) -> None:
        if response is not None:
            try:
                value = response.json()
            except ValueError as exc:
                raise AssertionError(
                    "Unconfirmed upstream response: invalid JSON"
                ) from exc
            if (
                response.status_code != self.expected_status
                or not isinstance(value, dict)
                or any(
                    key not in value
                    or json.dumps(value[key], sort_keys=True)
                    != json.dumps(expected, sort_keys=True)
                    for key, expected in self.expected_response.items()
                )
            ):
                raise AssertionError(
                    "Unconfirmed upstream response: identity/status mismatch"
                )
        artifact = {
            "pid": os.getpid(),
            "pgid": os.getpgrp(),
            "sid": os.getsid(0),
            "phase": self.phase,
            "method": self.method,
            "path": self.path,
            "occurrence": self.occurrence,
            "request_sha256": request_sha256,
            "request_headers_sha256": request_headers_sha256,
            "response_headers_sha256": None
            if response is None
            else canonical_headers_sha256(response.headers.raw),
            "worker_id": worker_id,
            "upstream_status": None if response is None else response.status_code,
            "response_sha256": None
            if response is None
            else hashlib.sha256(response.content).hexdigest(),
            "confirmed_fields": None if response is None else self.expected_response,
        }
        _publish_gate(self.artifact_path, artifact)
        self.reached.set()
        await self.release.wait()


def _publish_gate(path: Path, value: dict[str, Any]) -> None:
    """Publish once; never replace evidence from a previous gate."""
    staged = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(staged, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        staged.unlink(missing_ok=True)


def create_proxy(
    client: httpx.AsyncClient, upstream_url: str, gate: FaultGate
) -> Starlette:
    """Forward original signed request bytes; hold before any downstream response."""
    parsed = httpx.URL(upstream_url)
    if parsed.scheme != "http" or parsed.path != "/" or parsed.query or parsed.fragment:
        raise ValueError("Test upstream must be an explicit HTTP origin")

    async def forward(request: Request) -> Response:
        body = await request.body()
        target = request.scope["raw_path"].decode("ascii")
        query = request.scope["query_string"]
        if query:
            target += "?" + query.decode("ascii")
        headers = [
            (name, value)
            for name, value in request.headers.raw
            if name.lower() not in {b"host", b"connection", b"transfer-encoding"}
        ]
        prepared = client.build_request(
            request.method,
            upstream_url.rstrip("/") + target,
            headers=headers,
            content=body,
        )
        request_hash = hashlib.sha256(body).hexdigest()
        request_headers_hash = canonical_headers_sha256(prepared.headers.raw)
        selected, worker_id = gate.matches(
            request.method,
            target,
            query,
            body,
            prepared.headers.raw,
        )
        if selected and gate.phase == "before_forward":
            await gate.hold(
                None,
                request_sha256=request_hash,
                request_headers_sha256=request_headers_hash,
                worker_id=worker_id,
            )
        response = await client.send(prepared)
        if selected and gate.phase == "after_response":
            await gate.hold(
                response,
                request_sha256=request_hash,
                request_headers_sha256=request_headers_hash,
                worker_id=worker_id,
            )
        # httpx has decoded any content encoding; Starlette computes the new length.
        returned = Response(content=response.content, status_code=response.status_code)
        returned.raw_headers.extend(
            (name, value)
            for name, value in response.headers.raw
            if name.lower()
            not in {
                b"connection",
                b"transfer-encoding",
                b"content-length",
                b"content-encoding",
            }
        )
        return returned

    return Starlette(
        routes=[
            Route(
                "/{path:path}",
                forward,
                methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
            )
        ]
    )


async def _serve(arguments: argparse.Namespace) -> None:
    config = json.loads(arguments.config.read_text())
    required_keys = {
        "upstream_url",
        "timeout_seconds",
        "release_poll_seconds",
        "release_path",
        "gate",
    }
    if not isinstance(config, dict) or set(config) not in (
        required_keys,
        required_keys | {"authentication"},
    ):
        raise ValueError(
            "Proxy configuration must contain exactly the explicit required fields"
        )
    for name in ("timeout_seconds", "release_poll_seconds"):
        if (
            type(config[name]) not in (int, float)
            or not math.isfinite(config[name])
            or config[name] <= 0
        ):
            raise ValueError(f"{name} must be positive and finite")
    gate_fields = dict(config["gate"])
    gate_keys = set(gate_fields)
    fixed_gate_keys = {
        "method",
        "path",
        "request_sha256",
        "occurrence",
        "phase",
        "expected_status",
        "expected_response",
        "artifact_path",
    }
    if gate_keys != fixed_gate_keys:
        raise ValueError("Gate configuration must contain exactly the required fields")
    gate_fields["artifact_path"] = Path(gate_fields["artifact_path"])
    if "authentication" in config:
        if gate_fields["request_sha256"] is not None:
            raise ValueError("Authenticated dynamic gate request_sha256 must be null")
        gate_fields["authentication"] = AuthenticationConfig.from_mapping(
            config["authentication"]
        )
    gate = FaultGate(**gate_fields)
    release_path = Path(config["release_path"])
    if release_path.exists():
        raise ValueError("Proxy release path must not contain a stale release signal")

    async def release_when_requested() -> None:
        while not release_path.exists():
            await asyncio.sleep(config["release_poll_seconds"])
        gate.release.set()

    async with httpx.AsyncClient(
        timeout=config["timeout_seconds"], trust_env=False
    ) as client:
        app = create_proxy(client, config["upstream_url"], gate)
        release_task = asyncio.create_task(release_when_requested())
        try:
            server = uvicorn.Server(
                uvicorn.Config(
                    app, fd=arguments.fd, log_level="warning", lifespan="off"
                )
            )
            await server.serve()
        finally:
            release_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await release_task


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--fd", type=int, required=True)
    arguments = parser.parse_args()
    if arguments.fd < 0:
        raise ValueError("An owned inherited listener is required")
    asyncio.run(_serve(arguments))


if __name__ == "__main__":
    main()
