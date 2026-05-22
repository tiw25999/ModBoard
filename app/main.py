"""ModBoard FastAPI app."""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.routes import admin as admin_routes
from app.routes import forum as forum_routes
from app.routes import public as public_routes
from app.services.poller import poller_task

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(poller_task())
    yield
    task.cancel()


app = FastAPI(title="ModBoard", lifespan=lifespan)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(public_routes.router)
app.include_router(forum_routes.router)
app.include_router(admin_routes.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
