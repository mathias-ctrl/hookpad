"""
HookPad — Router de Scripts
CRUD completo + versionamento draft/published + build
"""
import json
import secrets
import shutil
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator

from core.auth import require_admin
from core.builder import create_version, get_build_status
from core.utils import (
    parse_main_signature, slugify, sanitize_slug,
    check_token_valid, expiration_to_datetime, utcnow_iso,
)
from core.config import VENV_DIR, BASE_URL
from db.database import get_conn, row_to_dict, rows_to_list

router = APIRouter(prefix="/api/scripts", tags=["scripts"])

VALID_SCHEDULES = {"5min", "1h", "daily", "weekly"}
VALID_METHODS   = {"GET", "POST", "PUT", "DELETE", "PATCH"}


# ─── Models ──────────────────────────────────────────────────────────────────
class ScriptCreate(BaseModel):
    name: str
    description: str = ""
    code: str = ""
    method: str = "POST"
    enabled: bool = True
    trigger: str = "webhook"
    schedule_interval: Optional[str] = None
    folder_id: Optional[str] = None
    timeout_ms: int = 30000
    max_body_bytes: int = 10 * 1024 * 1024
    runtime_mode: str = "sync"
    timezone: str = "America/Sao_Paulo"
    cron_config: Optional[dict] = None

    @field_validator("method")
    @classmethod
    def val_method(cls, v):
        v = v.upper()
        if v not in VALID_METHODS:
            raise ValueError(f"method deve ser um de: {sorted(VALID_METHODS)}")
        return v

    @field_validator("schedule_interval")
    @classmethod
    def val_schedule(cls, v):
        # Aceita formatos legados e novo formato do cron builder (Ns/Nm/Nh/Nd/NM)
        if v is not None:
            from core.scheduler import interval_to_seconds
            if interval_to_seconds(v) is None:
                raise ValueError(f"schedule_interval inválido: '{v}'")
        return v

    @field_validator("name")
    @classmethod
    def val_name(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("name não pode ser vazio")
        if len(v) > 100:
            raise ValueError("name muito longo (max 100 chars)")
        return v


class ScriptUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    draft_code: Optional[str] = None  # edita rascunho
    method: Optional[str] = None
    enabled: Optional[bool] = None
    trigger: Optional[str] = None
    schedule_interval: Optional[str] = None
    folder_id: Optional[str] = None
    timeout_ms: Optional[int] = None
    max_body_bytes: Optional[int] = None
    runtime_mode: Optional[str] = None
    timezone: Optional[str] = None
    cron_config: Optional[dict] = None

    @field_validator("method")
    @classmethod
    def val_method(cls, v):
        if v is not None:
            v = v.upper()
            if v not in VALID_METHODS:
                raise ValueError(f"method inválido")
        return v

    @field_validator("schedule_interval")
    @classmethod
    def val_schedule(cls, v):
        if v is not None:
            from core.scheduler import interval_to_seconds
            if interval_to_seconds(v) is None:
                raise ValueError(f"schedule_interval inválido: '{v}'")
        return v


class PublishRequest(BaseModel):
    code: Optional[str] = None  # se omitido, publica o draft_code atual


class GenerateTokenRequest(BaseModel):
    expiration: Optional[str] = None


# ─── Helpers ─────────────────────────────────────────────────────────────────
def _enrich(s: dict) -> dict:
    if not s:
        return s
    s["webhook_url"] = f"{BASE_URL}/hook/{s['id']}"
    s["main_params"] = parse_main_signature(s.get("draft_code") or "")
    if s.get("token_expires_at"):
        from core.utils import utcnow
        from datetime import datetime
        exp = datetime.fromisoformat(s["token_expires_at"].replace("Z", "+00:00")).replace(tzinfo=None)
        s["token_expired"] = utcnow() > exp
    else:
        s["token_expired"] = False
    return s


def _get_or_404(script_id: str) -> dict:
    conn = get_conn()
    row = conn.execute("SELECT * FROM scripts WHERE id=?", (script_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Script não encontrado")
    return row_to_dict(row)


# ─── CRUD ────────────────────────────────────────────────────────────────────
@router.get("", dependencies=[Depends(require_admin)])
def list_scripts(folder_id: Optional[str] = None):
    conn = get_conn()
    if folder_id:
        rows = conn.execute(
            "SELECT * FROM scripts WHERE folder_id=? ORDER BY name", (folder_id,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM scripts ORDER BY name").fetchall()
    return [_enrich(r) for r in rows_to_list(rows)]


@router.post("", dependencies=[Depends(require_admin)])
def create_script(body: ScriptCreate):
    conn = get_conn()
    sid = secrets.token_hex(8)
    slug = slugify(body.name)
    token = None  # token gerado manualmente pelo usuário

    # Valida folder_id se fornecido
    if body.folder_id:
        row = conn.execute("SELECT id FROM folders WHERE id=?", (body.folder_id,)).fetchone()
        if not row:
            raise HTTPException(400, "folder_id inválido")

    # Valida colisão método+rota
    route = sanitize_slug(slug)
    existing = conn.execute(
        "SELECT id FROM scripts WHERE method=? AND COALESCE(route,id)=? AND id!=?",
        (body.method.upper(), route, sid),
    ).fetchone()
    if existing:
        route = f"{route}-{sid[:6]}"

    conn.execute(
        """INSERT INTO scripts
           (id, folder_id, name, slug, description, method, route,
            enabled, trigger, schedule_interval, auth_mode,
            token, token_expiration, timeout_ms, max_body_bytes,
            runtime_mode, draft_code, packages_ready)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            sid, body.folder_id, body.name, slug, body.description,
            body.method.upper(), route,
            1 if body.enabled else 0,
            body.trigger, body.schedule_interval,
            "token", None, None,
            body.timeout_ms, body.max_body_bytes,
            body.runtime_mode, body.code, 0,
        ),
    )
    conn.commit()

    s = _get_or_404(sid)
    return _enrich(s)


@router.get("/{script_id}", dependencies=[Depends(require_admin)])
def get_script(script_id: str):
    return _enrich(_get_or_404(script_id))


@router.put("/{script_id}", dependencies=[Depends(require_admin)])
def update_script(script_id: str, body: ScriptUpdate):
    conn = get_conn()
    s = _get_or_404(script_id)

    updates = {}
    data = body.model_dump(exclude_none=True)

    for field, val in data.items():
        if field == "draft_code":
            updates["draft_code"] = val
            updates["packages_ready"] = 0  # build necessário
        elif field == "method":
            updates["method"] = val.upper()
        elif field == "enabled":
            updates["enabled"] = 1 if val else 0
        elif field == "name":
            val = val.strip()
            if not val:
                raise HTTPException(400, "name não pode ser vazio")
            updates["name"] = val
            updates["slug"] = slugify(val)
        elif field == "cron_config":
            updates["cron_config"] = json.dumps(val) if val else None
        else:
            updates[field] = val

    if not updates:
        return _enrich(s)

    updates["updated_at"] = utcnow_iso()
    set_clause = ", ".join(f"{k}=?" for k in updates)
    conn.execute(
        f"UPDATE scripts SET {set_clause} WHERE id=?",
        list(updates.values()) + [script_id],
    )
    conn.commit()
    return _enrich(_get_or_404(script_id))


@router.delete("/{script_id}", dependencies=[Depends(require_admin)])
def delete_script(script_id: str):
    _get_or_404(script_id)
    conn = get_conn()
    conn.execute("DELETE FROM scripts WHERE id=?", (script_id,))
    conn.commit()
    venv_path = VENV_DIR / script_id
    if venv_path.exists():
        shutil.rmtree(venv_path, ignore_errors=True)
    return {"ok": True}


@router.post("/{script_id}/duplicate", dependencies=[Depends(require_admin)])
def duplicate_script(script_id: str):
    s = _get_or_404(script_id)
    conn = get_conn()
    new_id = secrets.token_hex(8)
    new_name = f"{s['name']} (cópia)"
    new_slug = slugify(new_name)
    token = None  # sem token automático

    conn.execute(
        """INSERT INTO scripts
           (id, folder_id, name, slug, description, method, route,
            enabled, trigger, schedule_interval, auth_mode,
            token, token_expiration, timeout_ms, max_body_bytes,
            runtime_mode, draft_code, packages_ready)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            new_id, s.get("folder_id"), new_name, new_slug,
            s.get("description", ""),
            s.get("method", "POST"), new_slug,
            s.get("enabled", 1),
            s.get("trigger", "webhook"), s.get("schedule_interval"),
            "token", None, None,
            s.get("timeout_ms", 30000), s.get("max_body_bytes", 10485760),
            s.get("runtime_mode", "sync"),
            s.get("draft_code") or s.get("code", ""), 0,
        ),
    )
    conn.commit()
    return _enrich(_get_or_404(new_id))


# ─── Draft / Published ───────────────────────────────────────────────────────
@router.post("/{script_id}/publish", dependencies=[Depends(require_admin)])
def publish_script(script_id: str, body: PublishRequest):
    """
    Publica o script: cria nova script_version e triggera build.
    """
    s = _get_or_404(script_id)
    code = body.code if body.code is not None else (s.get("draft_code") or "")
    version = create_version(script_id, code, trigger_build_now=True)
    return {
        "ok": True,
        "version_id": version["id"],
        "version": version["version"],
        "build_status": version["build_status"],
    }


@router.get("/{script_id}/versions", dependencies=[Depends(require_admin)])
def list_versions(script_id: str):
    _get_or_404(script_id)
    conn = get_conn()
    rows = conn.execute(
        """SELECT id, script_id, version, build_status, build_error,
                  detected_imports, created_at
           FROM script_versions WHERE script_id=? ORDER BY version DESC""",
        (script_id,),
    ).fetchall()
    result = []
    for r in rows_to_list(rows):
        if r.get("detected_imports"):
            try:
                r["detected_imports"] = json.loads(r["detected_imports"])
            except Exception:
                pass
        result.append(r)
    return result


@router.post("/{script_id}/versions/{version_id}/restore", dependencies=[Depends(require_admin)])
def restore_version(script_id: str, version_id: str):
    """Restaura o draft_code para o código de uma versão específica."""
    _get_or_404(script_id)
    conn = get_conn()
    row = conn.execute(
        "SELECT code, version_label FROM script_versions WHERE id=? AND script_id=?",
        (version_id, script_id),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Versão não encontrada")
    conn.execute(
        "UPDATE scripts SET draft_code=?, updated_at=? WHERE id=?",
        (row["code"], utcnow_iso(), script_id),
    )
    conn.commit()
    return {"ok": True, "code": row["code"], "version_label": row["version_label"]}


@router.get("/{script_id}/versions/{version_id}/build", dependencies=[Depends(require_admin)])
def get_version_build(script_id: str, version_id: str):
    conn = get_conn()
    row = conn.execute(
        """SELECT id, script_id, version, version_label,
                  build_status, build_error, detected_imports
           FROM script_versions WHERE id=? AND script_id=?""",
        (version_id, script_id),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Versão não encontrada")
    r = row_to_dict(row)
    if r.get("detected_imports"):
        try:
            r["detected_imports"] = json.loads(r["detected_imports"])
        except Exception:
            pass
    return r


# ─── Install manual ──────────────────────────────────────────────────────────
@router.post("/{script_id}/install", dependencies=[Depends(require_admin)])
async def install_deps(script_id: str, request: Request):
    """Instala deps do draft atual — equivale a forçar build."""
    import asyncio
    from functools import partial
    from core.executor import _script_executor

    s = _get_or_404(script_id)
    code = s.get("draft_code") or ""

    from core.builder import create_version
    version = create_version(script_id, code, trigger_build_now=True)
    return {"ok": True, "version_id": version["id"], "build_status": "building"}


# ─── Token ───────────────────────────────────────────────────────────────────
@router.post("/{script_id}/generate-token", dependencies=[Depends(require_admin)])
def generate_token(script_id: str, body: GenerateTokenRequest):
    _get_or_404(script_id)
    conn = get_conn()
    token = secrets.token_urlsafe(32)
    expires_at = expiration_to_datetime(body.expiration)
    conn.execute(
        """UPDATE scripts
           SET token=?, token_expires_at=?, token_expiration=?, updated_at=?
           WHERE id=?""",
        (token, expires_at, body.expiration or "never", utcnow_iso(), script_id),
    )
    conn.commit()
    return {"token": token, "expires_at": expires_at, "expiration": body.expiration or "never"}


@router.post("/{script_id}/revoke-token", dependencies=[Depends(require_admin)])
def revoke_token(script_id: str):
    _get_or_404(script_id)
    conn = get_conn()
    conn.execute(
        "UPDATE scripts SET token=NULL, token_expires_at=NULL, token_expiration=NULL WHERE id=?",
        (script_id,),
    )
    conn.commit()
    return {"ok": True}


# ─── Signature ───────────────────────────────────────────────────────────────
@router.get("/{script_id}/signature", dependencies=[Depends(require_admin)])
def get_signature(script_id: str):
    s = _get_or_404(script_id)
    code = s.get("draft_code") or ""
    return {"params": parse_main_signature(code)}


# ─── Enable/Disable ──────────────────────────────────────────────────────────
@router.post("/{script_id}/enable", dependencies=[Depends(require_admin)])
def enable_script(script_id: str):
    _get_or_404(script_id)
    get_conn().execute(
        "UPDATE scripts SET enabled=1, updated_at=? WHERE id=?",
        (utcnow_iso(), script_id),
    )
    get_conn().commit()
    return {"ok": True}


@router.post("/{script_id}/disable", dependencies=[Depends(require_admin)])
def disable_script(script_id: str):
    _get_or_404(script_id)
    get_conn().execute(
        "UPDATE scripts SET enabled=0, updated_at=? WHERE id=?",
        (utcnow_iso(), script_id),
    )
    get_conn().commit()
    return {"ok": True}


# ─── Move to folder (drag and drop) ─────────────────────────────────────────
@router.post("/{script_id}/move", dependencies=[Depends(require_admin)])
async def move_script(script_id: str, request: Request):
    """Move script para uma pasta (ou remove de pasta com folder_id=null)."""
    _get_or_404(script_id)
    body = await request.json()
    folder_id = body.get("folder_id")
    conn = get_conn()
    if folder_id:
        row = conn.execute("SELECT id FROM folders WHERE id=?", (folder_id,)).fetchone()
        if not row:
            raise HTTPException(400, "folder_id inválido")
    conn.execute(
        "UPDATE scripts SET folder_id=?, updated_at=? WHERE id=?",
        (folder_id, utcnow_iso(), script_id),
    )
    conn.commit()
    return _enrich(_get_or_404(script_id))
