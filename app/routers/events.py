"""
HookPad — Router SSE
GET /api/events  →  text/event-stream
"""
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse

from core.config import ADMIN_TOKEN
from core.events import broadcaster

router = APIRouter(prefix="/api", tags=["events"])


@router.get("/events")
async def sse_stream(request: Request):
    """
    Endpoint SSE. Aceita token via query param ou header.
    Mantém conexão aberta e envia eventos enquanto o cliente estiver conectado.
    """
    # Auth — mesma lógica do require_admin, mas sem levantar exceção
    # (StreamingResponse não lida bem com exceções no meio do stream)
    token = (
        request.query_params.get("admin_token")
        or request.headers.get("x-admin-token")
    )
    if not token or token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Token admin inválido")

    async def event_generator():
        async for chunk in broadcaster.subscribe():
            # Para se o cliente desconectou
            if await request.is_disconnected():
                break
            yield chunk

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "X-Accel-Buffering": "no",      # nginx: desativa buffering
            "Connection":       "keep-alive",
        },
    )


@router.get("/events/status", tags=["events"])
async def sse_status(request: Request):
    """Retorna quantos clientes SSE estão conectados."""
    token = (
        request.query_params.get("admin_token")
        or request.headers.get("x-admin-token")
    )
    if not token or token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Token admin inválido")
    return {"connections": broadcaster.connection_count}
