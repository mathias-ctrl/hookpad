"""
HookPad — Serviço de execuções
Separa metadados leves (executions) de payloads completos (execution_payloads).
Input representa a chamada completa que originou a execução.
Output representa a saída estruturada da execução.
"""
import concurrent.futures
import json
import secrets
from typing import Any, Optional

from core.events import (
    broadcaster,
    EVENT_EXECUTION_CREATED,
    EVENT_EXECUTION_UPDATED,
)
from core.preview import (
    INPUT_PREVIEW_LIMIT, OUTPUT_PREVIEW_LIMIT,
    build_input_full, build_output_full,
    encode_cursor, decode_cursor,
    make_preview,
)
from core.sandbox import execute_script
from core.utils import utcnow_iso
from db.database import get_conn, row_to_dict

# Inicializado pelo main.py
_script_executor: concurrent.futures.ThreadPoolExecutor = None  # type: ignore


# ─── Status ───────────────────────────────────────────────────────────────────
class ExecStatus:
    QUEUED    = "queued"
    RUNNING   = "running"
    SUCCESS   = "success"
    FAILED    = "failed"
    TIMEOUT   = "timeout"
    CANCELLED = "cancelled"


# Campos leves para listagem — sem payload, sem stdout/stderr brutos
_LIST_FIELDS = """
    id, script_id, script_version_id, status, trigger_type,
    request_method, request_path, response_status,
    error_message, error_type,
    started_at, finished_at, duration_ms,
    request_id, ip, user_agent,
    input_preview, output_preview,
    input_truncated, output_truncated,
    input_size_bytes, output_size_bytes,
    created_at
"""


def create_execution(
    script_id: str,
    script_version_id: Optional[str],
    trigger_type: str = "webhook",
    request_method: Optional[str] = None,
    request_path: Optional[str] = None,
    request_url: Optional[str] = None,
    request_headers: Optional[dict] = None,
    request_query: Optional[dict] = None,
    request_body: Any = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    host: Optional[str] = None,
    forwarded_for: Optional[str] = None,
) -> str:
    """
    Cria registro de execução (status=queued), monta input_full e persiste
    payload em execution_payloads. Retorna exec_id.
    """
    exec_id     = secrets.token_hex(8)
    received_at = utcnow_iso()

    input_full = build_input_full(
        trigger_type=trigger_type,
        request_method=request_method,
        request_path=request_path,
        request_url=request_url,
        query=request_query,
        headers=request_headers,
        body=request_body,
        ip=ip,
        user_agent=user_agent,
        host=host,
        forwarded_for=forwarded_for,
        request_id=exec_id,
        received_at=received_at,
        script_id=script_id,
        script_version_id=script_version_id,
    )
    ip_info = make_preview(input_full, limit=INPUT_PREVIEW_LIMIT)

    conn = get_conn()
    conn.execute(
        """INSERT INTO executions
           (id, script_id, script_version_id, status, trigger_type,
            request_method, request_path, ip, user_agent, request_id,
            input_preview, input_truncated, input_size_bytes, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            exec_id, script_id, script_version_id,
            ExecStatus.QUEUED, trigger_type,
            request_method, request_path,
            ip, user_agent, exec_id,
            ip_info["preview"],
            1 if ip_info["truncated"] else 0,
            ip_info["size_bytes"],
            received_at,
        ),
    )
    conn.execute(
        """INSERT OR REPLACE INTO execution_payloads
           (execution_id, input_full, request_headers, request_query, request_body)
           VALUES (?,?,?,?,?)""",
        (
            exec_id,
            json.dumps(input_full, ensure_ascii=False),
            json.dumps(request_headers) if request_headers else None,
            json.dumps(request_query) if request_query else None,
            json.dumps(request_body, ensure_ascii=False, default=str)
            if request_body is not None else None,
        ),
    )
    conn.commit()

    # Notifica clientes SSE
    broadcaster.publish(EVENT_EXECUTION_CREATED, {
        "execution_id": exec_id,
        "script_id":    script_id,
        "trigger_type": trigger_type,
        "status":       ExecStatus.QUEUED,
    })

    return exec_id


def run_execution(
    exec_id: str,
    script_id: str,
    code: str,
    params: dict,
    timeout_ms: Optional[int] = None,
    skip_install: bool = True,
) -> dict:
    """
    Executa o script no sandbox, persiste output_full em execution_payloads,
    atualiza metadados leves em executions.
    """
    conn = get_conn()
    conn.execute(
        "UPDATE executions SET status=?, started_at=? WHERE id=?",
        (ExecStatus.RUNNING, utcnow_iso(), exec_id),
    )
    conn.commit()

    result = execute_script(
        script_id=script_id,
        code=code,
        params=params,
        triggered_by="webhook",
        timeout_ms=timeout_ms,
        skip_install=skip_install,
    )

    raw_status = result.get("status", "failed")
    status = {"success": ExecStatus.SUCCESS, "timeout": ExecStatus.TIMEOUT}.get(
        raw_status, ExecStatus.FAILED
    )
    http_status = 200 if status == ExecStatus.SUCCESS else 500

    # Conteúdo da resposta para output
    response_body = result.get("result")
    if response_body is None and result.get("stdout"):
        response_body = result["stdout"]

    output_full = build_output_full(
        result=result.get("result"),
        response_status=http_status,
        response_headers=None,
        response_body=response_body,
        stdout=result.get("stdout") or None,
        stderr=result.get("stderr") or None,
        error_type=result.get("error_type"),
        error_message=result.get("error_message"),
        installed_packages=result.get("installed_packages") or None,
        duration_ms=result.get("duration_ms"),
    )
    op_info = make_preview(output_full, limit=OUTPUT_PREVIEW_LIMIT)

    # Persiste payload de output
    conn.execute(
        "INSERT OR IGNORE INTO execution_payloads (execution_id) VALUES (?)",
        (exec_id,),
    )
    conn.execute(
        """UPDATE execution_payloads
           SET output_full=?, response_body=?, stdout=?, stderr=?
           WHERE execution_id=?""",
        (
            json.dumps(output_full, ensure_ascii=False, default=str),
            json.dumps(response_body, ensure_ascii=False, default=str)
            if response_body is not None else None,
            (result.get("stdout") or "")[:200_000] or None,
            (result.get("stderr") or "")[:50_000] or None,
            exec_id,
        ),
    )

    # Atualiza metadados leves
    conn.execute(
        """UPDATE executions SET
               status=?, response_status=?,
               error_message=?, error_type=?,
               output_preview=?, output_truncated=?, output_size_bytes=?,
               finished_at=?, duration_ms=?
           WHERE id=?""",
        (
            status, http_status,
            result.get("error_message"),
            result.get("error_type"),
            op_info["preview"],
            1 if op_info["truncated"] else 0,
            op_info["size_bytes"],
            result.get("finished_at"),
            result.get("duration_ms"),
            exec_id,
        ),
    )
    conn.commit()

    # Notifica clientes SSE
    broadcaster.publish(EVENT_EXECUTION_UPDATED, {
        "execution_id": exec_id,
        "script_id":    script_id,
        "status":       status,
        "duration_ms":  result.get("duration_ms"),
        "error_message": result.get("error_message"),
    })

    result["execution_id"] = exec_id
    result["status"]       = status
    return result


def get_execution(exec_id: str) -> Optional[dict]:
    """Metadados + previews sem payload."""
    conn = get_conn()
    row  = conn.execute(
        f"SELECT {_LIST_FIELDS} FROM executions WHERE id=?", (exec_id,)
    ).fetchone()
    return row_to_dict(row)


def get_execution_payload(exec_id: str) -> Optional[dict]:
    """Payload completo (input_full + output_full + raw fields)."""
    conn = get_conn()
    row  = conn.execute(
        "SELECT * FROM execution_payloads WHERE execution_id=?", (exec_id,)
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    for field in ("request_headers", "request_query"):
        if d.get(field) and isinstance(d[field], str):
            try:
                d[field] = json.loads(d[field])
            except Exception:
                pass
    return d


def list_executions_cursor(
    script_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 20,
    cursor: Optional[str] = None,
) -> dict:
    """
    Paginação por cursor (created_at DESC, id DESC).
    Retorna {items, next_cursor, has_more}.
    """
    limit = min(max(1, limit), 100)
    conn  = get_conn()

    clauses: list[str] = []
    vals: list = []

    if script_id:
        clauses.append("script_id=?")
        vals.append(script_id)
    if status:
        clauses.append("status=?")
        vals.append(status)
    if cursor:
        try:
            cur_ts, cur_id = decode_cursor(cursor)
            clauses.append("(created_at < ? OR (created_at = ? AND id < ?))")
            vals.extend([cur_ts, cur_ts, cur_id])
        except ValueError:
            pass  # cursor inválido → começa do topo

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    rows = conn.execute(
        f"SELECT {_LIST_FIELDS} FROM executions {where} ORDER BY created_at DESC, id DESC LIMIT ?",
        vals + [limit + 1],
    ).fetchall()

    items    = [row_to_dict(r) for r in rows[:limit]]
    has_more = len(rows) > limit
    next_cursor = encode_cursor(items[-1]["created_at"], items[-1]["id"]) if has_more and items else None

    return {"items": items, "next_cursor": next_cursor, "has_more": has_more}


# compat shim — usado pelo scheduler e test_run que ainda chamam a assinatura antiga
def list_executions(
    script_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    status: Optional[str] = None,
) -> list[dict]:
    """Legacy offset-based list — use list_executions_cursor para novos endpoints."""
    conn = get_conn()
    clauses: list[str] = []
    vals: list = []
    if script_id:
        clauses.append("script_id=?")
        vals.append(script_id)
    if status:
        clauses.append("status=?")
        vals.append(status)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        f"SELECT {_LIST_FIELDS} FROM executions {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        vals + [limit, offset],
    ).fetchall()
    return [row_to_dict(r) for r in rows]
