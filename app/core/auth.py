"""
HookPad — Autenticação
"""
from fastapi import HTTPException, Request
from core.config import ADMIN_TOKEN


def require_admin(request: Request):
    token = (
        request.headers.get("x-admin-token")
        or request.headers.get("X-Admin-Token")
        or request.query_params.get("admin_token")
    )
    if not token or token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Token admin inválido")
