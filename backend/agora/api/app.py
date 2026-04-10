"""FastAPI application."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agora.api.chat import router as chat_router
from agora.api.agents import router as agents_router

app = FastAPI(title="Agora", version="0.1.0", description="Multi-perspective AI council")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(chat_router, prefix="/api")
app.include_router(agents_router, prefix="/api")


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}
