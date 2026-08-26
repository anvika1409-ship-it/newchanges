"""FastAPI application factory.

FastAPI owns the application and control plane: endpoints, validation,
authentication integration, authorization, orchestration, policy enforcement,
persistence services and telemetry (AI_DEVELOPMENT_RULES.md section 4.1).

LangGraph is not involved at this layer. It is used only for stateful multi-step
reasoning workflows and never replaces FastAPI (section 4.3).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.cache.redis_client import NullCache, RedisCache
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import MaxBodySizeMiddleware, RequestIDMiddleware
from app.db.session import Database
from app.integrations.llm.client import build_model_gateway
from app.security.identity import build_identity_adapter

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Start and stop the application's dependencies."""
    settings: Settings = app.state.settings

    database = Database(settings)
    cache = RedisCache(settings) if settings.redis_enabled else NullCache()
    gateway = build_model_gateway(settings)
    identity_adapter = build_identity_adapter(settings)

    await database.connect()
    await cache.connect()

    app.state.database = database
    app.state.cache = cache
    app.state.model_gateway = gateway
    app.state.identity_adapter = identity_adapter

    if settings.model_registry_seed_on_startup:
        await _register_seed_models(database, settings)

    logger.info(
        "application_started",
        extra={
            "app_env": str(settings.app_env),
            "model_gateway_provider": str(settings.model_gateway_provider),
            "auth_mode": str(settings.auth_mode),
            "identity_adapter": type(identity_adapter).__name__,
        },
    )
    try:
        yield
    finally:
        await gateway.close()
        await cache.disconnect()
        await database.disconnect()
        logger.info("application_stopped")


async def _register_seed_models(database: Database, settings: Settings) -> None:
    """Register configured models that are not in the registry yet.

    Insert-only: an existing row is never overwritten, so pricing or quality an
    operator has filled in survives a restart. A seed failure is logged and does
    not prevent startup — the registry stays queryable and correctable.
    """
    from app.repositories.model_repository import ModelRepository
    from app.services.model_registry import ModelRegistryService

    try:
        async with database.session() as session:
            service = ModelRegistryService(ModelRepository(session))
            inserted = await service.register_from_seed(
                Path(settings.model_registry_seed_path)
            )
        logger.info("model_registry_startup_seed", extra={"inserted": inserted})
    except Exception:
        logger.exception("model_registry_startup_seed_failed")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application.

    Accepts an explicit ``Settings`` so tests can construct an isolated app
    without mutating process environment.
    """
    settings = settings or get_settings()
    configure_logging(settings)

    app = FastAPI(
        title="Manufacturing AI Cost Intelligence API",
        version="1.1.0",
        description=(
            "Cost-aware AI runtime, governance and optimization layer for "
            "manufacturing AI workloads."
        ),
        docs_url="/docs" if settings.debug else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.debug else None,
        lifespan=lifespan,
    )
    app.state.settings = settings

    # Middleware is applied bottom-up: RequestID must be outermost so every
    # other layer, including error responses, carries the correlation id.
    app.add_middleware(MaxBodySizeMiddleware, max_bytes=settings.max_request_bytes)
    app.add_middleware(RequestIDMiddleware)

    if settings.cors_allow_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allow_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-Trace-ID"],
            expose_headers=["X-Request-ID", "X-Trace-ID"],
        )

    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.api_v1_prefix)
    return app


# No module-level `app = create_app()`.
#
# Building the application at import time would read configuration as an import
# side effect, so merely importing this module — from a test, a migration or a
# management command — would fail whenever configuration was incomplete. Serve
# it with uvicorn's factory flag instead:
#
#     uvicorn app.main:create_app --factory
