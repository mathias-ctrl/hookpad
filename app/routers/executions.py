"""
HookPad — Router de Execuções
Paginação por cursor, endpoints de preview e payload raw.
"""
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from core.auth import require_admin
from core.config import BASE_URL
from core.executor import (
    get_execution,
    get_execution_payload,
    list_executions_cursor,
)
from db.database import get_conn

router = APIRouter(prefix="/api/executions", tags=["executions"])


# ─── Listagem paginada por cursor ─────────────────────────────────────────────
@router.get("", dependencies=[Depends(require_admin)])
def list_executions(
    script_id: Optional[str] = None,
    status:    Optional[str] = None,
    limit:     int           = 20,
    cursor:    Optional[str] = None,
):
    """
    Lista execuções com campos leves + previews.
    Paginação por cursor: passe `cursor=<next_cursor>` para a próxima página.
    """
    return list_executions_cursor(
        script_id=script_id,
        status=status,
        limit=limit,
        cursor=cursor,
    )


# ─── Detalhe da execução (sem payload pesado) ────────────────────────────────
@router.get("/{exec_id}", dependencies=[Depends(require_admin)])
def get_exec(exec_id: str, request: Request):
    ex = get_execution(exec_id)
    if not ex:
        raise HTTPException(404, "Execução não encontrada")
    # Injeta raw_url helpers
    base = str(request.base_url).rstrip("/")
    ex["input_raw_url"]  = f"{base}/api/executions/{exec_id}/input/raw"
    ex["output_raw_url"] = f"{base}/api/executions/{exec_id}/output/raw"
    return ex


# ─── Input: preview + flags ───────────────────────────────────────────────────
@router.get("/{exec_id}/input", dependencies=[Depends(require_admin)])
def get_input_info(exec_id: str, request: Request):
    ex = get_execution(exec_id)
    if not ex:
        raise HTTPException(404, "Execução não encontrada")
    base = str(request.base_url).rstrip("/")
    return {
        "preview":    ex.get("input_preview"),
        "truncated":  bool(ex.get("input_truncated")),
        "size_bytes": ex.get("input_size_bytes"),
        "raw_url":    f"{base}/api/executions/{exec_id}/input/raw",
    }


# ─── Output: preview + flags ──────────────────────────────────────────────────
@router.get("/{exec_id}/output", dependencies=[Depends(require_admin)])
def get_output_info(exec_id: str, request: Request):
    ex = get_execution(exec_id)
    if not ex:
        raise HTTPException(404, "Execução não encontrada")
    base = str(request.base_url).rstrip("/")
    return {
        "preview":    ex.get("output_preview"),
        "truncated":  bool(ex.get("output_truncated")),
        "size_bytes": ex.get("output_size_bytes"),
        "raw_url":    f"{base}/api/executions/{exec_id}/output/raw",
    }


# ─── Input raw (abre no navegador) ────────────────────────────────────────────
@router.get("/{exec_id}/input/raw", dependencies=[Depends(require_admin)])
def get_input_raw(exec_id: str):
    payload = get_execution_payload(exec_id)
    if not payload:
        raise HTTPException(404, "Payload não encontrado")
    raw = payload.get("input_full") or ""
    return _raw_response(raw)


# ─── Output raw (abre no navegador) ───────────────────────────────────────────
@router.get("/{exec_id}/output/raw", dependencies=[Depends(require_admin)])
def get_output_raw(exec_id: str):
    payload = get_execution_payload(exec_id)
    if not payload:
        raise HTTPException(404, "Payload não encontrado")
    raw = payload.get("output_full") or ""
    return _raw_response(raw)


# ─── Limpar execuções ────────────────────────────────────────────────────────
@router.delete("", dependencies=[Depends(require_admin)])
def clear_executions(script_id: Optional[str] = None):
    """
    Deleta execuções (e payloads em cascata via FK ON DELETE CASCADE).
    """
    conn = get_conn()
    if script_id:
        conn.execute("DELETE FROM executions WHERE script_id=?", (script_id,))
    else:
        conn.execute("DELETE FROM executions")
    conn.commit()
    return {"ok": True}


# ─── Helper ───────────────────────────────────────────────────────────────────
def _raw_response(raw: str):
    """
    Retorna application/json se o conteúdo for JSON válido,
    ou text/plain caso contrário — para abrir diretamente no navegador.
    """
    try:
        parsed = json.loads(raw)
        return JSONResponse(
            content=parsed,
            media_type="application/json",
        )
    except Exception:
        return PlainTextResponse(
            content=raw,
            media_type="text/plain; charset=utf-8",
        )
