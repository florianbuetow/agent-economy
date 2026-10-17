"""Application lifecycle management."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from service_commons.maintenance_lock import MaintenanceLockMode, hold_maintenance_lock

from db_gateway_service.config import get_settings, resolve_recovery_path
from db_gateway_service.core.state import init_app_state
from db_gateway_service.logging import get_logger, setup_logging
from db_gateway_service.services.db_reader import DbReader
from db_gateway_service.services.db_writer import DbWriter

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Manage application lifecycle."""
    settings = get_settings()
    lock_path = resolve_recovery_path(settings.recovery.maintenance_lock_path)

    with hold_maintenance_lock(
        lock_path,
        mode=MaintenanceLockMode.SHARED,
        blocking=False,
    ):
        setup_logging(settings.logging.level, settings.service.name, settings.logging.directory)
        logger = get_logger(__name__)
        state = init_app_state()
        db_writer: DbWriter | None = None
        try:
            db_path = settings.database.path
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)

            schema_path = Path(settings.database.schema_path)
            schema_sql = schema_path.read_text()

            db_writer = DbWriter(
                db_path=db_path,
                busy_timeout_ms=settings.database.busy_timeout_ms,
                journal_mode=settings.database.journal_mode,
                schema_sql=schema_sql,
            )
            state.db_writer = db_writer
            state.db_reader = DbReader(db=db_writer.connection)

            logger.info(
                "Service starting",
                extra={
                    "service": settings.service.name,
                    "version": settings.service.version,
                    "port": settings.server.port,
                    "database": db_path,
                },
            )

            yield
        finally:
            if db_writer is not None:
                logger.info(
                    "Service shutting down",
                    extra={"uptime_seconds": state.uptime_seconds},
                )
                db_writer.close()
