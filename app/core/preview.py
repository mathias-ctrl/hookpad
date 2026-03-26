"""
HookPad — Utilitário de preview truncado para input/output de execuções.

Gera previews leves para listagem, marcando quando o conteúdo foi truncado.
Payloads completos ficam em execution_payloads, acessíveis sob demanda.
"""
import base64
import json
from typing import Any

INPUT_PREVIEW_LIMIT  = 2000   # chars
OUTPUT_PREVIEW_LIMIT = 2000   # chars


def _to_json_str(value: Any) -> str:
    """Serializa qualquer valor para string JSON compacta."""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return str(value)


def make_preview(value: Any, limit: int = INPUT_PREVIEW_LIMIT) -> dict:
    """
    Serializa `value`, calcula tamanho e gera preview truncado.

    Retorna:
        preview       - string até `limit` chars
        truncated     - bool, True se foi cortado
        size_bytes    - tamanho total do serializado em UTF-8 bytes
    """
    full_str  = _to_json_str(value)
    size_bytes = len(full_str.encode("utf-8"))
    truncated  = len(full_str) > limit
    preview    = full_str[:limit] if truncated else full_str
    return {
        "preview":    preview,
        "truncated":  truncated,
        "size_bytes": size_bytes,
    }


def build_input_full(
    trigger_type: str,
    request_method: str | None,
    request_path: str | None,
    request_url: str | None,
    query: dict | None,
    headers: dict | None,
    body: Any,
    ip: str | None,
    user_agent: str | None,
    host: str | None,
    forwarded_for: str | None,
    request_id: str | None,
    received_at: str | None,
    script_id: str | None,
    script_version_id: str | None,
) -> dict:
    """
    Monta o objeto input_full representando a chamada completa que originou
    a execução — no formato mais próximo possível do request real.
    """
    client: dict = {}
    if ip:             client["ip"]            = ip
    if user_agent:     client["user_agent"]    = user_agent
    if host:           client["host"]          = host
    if forwarded_for:  client["forwarded_for"] = forwarded_for

    req: dict = {}
    if request_method: req["method"]  = request_method
    if request_path:   req["path"]    = request_path
    if request_url:    req["url"]     = request_url
    if query:          req["query"]   = query
    if headers:        req["headers"] = headers
    if body is not None: req["body"] = body
    if client:         req["client"]  = client

    meta: dict = {}
    if request_id:         meta["request_id"]        = request_id
    if received_at:        meta["received_at"]        = received_at
    if script_id:          meta["script_id"]          = script_id
    if script_version_id:  meta["script_version_id"] = script_version_id

    return {
        "trigger_type": trigger_type,
        "request":      req,
        "meta":         meta,
    }


def build_output_full(
    result:         Any,
    response_status: int | None,
    response_headers: dict | None,
    response_body:  Any,
    stdout:         str | None,
    stderr:         str | None,
    error_type:     str | None,
    error_message:  str | None,
    installed_packages: list | None,
    duration_ms:    int | None,
) -> dict:
    """
    Monta o objeto output_full representando a saída completa da execução.
    """
    output: dict = {}

    if result is not None:
        output["result"] = result

    resp: dict = {}
    if response_status  is not None: resp["status"]  = response_status
    if response_headers:             resp["headers"] = response_headers
    if response_body    is not None: resp["body"]    = response_body
    if resp:
        output["response"] = resp

    logs: dict = {}
    if stdout: logs["stdout"] = stdout
    if stderr: logs["stderr"] = stderr
    if logs:
        output["logs"] = logs

    if error_type or error_message:
        output["error"] = {
            "type":    error_type,
            "message": error_message,
        }

    if installed_packages:
        output["installed_packages"] = installed_packages

    if duration_ms is not None:
        output["duration_ms"] = duration_ms

    return output


def encode_cursor(created_at: str, exec_id: str) -> str:
    """Codifica cursor de paginação em base64url."""
    raw = f"{created_at}|{exec_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(cursor: str) -> tuple[str, str]:
    """
    Decodifica cursor de paginação.
    Retorna (created_at, exec_id).
    Lança ValueError se inválido.
    """
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        parts = raw.split("|", 1)
        if len(parts) != 2:
            raise ValueError("formato inválido")
        return parts[0], parts[1]
    except Exception as exc:
        raise ValueError(f"cursor inválido: {exc}") from exc
