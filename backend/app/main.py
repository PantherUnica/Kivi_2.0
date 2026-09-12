"""FastAPI entrypoint."""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config import settings

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(levelname)-5s %(name)s: %(message)s",
)

app = FastAPI(
    title="Kivi - semantic memory",
    version="1.0.0",
    description=(
        "Kivi remembers what you said and how you say it, not who you are. "
        "Hybrid RAG over a person's own dictation history, with typed, "
        "evidence-backed memory."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")


@app.get("/")
def root() -> dict:
    return {
        "name": "kivi",
        "docs": "/docs",
        "health": "/api/health",
        "principle": (
            "Kivi should remember enough to make you repeat yourself less, but "
            "not so much that you feel observed or lose control."
        ),
    }
