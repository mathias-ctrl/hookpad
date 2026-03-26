"""
HookPad — Scheduler de scripts agendados

Suporta dois formatos de schedule_interval:
  Legado: "5min", "1h", "daily", "weekly"
  Novo (cron builder): "Ns", "Nm", "Nh", "Nd", "NM"
    onde N é um inteiro positivo e o sufixo é:
      s = segundos, m = minutos, h = horas, d = dias, M = meses (≈30 dias)
"""
import logging
import threading
import time
from datetime import datetime
from typing import Optional

from core.utils import utcnow
from db.database import get_conn

log = logging.getLogger("hookpad.scheduler")

_running = False
_thread: Optional[threading.Thread] = None


def start():
    global _running, _thread
    _running = True
    _thread = threading.Thread(target=_loop, daemon=True, name="hookpad-scheduler")
    _thread.start()
    log.info("Scheduler iniciado")


def stop():
    global _running
    _running = False


def interval_to_seconds(schedule: str) -> Optional[int]:
    """
    Converte schedule_interval para segundos.
    Suporta formatos legados e novo formato do cron builder.
    """
    if not schedule:
        return None

    # ── Formato legado ────────────────────────────────────────────────────────
    legacy = {
        "5min":  5 * 60,
        "1h":    60 * 60,
        "daily": 24 * 60 * 60,
        "weekly": 7 * 24 * 60 * 60,
    }
    if schedule in legacy:
        return legacy[schedule]

    # ── Novo formato: Ns, Nm, Nh, Nd, NM ──────────────────────────────────────
    # sufixo pode ser: s, m, h, d, M
    if len(schedule) >= 2:
        suffix = schedule[-1]
        try:
            n = int(schedule[:-1])
        except ValueError:
            return None
        multipliers = {
            "s": 1,
            "m": 60,
            "h": 3600,
            "d": 86400,
            "M": 30 * 86400,
        }
        if suffix in multipliers and n > 0:
            return n * multipliers[suffix]

    return None


def _loop():
    while _running:
        try:
            _tick()
        except Exception as e:
            log.error(f"Scheduler tick error: {e}", exc_info=True)
        time.sleep(10)  # verifica a cada 10s (antes era 30s)


def _tick():
    conn = get_conn()
    now  = utcnow()

    rows = conn.execute(
        """SELECT id, draft_code, published_version_id, schedule_interval,
                  last_schedule_run, timeout_ms, name
           FROM scripts
           WHERE enabled=1 AND trigger='schedule'"""
    ).fetchall()

    for row in rows:
        sid      = row["id"]
        schedule = row["schedule_interval"]

        if not schedule:
            log.debug(f"Script {sid} sem schedule_interval, pulando")
            continue

        interval_sec = interval_to_seconds(schedule)
        if not interval_sec:
            log.warning(f"Script {sid}: schedule_interval inválido '{schedule}'")
            continue

        last = row["last_schedule_run"]
        if last:
            last_dt  = datetime.fromisoformat(last.replace("Z", ""))
            elapsed  = (now - last_dt).total_seconds()
            if elapsed < interval_sec:
                continue

        code = _get_code(row)
        if not code:
            log.warning(f"Script {sid} sem código, pulando")
            continue

        log.info(f"Scheduler disparando script '{row['name']}' ({sid}), interval={schedule}")

        try:
            from core.executor import create_execution, run_execution, _script_executor
            exec_id = create_execution(
                script_id=sid,
                script_version_id=row["published_version_id"],
                trigger_type="schedule",
            )
            _script_executor.submit(
                run_execution, exec_id, sid, code,
                {}, row["timeout_ms"] or 30000, True,
            )
            conn.execute(
                "UPDATE scripts SET last_schedule_run=? WHERE id=?",
                (now.isoformat(), sid),
            )
            conn.commit()
            log.info(f"Execução agendada criada: {exec_id}")
        except Exception as e:
            log.error(f"Erro ao disparar script {sid}: {e}", exc_info=True)


def _get_code(row) -> str:
    version_id = row["published_version_id"]
    if version_id:
        conn = get_conn()
        r = conn.execute(
            "SELECT code FROM script_versions WHERE id=?", (version_id,)
        ).fetchone()
        if r:
            return r["code"]
    return row["draft_code"] or ""
