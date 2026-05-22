"""ModBoard FastAPI app."""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.services.poller import poller_task

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(poller_task())
    yield
    task.cancel()


app = FastAPI(title="ModBoard", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
