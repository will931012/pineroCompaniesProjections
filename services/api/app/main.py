import logging
import secrets
import time
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.middleware.sessions import SessionMiddleware

from app.admin.routes import router as admin_router
from app.alerts.routes import router as alerts_router
from app.auth.dependencies import UNSAFE_METHODS
from app.auth.routes import router as auth_router
from app.companies.routes import router as companies_router
from app.core.config import Settings, get_settings
from app.core.errors import error_body, register_error_handlers
from app.core.logging import configure_logging, set_request_id
from app.core.metrics import HTTP_REQUEST_DURATION
from app.events.routes import router as events_router
from app.filings.routes import router as filings_router
from app.fundamentals.routes import router as fundamentals_router
from app.market_data.routes import router as market_data_router
from app.quant.routes import router as quant_router
from app.screener.routes import router as screener_router
from app.system.routes import health_router
from app.system.routes import router as system_router
from app.valuation.routes import router as valuation_router
from app.workspace.routes import router as workspace_router

logger = logging.getLogger("app.request")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Cache-Control": "no-store",
}


def _init_sentry(settings: Settings) -> None:
    if settings.sentry_dsn is None:
        return
    import sentry_sdk

    sentry_sdk.init(
        dsn=settings.sentry_dsn.get_secret_value(),
        environment=settings.app_env,
        send_default_pii=False,
        traces_sample_rate=0.0,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_format)
    _init_sentry(settings)
    if settings.auth_disabled:
        logging.getLogger(__name__).warning(
            "auth_disabled", extra={"detail": "AUTH_DISABLED is set: every visitor is an admin."}
        )

    app = FastAPI(
        title=settings.app_name,
        version="0.2.0",
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
    )
    register_error_handlers(app)

    if settings.oidc_configured:
        # Holds only the short-lived OIDC state/nonce during the redirect round trip.
        secret = settings.auth_secret.get_secret_value() if settings.auth_secret else None
        app.add_middleware(
            SessionMiddleware,
            secret_key=secret or secrets.token_urlsafe(32),
            session_cookie="pinero_oidc_state",
            max_age=600,
            same_site="lax",
            https_only=settings.is_production,
        )
    if settings.cors_origins:
        # Only needed when a browser calls the API directly instead of via the web proxy.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Content-Type", "X-CSRF-Token", "X-Request-ID"],
        )

    allowed_origins = set(settings.allowed_origins)

    @app.middleware("http")
    async def request_context(request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
        incoming = request.headers.get("x-request-id", "")
        request_id = incoming if 8 <= len(incoming) <= 64 and incoming.isascii() else str(uuid4())
        set_request_id(request_id)
        started = time.perf_counter()

        origin = request.headers.get("origin")
        if (
            request.method in UNSAFE_METHODS
            and origin
            and origin.rstrip("/") not in allowed_origins
        ):
            response: Response = JSONResponse(
                status_code=403,
                content=error_body("origin_not_allowed", "This request origin is not allowed."),
            )
        else:
            response = await call_next(request)

        duration = time.perf_counter() - started
        route = request.scope.get("route")
        route_path = getattr(route, "path", "unmatched")
        HTTP_REQUEST_DURATION.labels(request.method, route_path, str(response.status_code)).observe(
            duration
        )
        response.headers["X-Request-ID"] = request_id
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        logger.info(
            "request",
            extra={
                "method": request.method,
                "route": route_path,
                "status": response.status_code,
                "duration_ms": round(duration * 1000, 1),
            },
        )
        return response

    if settings.metrics_enabled:

        @app.get("/metrics", include_in_schema=False)
        def metrics() -> Response:
            return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    app.include_router(health_router)
    for router in (
        auth_router,
        companies_router,
        events_router,
        filings_router,
        alerts_router,
        fundamentals_router,
        market_data_router,
        screener_router,
        valuation_router,
        quant_router,
        workspace_router,
        system_router,
        admin_router,
    ):
        app.include_router(router, prefix=settings.api_prefix)
    return app


app = create_app()
