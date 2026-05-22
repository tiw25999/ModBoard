"""ModBoard FastAPI app.

Phase 1 in-progress — only health endpoint wired so far. See
docs/plans/2026-05-22-modboard-phase1-mvp.md for remaining tasks
(Alembic init, Steam API client, scraper, poller, public + admin
routes, chart page).
"""
from fastapi import FastAPI

app = FastAPI(title="ModBoard")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
