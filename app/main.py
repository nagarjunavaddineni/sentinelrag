import os
import random
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app import rag, telemetry

state = {}


@asynccontextmanager
async def lifespan(app):
    state["index"] = rag.Index(rag.load_chunks())
    yield


app = FastAPI(title="SentinelRAG", lifespan=lifespan)
telemetry.setup(app)


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)


@app.middleware("http")
async def chaos(request: Request, call_next):
    # Fault injection for reliability drills: FAILURE_RATE=0.5 fails half of /ask calls
    if request.url.path == "/ask" and random.random() < float(os.getenv("FAILURE_RATE", "0")):
        return JSONResponse({"detail": "injected failure"}, status_code=500)
    return await call_next(request)


@app.get("/healthz")
def healthz():
    return {"status": "ok", "chunks": len(state["index"].chunks) if "index" in state else 0}


@app.post("/ask")
def ask(req: AskRequest, x_api_key: str | None = Header(default=None)):
    expected = os.getenv("APP_API_KEY")
    if expected and x_api_key != expected:
        raise HTTPException(401, "invalid or missing X-API-Key")
    return rag.answer(state["index"], req.question)
