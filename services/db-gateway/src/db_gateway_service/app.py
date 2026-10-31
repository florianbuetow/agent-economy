"""FastAPI application factory."""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.routing import APIRoute
from service_auth.jws import TokenPolicy
from service_auth.signing import load_public_key, public_key_to_b64
from service_commons.telemetry import setup_telemetry

from db_gateway_service.board_authority import load_board_authority
from db_gateway_service.config import get_config_path, get_settings
from db_gateway_service.core.exceptions import register_exception_handlers
from db_gateway_service.core.lifespan import lifespan
from db_gateway_service.core.middleware import (
    GATEWAY_ROUTE_POLICIES,
    GatewayRouteMatcher,
    RequestValidationMiddleware,
    TrustedGatewayCaller,
)
from db_gateway_service.routers import (
    bank,
    board,
    court,
    health,
    identity,
    prepublications,
    reputation,
)


def create_app() -> FastAPI:
    """
    Create and configure FastAPI application.

    Returns:
        Configured FastAPI instance with all routers registered.
    """
    settings = get_settings()
    config_dir = get_config_path().parent
    trusted_callers: dict[str, TrustedGatewayCaller] = {}
    public_key_owners: dict[str, str] = {}
    for kid, caller_config in settings.auth.trusted_callers.items():
        public_key_path = config_dir / caller_config.public_key_path
        if public_key_path.is_absolute():
            public_key_path = public_key_path.resolve()
        public_key = load_public_key(public_key_path)
        fingerprint = public_key_to_b64(public_key)
        existing_owner = public_key_owners.get(fingerprint)
        if existing_owner is not None:
            msg = f"Trusted Gateway callers {existing_owner!r} and {kid!r} share key material"
            raise ValueError(msg)
        public_key_owners[fingerprint] = kid
        trusted_callers[kid] = TrustedGatewayCaller(
            public_key=public_key,
            capabilities=frozenset(caller_config.capabilities),
        )

    board_authority = load_board_authority(
        settings.board_authority,
        config_dir=config_dir,
        trusted_public_key_owners=public_key_owners,
    )

    app = FastAPI(
        title=f"{settings.service.name} Service",
        version=settings.service.version,
        lifespan=lifespan,
    )
    app.state.board_authority = board_authority

    register_exception_handlers(app)
    setup_telemetry(
        settings.telemetry,
        service_name=settings.service.name,
        app=app,
        log_logger=logging.getLogger(settings.service.name),
    )

    app.include_router(health.router, tags=["Operations"])
    app.include_router(identity.router)
    app.include_router(bank.router)
    app.include_router(board.router)
    app.include_router(prepublications.router)
    app.include_router(reputation.router)
    app.include_router(court.router)

    api_routes = [route for route in app.routes if isinstance(route, APIRoute)]
    public_routes = {
        (method, route.path)
        for route in api_routes
        if route.path == "/health"
        for method in route.methods
    }
    if public_routes != {("GET", "/health")}:
        msg = "Gateway must expose exactly GET /health as its public route"
        raise ValueError(msg)
    protected_routes = {
        (method, route.path)
        for route in api_routes
        if route.path != "/health"
        for method in route.methods
    }
    configured_routes = set(GATEWAY_ROUTE_POLICIES)
    if protected_routes != configured_routes:
        missing = sorted(protected_routes - configured_routes)
        stale = sorted(configured_routes - protected_routes)
        msg = f"Gateway route policy inventory mismatch: missing={missing}, stale={stale}"
        raise ValueError(msg)
    route_matchers = tuple(
        GatewayRouteMatcher(route_template=route.path, path_regex=route.path_regex)
        for route in api_routes
    )

    app.add_middleware(
        RequestValidationMiddleware,
        max_body_size=settings.request.max_body_size,
        audience=settings.auth.audience,
        token_policy=TokenPolicy(
            max_lifetime_seconds=settings.auth.max_lifetime_seconds,
            clock_skew_seconds=settings.auth.clock_skew_seconds,
        ),
        trusted_callers=trusted_callers,
        route_matchers=route_matchers,
        route_policies=GATEWAY_ROUTE_POLICIES,
    )

    return app
