"""Retenção e limpeza segura do histórico de execuções."""
import logging
import sqlite3
import time
from datetime import timedelta

from core.utils import utcnow
from db.database import DB_PATH, get_conn

log = logging.getLogger("hookpad.history")
_LOCK_RETRIES = 6
_LOCK_RETRY_BASE_SEC = 0.15


def _maintenance_conn() -> sqlite3.Connection:
    """Conexão curta e independente para operações de manutenção."""
    conn = sqlite3.connect(str(DB_PATH), timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _delete_with_retry(script_id: str | None = None, cutoff: str | None = None) -> int:
    """Exclui em uma transação curta e repete quando outro writer segura o SQLite."""
    last_error: Exception | None = None
    for attempt in range(_LOCK_RETRIES):
        conn = _maintenance_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")

            clauses: list[str] = []
            params: list[str] = []
            if script_id:
                clauses.append("script_id=?")
                params.append(script_id)
            if cutoff:
                clauses.append("created_at < ?")
                params.append(cutoff)
            where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

            row = conn.execute(f"SELECT COUNT(*) AS total FROM executions{where}", params).fetchone()
            total = int(row["total"] if row else 0)
            if total:
                # O FK ON DELETE CASCADE remove execution_payloads na mesma transação.
                conn.execute(f"DELETE FROM executions{where}", params)
            conn.commit()
            return total
        except sqlite3.OperationalError as exc:
            conn.rollback()
            last_error = exc
            if "locked" not in str(exc).lower() or attempt == _LOCK_RETRIES - 1:
                raise
            time.sleep(_LOCK_RETRY_BASE_SEC * (2 ** attempt))
        finally:
            conn.close()
    if last_error:
        raise last_error
    return 0


def _checkpoint_passive() -> None:
    """Checkpoint não bloqueante; falhar aqui não invalida a exclusão."""
    conn = _maintenance_conn()
    try:
        conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
    except sqlite3.OperationalError:
        log.debug("Checkpoint adiado porque o banco está ocupado", exc_info=True)
    finally:
        conn.close()


def cleanup_expired_history() -> int:
    """Remove execuções mais antigas que `settings.history_days`."""
    conn = get_conn()
    row = conn.execute("SELECT value FROM settings WHERE key='history_days'").fetchone()
    try:
        days = int(row["value"]) if row else 30
    except (TypeError, ValueError):
        days = 30
    if days <= 0:
        return 0

    cutoff = (utcnow() - timedelta(days=days)).isoformat()
    deleted = _delete_with_retry(cutoff=cutoff)
    if deleted:
        _checkpoint_passive()
        log.info("Histórico: %s execuções expiradas removidas", deleted)
    return deleted


def clear_history(script_id: str | None = None, compact: bool = False) -> int:
    """Apaga o histórico sem executar VACUUM durante requisições concorrentes.

    `compact` é mantido por compatibilidade, mas a compactação online é evitada:
    VACUUM exige lock exclusivo e causava erro 500 enquanto webhooks gravavam.
    """
    deleted = _delete_with_retry(script_id=script_id)
    if deleted:
        _checkpoint_passive()
    return deleted
