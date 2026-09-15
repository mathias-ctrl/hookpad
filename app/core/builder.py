"""
HookPad — Sistema de build separado da execução
Quando o script é publicado:
  1. Detecta imports
  2. Instala dependências no venv
  3. Marca build_status como ready ou failed
A execução nunca instala pacotes — só roda código.
"""
import json
import secrets
import threading
from datetime import timezone, datetime
from typing import Optional

from core.events import broadcaster, EVENT_BUILD_UPDATED
from core.utils import (
    ensure_venv, extract_imports, install_packages,
    utcnow_iso,
)
from db.database import get_conn


_build_lock = threading.Lock()


def trigger_build(script_id: str, version_id: str, code: str) -> None:
    """Inicia build em thread background."""
    t = threading.Thread(
        target=_run_build,
        args=(script_id, version_id, code),
        daemon=True,
    )
    t.start()


def _run_build(script_id: str, version_id: str, code: str) -> None:
    """Executa o build real: instala deps, atualiza status."""
    conn = get_conn()
    try:
        # Marca como building
        conn.execute(
            "UPDATE script_versions SET build_status='building' WHERE id=?",
            (version_id,),
        )
        conn.commit()

        imports = extract_imports(code)
        detected = json.dumps(imports)

        python_bin = ensure_venv(script_id)
        pkgs, err = install_packages(python_bin, imports)

        if err:
            conn.execute(
                """UPDATE script_versions
                   SET build_status='failed', build_error=?, detected_imports=?
                   WHERE id=?""",
                (err[:4000], detected, version_id),
            )
            conn.execute(
                "UPDATE scripts SET packages_ready=0 WHERE id=?",
                (script_id,),
            )
        else:
            # Captura lock de requirements
            req_lock = _capture_requirements(python_bin, pkgs)
            conn.execute(
                """UPDATE script_versions
                   SET build_status='ready', build_error=NULL,
                       detected_imports=?, requirements_lock=?
                   WHERE id=?""",
                (detected, req_lock, version_id),
            )
            conn.execute(
                "UPDATE scripts SET packages_ready=1 WHERE id=?",
                (script_id,),
            )
        conn.commit()
        # Notifica clientes SSE
        build_status = "failed" if err else "ready"
        broadcaster.publish(EVENT_BUILD_UPDATED, {
            "script_id":  script_id,
            "version_id": version_id,
            "status":     build_status,
            "error":      err[:500] if err else None,
        })

    except Exception as exc:
        try:
            conn.execute(
                "UPDATE script_versions SET build_status='failed', build_error=? WHERE id=?",
                (str(exc)[:4000], version_id),
            )
            conn.execute(
                "UPDATE scripts SET packages_ready=0 WHERE id=?",
                (script_id,),
            )
            conn.commit()
        except Exception:
            pass


def _capture_requirements(python_bin, pkgs: list[str]) -> Optional[str]:
    """Captura versões exatas dos pacotes instalados."""
    if not pkgs:
        return None
    try:
        import subprocess
        result = subprocess.run(
            [str(python_bin), "-m", "pip", "freeze",
             "--disable-pip-version-check"],
            capture_output=True, text=True, timeout=30,
        )
        return result.stdout[:50000] if result.returncode == 0 else None
    except Exception:
        return None


def _next_semver(script_id: str, conn) -> tuple[int, int, int]:
    """
    Calcula a próxima versão semver para o script.
    Patch vai até 999, aí incrementa minor. Minor vai até 999, aí incrementa major.
    """
    row = conn.execute(
        "SELECT version_major, version_minor, version_patch FROM script_versions WHERE script_id=? ORDER BY version DESC LIMIT 1",
        (script_id,),
    ).fetchone()
    if not row:
        return 0, 0, 1
    major, minor, patch = row["version_major"], row["version_minor"], row["version_patch"]
    patch += 1
    if patch > 999:
        patch = 0
        minor += 1
    if minor > 999:
        minor = 0
        major += 1
    return major, minor, patch


def semver_str(major: int, minor: int, patch: int) -> str:
    return f"v{major}.{minor}.{patch}"


def create_version(script_id: str, code: str, trigger_build_now: bool = True) -> dict:
    """
    Cria nova script_version com semver, opcionalmente triggera build.
    Retorna o dict da versão criada.
    """
    conn = get_conn()
    major, minor, patch = _next_semver(script_id, conn)

    # version sequencial para ordenação
    row = conn.execute(
        "SELECT COALESCE(MAX(version),0)+1 as next FROM script_versions WHERE script_id=?",
        (script_id,),
    ).fetchone()
    next_v = row["next"] if row else 1

    version_id = secrets.token_hex(8)
    imports = extract_imports(code)
    semver = semver_str(major, minor, patch)

    conn.execute(
        """INSERT INTO script_versions
           (id, script_id, version, version_major, version_minor, version_patch,
            version_label, code, detected_imports, build_status)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            version_id, script_id, next_v,
            major, minor, patch, semver,
            code, json.dumps(imports), "pending",
        ),
    )
    # Atualiza published_version no script
    conn.execute(
        "UPDATE scripts SET published_version_id=?, draft_code=?, updated_at=? WHERE id=?",
        (version_id, code, utcnow_iso(), script_id),
    )
    conn.commit()

    if trigger_build_now:
        trigger_build(script_id, version_id, code)

    return {
        "id": version_id,
        "script_id": script_id,
        "version": next_v,
        "version_label": semver,
        "code": code,
        "detected_imports": imports,
        "build_status": "pending",
    }


def get_build_status(version_id: str) -> Optional[dict]:
    conn = get_conn()
    row = conn.execute(
        "SELECT id, version, build_status, build_error, detected_imports FROM script_versions WHERE id=?",
        (version_id,),
    ).fetchone()
    if not row:
        return None
    return dict(row)
