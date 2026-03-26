"""
HookPad — Python Script Webhook Platform
Main entrypoint: monta routers, configura middleware, startup/shutdown.
"""
import concurrent.futures
import os
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

# ── Pool de workers compartilhado ────────────────────────────────────────────
# Importado pelos routers que precisam de run_in_executor
import core.executor as _executor_module

_executor_module._script_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=int(os.getenv("MAX_WORKERS", "4")),
    thread_name_prefix="hookpad-worker",
)

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="HookPad", docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
from routers.scripts    import router as scripts_router
from routers.executions import router as exec_router
from routers.folders    import router as folders_router
from routers.webhook    import router as webhook_router
from routers.test_run   import router as test_router
from routers.settings   import router as settings_router

app.include_router(scripts_router)
app.include_router(exec_router)
app.include_router(folders_router)
app.include_router(webhook_router)
app.include_router(test_router)
app.include_router(settings_router)


# ── Frontend ──────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def frontend():
    html_path = Path(__file__).parent / "index.html"
    return html_path.read_text(encoding="utf-8")


# ── Startup / Shutdown ────────────────────────────────────────────────────────
@app.on_event("startup")
def on_startup():
    from db.database import init_db
    init_db()
    from core import scheduler
    scheduler.start()


@app.on_event("shutdown")
def on_shutdown():
    from core import scheduler
    scheduler.stop()
    _executor_module._script_executor.shutdown(wait=False)
