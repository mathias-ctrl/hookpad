"""Retenção e compactação do histórico de execuções."""
import logging
from datetime import timedelta

from core.utils import utcnow
from db.database import get_conn

log = logging.getLogger("hookpad.history")


def cleanup_expired_history() -> int:
    """Remove execuções mais antigas que `settings.history_days`.

    history_days <= 0 desativa a limpeza automática.
    Retorna a quantidade aproximada de execuções removidas.
    """
    conn = get_conn()
    row = conn.execute("SELECT value FROM settings WHERE key='history_days'").fetchone()
    try:
        days = int(row["value"]) if row else 30
    except (TypeError, ValueError):
        days = 30
    if days <= 0:
        return 0

    cutoff = (utcnow() - timedelta(days=days)).isoformat()
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM executions WHERE created_at < ?", (cutoff,)
    ).fetchall()]
    if not ids:
        return 0

    # Exclusão explícita torna a limpeza robusta mesmo em bancos/conexões
    # legadas onde foreign_keys pode não ter sido habilitado anteriormente.
    placeholders = ",".join("?" for _ in ids)
    conn.execute(f"DELETE FROM execution_payloads WHERE execution_id IN ({placeholders})", ids)
    conn.execute(f"DELETE FROM executions WHERE id IN ({placeholders})", ids)
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
    log.info("Histórico: %s execuções expiradas removidas", len(ids))
    return len(ids)


def clear_history(script_id: str | None = None, compact: bool = True) -> int:
    """Apaga histórico e opcionalmente devolve espaço ao sistema operacional."""
    conn = get_conn()
    if script_id:
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM executions WHERE script_id=?", (script_id,)
        ).fetchall()]
    else:
        ids = [r["id"] for r in conn.execute("SELECT id FROM executions").fetchall()]

    if ids:
        placeholders = ",".join("?" for _ in ids)
        conn.execute(f"DELETE FROM execution_payloads WHERE execution_id IN ({placeholders})", ids)
        conn.execute(f"DELETE FROM executions WHERE id IN ({placeholders})", ids)
        conn.commit()

    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    if compact:
        conn.execute("VACUUM")
    return len(ids)
