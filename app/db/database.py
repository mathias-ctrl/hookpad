"""
HookPad — Camada de banco de dados (SQLite)
Entidades: folders, scripts, script_versions, executions, execution_payloads
"""
import json
import sqlite3
import threading
from pathlib import Path
from core.config import DATA_DIR

DB_PATH = DATA_DIR / "hookpad.db"
_local  = threading.local()


def get_conn() -> sqlite3.Connection:
    """Retorna conexão thread-local com row_factory configurada."""
    if not hasattr(_local, "conn") or _local.conn is None:
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=NORMAL")
        _local.conn = conn
    return _local.conn


DDL = """
-- ─── Folders ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS folders (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    slug        TEXT NOT NULL UNIQUE,
    parent_id   TEXT REFERENCES folders(id) ON DELETE SET NULL,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- ─── Scripts ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS scripts (
    id                    TEXT PRIMARY KEY,
    folder_id             TEXT REFERENCES folders(id) ON DELETE SET NULL,
    name                  TEXT NOT NULL,
    slug                  TEXT NOT NULL,
    description           TEXT NOT NULL DEFAULT '',
    method                TEXT NOT NULL DEFAULT 'POST',
    route                 TEXT,
    enabled               INTEGER NOT NULL DEFAULT 1,
    trigger               TEXT NOT NULL DEFAULT 'webhook',
    schedule_interval     TEXT,
    last_schedule_run     TEXT,
    auth_mode             TEXT NOT NULL DEFAULT 'token',
    token                 TEXT,
    token_expires_at      TEXT,
    token_expiration      TEXT DEFAULT 'never',
    timeout_ms            INTEGER NOT NULL DEFAULT 30000,
    max_body_bytes        INTEGER NOT NULL DEFAULT 10485760,
    max_response_bytes    INTEGER NOT NULL DEFAULT 10485760,
    max_log_bytes         INTEGER NOT NULL DEFAULT 524288,
    max_concurrency       INTEGER NOT NULL DEFAULT 0,
    runtime_mode          TEXT NOT NULL DEFAULT 'sync',
    is_public             INTEGER NOT NULL DEFAULT 0,
    draft_code            TEXT NOT NULL DEFAULT '',
    published_version_id  TEXT,
    packages_ready        INTEGER NOT NULL DEFAULT 0,
    timezone              TEXT NOT NULL DEFAULT 'America/Sao_Paulo',
    cron_config           TEXT,
    created_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_scripts_method_route
    ON scripts(method, COALESCE(route, id));

-- ─── Script Versions ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS script_versions (
    id                 TEXT PRIMARY KEY,
    script_id          TEXT NOT NULL REFERENCES scripts(id) ON DELETE CASCADE,
    version            INTEGER NOT NULL DEFAULT 1,
    version_major      INTEGER NOT NULL DEFAULT 0,
    version_minor      INTEGER NOT NULL DEFAULT 0,
    version_patch      INTEGER NOT NULL DEFAULT 1,
    version_label      TEXT NOT NULL DEFAULT 'v0.0.1',
    code               TEXT NOT NULL DEFAULT '',
    python_version     TEXT NOT NULL DEFAULT '3',
    requirements_lock  TEXT,
    detected_imports   TEXT,
    system_deps        TEXT,
    build_status       TEXT NOT NULL DEFAULT 'pending',
    build_error        TEXT,
    build_log          TEXT,
    created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_versions_script ON script_versions(script_id, version DESC);

-- ─── Executions (metadados leves + previews) ─────────────────────────────────
CREATE TABLE IF NOT EXISTS executions (
    id                  TEXT PRIMARY KEY,
    script_id           TEXT NOT NULL REFERENCES scripts(id) ON DELETE CASCADE,
    script_version_id   TEXT REFERENCES script_versions(id) ON DELETE SET NULL,
    status              TEXT NOT NULL DEFAULT 'queued',
    trigger_type        TEXT NOT NULL DEFAULT 'webhook',
    request_method      TEXT,
    request_path        TEXT,
    response_status     INTEGER,
    error_message       TEXT,
    error_type          TEXT,
    started_at          TEXT,
    finished_at         TEXT,
    duration_ms         INTEGER,
    request_id          TEXT,
    ip                  TEXT,
    user_agent          TEXT,
    -- previews truncados (max 2000 chars cada)
    input_preview       TEXT,
    output_preview      TEXT,
    input_truncated     INTEGER NOT NULL DEFAULT 0,
    output_truncated    INTEGER NOT NULL DEFAULT 0,
    input_size_bytes    INTEGER,
    output_size_bytes   INTEGER,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_exec_script_created
    ON executions(script_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_exec_created
    ON executions(created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_exec_status
    ON executions(status);

-- ─── Execution Payloads (conteúdo completo, carregado sob demanda) ────────────
CREATE TABLE IF NOT EXISTS execution_payloads (
    execution_id     TEXT PRIMARY KEY REFERENCES executions(id) ON DELETE CASCADE,
    input_full       TEXT,
    output_full      TEXT,
    request_headers  TEXT,
    request_query    TEXT,
    request_body     TEXT,
    response_headers TEXT,
    response_body    TEXT,
    stdout           TEXT,
    stderr           TEXT,
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- ─── Settings ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
INSERT OR IGNORE INTO settings(key, value) VALUES ('history_days', '30');
"""


def init_db():
    """Cria/migra tabelas mantendo compatibilidade com bancos existentes."""
    conn = get_conn()
    conn.executescript(DDL)
    conn.commit()

    # ── Migrações incrementais ────────────────────────────────────────────────
    _migrate_scripts_columns(conn)
    _migrate_versions_columns(conn)
    _migrate_executions_columns(conn)
    conn.commit()

    # ── Migração de dados legados (JSON → SQLite) ─────────────────────────────
    _migrate_legacy_json()


def _migrate_scripts_columns(conn):
    existing = {r[1] for r in conn.execute("PRAGMA table_info(scripts)").fetchall()}
    additions = [
        ("timezone",   "TEXT",    "'America/Sao_Paulo'"),
        ("cron_config","TEXT",    "NULL"),
    ]
    for col, typ, default in additions:
        if col not in existing:
            conn.execute(f"ALTER TABLE scripts ADD COLUMN {col} {typ} DEFAULT {default}")


def _migrate_versions_columns(conn):
    existing = {r[1] for r in conn.execute("PRAGMA table_info(script_versions)").fetchall()}
    additions = [
        ("version_major", "INTEGER", "0"),
        ("version_minor", "INTEGER", "0"),
        ("version_patch", "INTEGER", "1"),
        ("version_label", "TEXT",    "'v0.0.1'"),
    ]
    for col, typ, default in additions:
        if col not in existing:
            conn.execute(f"ALTER TABLE script_versions ADD COLUMN {col} {typ} DEFAULT {default}")


def _migrate_executions_columns(conn):
    """
    Adiciona colunas novas de preview/payload se não existirem.
    Remove colunas legadas (stdout/stderr/etc) que migraram para execution_payloads.
    SQLite não suporta DROP COLUMN antes de 3.35 — apenas deixamos de ler.
    """
    existing = {r[1] for r in conn.execute("PRAGMA table_info(executions)").fetchall()}
    additions = [
        ("input_preview",     "TEXT",    "NULL"),
        ("output_preview",    "TEXT",    "NULL"),
        ("input_truncated",   "INTEGER", "0"),
        ("output_truncated",  "INTEGER", "0"),
        ("input_size_bytes",  "INTEGER", "NULL"),
        ("output_size_bytes", "INTEGER", "NULL"),
    ]
    for col, typ, default in additions:
        if col not in existing:
            conn.execute(f"ALTER TABLE executions ADD COLUMN {col} {typ} DEFAULT {default}")

    # Cria tabela de payloads se ainda não existir (pode ter sido criada pelo DDL acima)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS execution_payloads (
            execution_id     TEXT PRIMARY KEY REFERENCES executions(id) ON DELETE CASCADE,
            input_full       TEXT,
            output_full      TEXT,
            request_headers  TEXT,
            request_query    TEXT,
            request_body     TEXT,
            response_headers TEXT,
            response_body    TEXT,
            stdout           TEXT,
            stderr           TEXT,
            created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
        )
    """)


# ─── Migração de scripts/histórico JSON legado → SQLite ─────────────────────
def _migrate_legacy_json():
    import secrets as _sec
    scripts_dir = DATA_DIR / "scripts"
    history_dir = DATA_DIR / "history"
    conn = get_conn()
    migrated = 0

    for f in scripts_dir.glob("*.json"):
        sid = f.stem
        if conn.execute("SELECT id FROM scripts WHERE id=?", (sid,)).fetchone():
            continue
        try:
            s = json.loads(f.read_text())
        except Exception:
            continue

        conn.execute("""
            INSERT OR IGNORE INTO scripts
              (id, name, slug, description, method, enabled, trigger,
               schedule_interval, last_schedule_run, auth_mode,
               token, token_expires_at, token_expiration,
               draft_code, packages_ready, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            sid, s.get("name", sid), _slugify(s.get("name", sid)),
            s.get("description", ""), s.get("method", "POST"),
            1 if s.get("enabled", True) else 0,
            s.get("trigger", "webhook"), s.get("schedule_interval"),
            s.get("last_schedule_run"), "token",
            s.get("token"), s.get("token_expires_at"),
            s.get("token_expiration", "never"),
            s.get("code", ""),
            1 if s.get("packages_ready", False) else 0,
            s.get("created_at", _now()), s.get("updated_at", _now()),
        ))

        # Migra histórico JSON → executions (sem payloads — dados antigos)
        hist_file = history_dir / f"{sid}.json"
        if hist_file.exists():
            try:
                runs = json.loads(hist_file.read_text())
                for r in runs:
                    eid = r.get("id", _sec.token_hex(6))
                    ts  = r.get("timestamp")
                    conn.execute("""
                        INSERT OR IGNORE INTO executions
                          (id, script_id, status, trigger_type,
                           input_preview, output_preview,
                           duration_ms, started_at, finished_at, created_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?)
                    """, (
                        eid, sid,
                        "success" if r.get("success") else "failed",
                        r.get("triggered_by", "webhook"),
                        (r.get("stdout") or "")[:2000],
                        (r.get("stderr") or "")[:2000],
                        r.get("duration_ms", 0),
                        ts, ts, ts,
                    ))
            except Exception:
                pass
        migrated += 1

    if migrated:
        conn.commit()


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slugify(text: str) -> str:
    import re
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-") or "script"


# ─── Helpers ─────────────────────────────────────────────────────────────────
def row_to_dict(row) -> dict | None:
    if row is None:
        return None
    return dict(row)


def rows_to_list(rows) -> list[dict]:
    return [dict(r) for r in rows]
