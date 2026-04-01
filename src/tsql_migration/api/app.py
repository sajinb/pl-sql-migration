"""FastAPI application — entry point for the web server."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from tsql_migration.api.database import init_db
from tsql_migration.api.routes.projects import router as projects_router
from tsql_migration.api.routes.migration import router as migration_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create database tables on startup."""
    await init_db()
    yield


app = FastAPI(
    title="T-SQL Migration Tool",
    description="AI-powered T-SQL to Spring Boot 3 / Java 21 migration",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS — allow React dev server
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://localhost:\d+",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(projects_router)
app.include_router(migration_router)


@app.get("/api/health")
async def health():
    return {"status": "ok"}
