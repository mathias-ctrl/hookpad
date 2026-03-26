"""
HookPad — Webhook Router
Endpoint público de execução de scripts.
Suporta modo sync e async.
"""
import asyncio
import base64
import json
from functools import partial
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from core.config import MAX_BODY_BYTES
from core.executor import create_execution, run_execution, _script_executor
from core.utils import check_token_valid, utcnow
from db.database import get_conn, row_to_dict

router = APIRouter(tags=["webhook"])


def _get_script(script_id: str) -> dict:
    conn = get_conn()
    row  = conn.execute("SELECT * FROM scripts WHERE id=?", (script_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Script não encontrado")
    return row_to_dict(row)


async def _parse_body(request: Request) -> tuple[dict, any]:
    """
    Extrai params (para execução) e body_raw (para logging de input).
    Retorna (params_dict, body_for_input).
    """
    params: dict = {}
    body_for_input = None

    params.update(dict(request.query_params))
    params.pop("token", None)

    for k, v in request.headers.items():
        k_lower = k.lower()
        if k_lower.startswith("x-") and k_lower not in ("x-token", "x-admin-token"):
            params[k_lower.replace("x-", "", 1)] = v

    content_type = request.headers.get("content-type", "")
    body_bytes   = await request.body()

    if len(body_bytes) > MAX_BODY_BYTES:
        raise HTTPException(413, "Payload muito grande")

    if "application/json" in content_type:
        try:
            body_json = json.loads(body_bytes)
            body_for_input = body_json
            if isinstance(body_json, dict):
                params.update(body_json)
        except Exception:
            body_for_input = body_bytes.decode("utf-8", errors="replace")
    elif ("application/x-www-form-urlencoded" in content_type
          or "multipart/form-data" in content_type):
        form = await request.form()
        form_dict = {}
        for k, v in form.items():
            if hasattr(v, "read"):
                data = await v.read()
                enc  = base64.b64encode(data).decode()
                params[k]    = enc
                form_dict[k] = f"<binary:{len(data)}bytes>"
            else:
                params[k]    = v
                form_dict[k] = v
        body_for_input = form_dict
    elif body_bytes:
        enc            = base64.b64encode(body_bytes).decode()
        params["file"] = enc
        body_for_input = f"<binary:{len(body_bytes)}bytes>"

    return params, body_for_input


async def _execute_hook(script_id: str, request: Request):
    s = _get_script(script_id)

    if not s.get("enabled", True):
        raise HTTPException(403, "Script desativado")

    if s.get("trigger", "webhook") != "webhook":
        raise HTTPException(400, "Este script não está configurado como webhook")

    # ── Auth ──────────────────────────────────────────────────────────────────
    provided = request.headers.get("x-token") or request.query_params.get("token")
    if not check_token_valid(s, provided):
        if not s.get("token"):
            raise HTTPException(401, "Nenhum token gerado para este script")
        if s.get("token_expires_at"):
            from datetime import datetime
            exp = datetime.fromisoformat(
                s["token_expires_at"].replace("Z", "+00:00")
            ).replace(tzinfo=None)
            if utcnow() > exp:
                raise HTTPException(401, "Token expirado")
        raise HTTPException(401, "Token inválido")

    params, body_for_input = await _parse_body(request)
    code       = _get_published_code(s)
    client_ip  = request.client.host if request.client else None
    query_dict = {k: v for k, v in request.query_params.items() if k != "token"}

    exec_id = create_execution(
        script_id=script_id,
        script_version_id=s.get("published_version_id"),
        trigger_type="webhook",
        request_method=request.method,
        request_path=str(request.url.path),
        request_url=str(request.url),
        request_headers=dict(request.headers),
        request_query=query_dict if query_dict else None,
        request_body=body_for_input,
        ip=client_ip,
        user_agent=request.headers.get("user-agent"),
        host=request.headers.get("host"),
        forwarded_for=request.headers.get("x-forwarded-for"),
    )

    runtime_mode = s.get("runtime_mode", "sync")
    timeout_ms   = s.get("timeout_ms", 30000)

    # ── Async mode ───────────────────────────────────────────────────────────
    if runtime_mode == "async":
        asyncio.get_event_loop().run_in_executor(
            _script_executor,
            partial(run_execution, exec_id, script_id, code, params, timeout_ms, True),
        )
        return JSONResponse(
            {"execution_id": exec_id, "status": "queued", "async": True},
            status_code=202,
        )

    # ── Sync mode ────────────────────────────────────────────────────────────
    result = await asyncio.get_event_loop().run_in_executor(
        _script_executor,
        partial(run_execution, exec_id, script_id, code, params, timeout_ms, True),
    )

    if result.get("binary_output"):
        raw_bytes = base64.b64decode(result["binary_output"])
        return Response(content=raw_bytes, media_type="application/octet-stream")

    if result["success"] and result.get("result") is not None:
        return JSONResponse(result["result"], status_code=200)

    if result.get("error"):
        err = result["error"]
        msg = err.get("message", "Script error") if isinstance(err, dict) else str(err)
        return JSONResponse({"error": msg, "execution_id": exec_id}, status_code=500)

    http_status = 200 if result.get("success") else 500
    return JSONResponse(
        {"success": result.get("success"), "stdout": result.get("stdout"),
         "execution_id": exec_id},
        status_code=http_status,
    )


def _get_published_code(s: dict) -> str:
    version_id = s.get("published_version_id")
    if version_id:
        conn = get_conn()
        row  = conn.execute(
            "SELECT code FROM script_versions WHERE id=?", (version_id,)
        ).fetchone()
        if row:
            return row["code"]
    return s.get("draft_code") or ""


@router.get("/hook/{script_id}")
async def webhook_get(script_id: str, request: Request):
    return await _execute_hook(script_id, request)

@router.post("/hook/{script_id}")
async def webhook_post(script_id: str, request: Request):
    return await _execute_hook(script_id, request)

@router.put("/hook/{script_id}")
async def webhook_put(script_id: str, request: Request):
    return await _execute_hook(script_id, request)

@router.delete("/hook/{script_id}")
async def webhook_delete(script_id: str, request: Request):
    return await _execute_hook(script_id, request)

@router.patch("/hook/{script_id}")
async def webhook_patch(script_id: str, request: Request):
    return await _execute_hook(script_id, request)
