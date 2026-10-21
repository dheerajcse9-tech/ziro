"""PlacementOS API entrypoint."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

import app.models  # noqa: F401  (register all tables)
from app.api import routes_core, routes_ops, routes_outreach
from app.audit.ledger import AuditImmutableError
from app.core.config import get_settings
from app.core.errors import DomainError
from app.db.session import SessionLocal
from app.integrations.registry import mock_admin_token
from app.mock_portal.app import create_mock_portal_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("placementos")

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    # AUTO_MIGRATE=true (used on serverless/container deploys) applies Alembic
    # migrations and, in demo mode, seeds the synthetic dataset if empty.
    if os.getenv("AUTO_MIGRATE", "").lower() in {"1", "true", "yes"}:
        from alembic import command
        from alembic.config import Config

        from app.services.seed import seed

        cfg = Config(os.path.join(os.path.dirname(os.path.dirname(__file__)), "alembic.ini"))
        cfg.set_main_option("script_location", os.path.join(os.path.dirname(os.path.dirname(__file__)), "migrations"))
        command.upgrade(cfg, "head")
        if settings.demo_mode:
            with SessionLocal() as db:
                log.info("seed: %s", seed(db))
    yield


app = FastAPI(
    lifespan=lifespan,
    title="PlacementOS API",
    version="1.0.0",
    description="Agentic placement drive coordinator: deterministic eligibility, consent enforcement, "
                "version-bound human approval, idempotent upload and independent verification.",
)
app.add_middleware(
    CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"], allow_headers=["Content-Type", "Authorization"],
)


@app.exception_handler(DomainError)
async def domain_error(_: Request, exc: DomainError):
    return JSONResponse({"detail": exc.message, "code": exc.code, "details": exc.details}, status_code=exc.status_code)


@app.exception_handler(AuditImmutableError)
async def audit_error(_: Request, exc: AuditImmutableError):
    return JSONResponse({"detail": str(exc), "code": "audit_immutable"}, status_code=409)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    return JSONResponse({"detail": "Request validation failed", "code": "validation_failed",
                         "details": {"errors": exc.errors()}}, status_code=422, )


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse({"detail": "Internal server error", "code": "internal_error"}, status_code=500)


app.include_router(routes_core.router)
app.include_router(routes_ops.router)
app.include_router(routes_outreach.router)
# The LOCAL MOCK employer portal is a separate ASGI app; PlacementOS reaches it only over HTTP.
app.mount("/mock-portal", create_mock_portal_app(SessionLocal, mock_admin_token()))

# Optional: serve the statically exported frontend from the same origin (single-service deploys).
_dist = settings.frontend_dist
if _dist and os.path.isdir(_dist):
    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str):
        base = os.path.realpath(_dist)
        for candidate in (path, f"{path}.html", os.path.join(path, "index.html")):
            full = os.path.realpath(os.path.join(base, candidate))
            if full.startswith(base) and os.path.isfile(full):
                return FileResponse(full)
        # Dynamic routes are exported as a single shell per segment (e.g. /drives/view).
        return FileResponse(os.path.join(base, "404.html") if os.path.isfile(os.path.join(base, "404.html"))
                            else os.path.join(base, "index.html"), status_code=404)
