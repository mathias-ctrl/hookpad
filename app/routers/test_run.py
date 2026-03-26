"""
HookPad — Router de testes (painel admin)
"""
import asyncio
import base64
import json
from functools import partial

from fastapi import APIRouter, Depends, HTTPException, Request

from core.auth import require_admin
from core.executor import create_execution, run_execution, _script_executor
from db.database import get_conn, row_to_dict

router = APIRouter(prefix="/api/scripts", tags=["test"])


def _get_script(script_id: str) -> dict:
    conn = get_conn()
    row  = conn.execute("SELECT * FROM scripts WHERE id=?", (script_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Script não encontrado")
    return row_to_dict(row)


@router.post("/{script_id}/test", dependencies=[Depends(require_admin)])
async def test_script(script_id: str, request: Request):
    s      = _get_script(script_id)
    params: dict = {}
    body_for_input = None

    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" in content_type or "application/x-www-form-urlencoded" in content_type:
        form = await request.form()
        for k, v in form.items():
            if hasattr(v, "read"):
                data = await v.read()
                params[k] = base64.b64encode(data).decode()
            else:
                params[k] = v
        body_for_input = dict(params)
    else:
        try:
            body = await request.json()
            if isinstance(body, dict):
                params.update(body)
                body_for_input = body
        except Exception:
            pass

    params.update({k: v for k, v in request.query_params.items()})

    code       = s.get("draft_code") or ""  # test sempre usa draft
    timeout_ms = s.get("timeout_ms", 30000)
    client_ip  = request.client.host if request.client else None

    exec_id = create_execution(
        script_id=script_id,
        script_version_id=None,
        trigger_type="test",
        request_method=request.method,
        request_path=str(request.url.path),
        request_url=str(request.url),
        request_headers=dict(request.headers),
        request_body=body_for_input,
        ip=client_ip,
        user_agent=request.headers.get("user-agent"),
        host=request.headers.get("host"),
    )

    loop   = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        _script_executor,
        partial(run_execution, exec_id, script_id, code, params, timeout_ms, False),
    )
    return result
