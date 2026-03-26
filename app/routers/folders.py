"""
HookPad — Router de Pastas (Folders)
Organização lógica dos scripts em namespaces.
"""
import secrets
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from core.auth import require_admin
from core.utils import slugify
from db.database import get_conn, row_to_dict, rows_to_list

router = APIRouter(prefix="/api/folders", tags=["folders"])


class FolderCreate(BaseModel):
    name: str
    parent_id: Optional[str] = None

    @field_validator("name")
    @classmethod
    def val_name(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("name não pode ser vazio")
        if len(v) > 80:
            raise ValueError("name muito longo")
        return v


class FolderUpdate(BaseModel):
    name: Optional[str] = None
    parent_id: Optional[str] = None


@router.get("", dependencies=[Depends(require_admin)])
def list_folders():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM folders ORDER BY name").fetchall()
    folders = rows_to_list(rows)
    # Conta scripts por pasta
    for f in folders:
        cnt = conn.execute(
            "SELECT COUNT(*) as c FROM scripts WHERE folder_id=?", (f["id"],)
        ).fetchone()
        f["script_count"] = cnt["c"] if cnt else 0
    return folders


@router.post("", dependencies=[Depends(require_admin)])
def create_folder(body: FolderCreate):
    conn = get_conn()
    fid = secrets.token_hex(8)
    slug = slugify(body.name)

    # Garante unicidade do slug
    existing = conn.execute("SELECT id FROM folders WHERE slug=?", (slug,)).fetchone()
    if existing:
        slug = f"{slug}-{fid[:6]}"

    # Valida parent_id
    if body.parent_id:
        p = conn.execute("SELECT id FROM folders WHERE id=?", (body.parent_id,)).fetchone()
        if not p:
            raise HTTPException(400, "parent_id inválido")

    conn.execute(
        "INSERT INTO folders (id, name, slug, parent_id) VALUES (?,?,?,?)",
        (fid, body.name, slug, body.parent_id),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM folders WHERE id=?", (fid,)).fetchone()
    return row_to_dict(row)


@router.put("/{folder_id}", dependencies=[Depends(require_admin)])
def update_folder(folder_id: str, body: FolderUpdate):
    conn = get_conn()
    row = conn.execute("SELECT * FROM folders WHERE id=?", (folder_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Pasta não encontrada")

    updates = {}
    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise HTTPException(400, "name não pode ser vazio")
        updates["name"] = name
        updates["slug"] = slugify(name)
    if body.parent_id is not None:
        if body.parent_id == folder_id:
            raise HTTPException(400, "Uma pasta não pode ser pai dela mesma")
        updates["parent_id"] = body.parent_id

    if updates:
        set_clause = ", ".join(f"{k}=?" for k in updates)
        conn.execute(
            f"UPDATE folders SET {set_clause} WHERE id=?",
            list(updates.values()) + [folder_id],
        )
        conn.commit()

    row = conn.execute("SELECT * FROM folders WHERE id=?", (folder_id,)).fetchone()
    return row_to_dict(row)


@router.delete("/{folder_id}", dependencies=[Depends(require_admin)])
def delete_folder(folder_id: str):
    conn = get_conn()
    row = conn.execute("SELECT id FROM folders WHERE id=?", (folder_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Pasta não encontrada")
    # Desvincula scripts antes de deletar
    conn.execute(
        "UPDATE scripts SET folder_id=NULL WHERE folder_id=?", (folder_id,)
    )
    conn.execute("DELETE FROM folders WHERE id=?", (folder_id,))
    conn.commit()
    return {"ok": True}


@router.get("/{folder_id}/scripts", dependencies=[Depends(require_admin)])
def list_folder_scripts(folder_id: str):
    conn = get_conn()
    row = conn.execute("SELECT id FROM folders WHERE id=?", (folder_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Pasta não encontrada")
    rows = conn.execute(
        "SELECT * FROM scripts WHERE folder_id=? ORDER BY name", (folder_id,)
    ).fetchall()
    return rows_to_list(rows)
