"""
HookPad — Settings router
"""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from core.auth import require_admin
from core.config import BASE_URL, ADMIN_TOKEN
from db.database import get_conn

router = APIRouter(prefix="/api", tags=["settings"])


class SettingsUpdate(BaseModel):
    history_days: Optional[int] = None


@router.post("/auth")
async def auth(request: Request):
    body = await request.json()
    token = body.get("token", "")
    if token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Token inválido")
    return {"ok": True}


@router.get("/settings", dependencies=[Depends(require_admin)])
def get_settings():
    conn = get_conn()
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


@router.put("/settings", dependencies=[Depends(require_admin)])
def update_settings(body: SettingsUpdate):
    conn = get_conn()
    if body.history_days is not None:
        conn.execute(
            "INSERT OR REPLACE INTO settings(key,value) VALUES('history_days',?)",
            (str(body.history_days),),
        )
        conn.commit()
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


@router.get("/base-url")
def get_base_url():
    return {"base_url": BASE_URL}
