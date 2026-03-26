"""
HookPad — Configuração central
"""
import os
from pathlib import Path

DATA_DIR       = Path(os.getenv("DATA_DIR", "./scripts_data"))
VENV_DIR       = DATA_DIR / "venvs"
SCRIPTS_DIR    = DATA_DIR / "scripts"
HISTORY_DIR    = DATA_DIR / "history"
BUILDS_DIR     = DATA_DIR / "builds"

ADMIN_TOKEN    = os.getenv("ADMIN_TOKEN", "admin-mude-isso")
BASE_URL       = os.getenv("BASE_URL", "http://localhost:8000")
EXEC_TIMEOUT   = int(os.getenv("EXEC_TIMEOUT", "30"))
VENV_TTL_DAYS  = int(os.getenv("VENV_TTL_DAYS", "7"))
MAX_WORKERS    = int(os.getenv("MAX_WORKERS", "4"))

# Sandbox limits
MEM_LIMIT_MB   = int(os.getenv("SANDBOX_MEM_MB", "512"))
CPU_LIMIT_SEC  = int(os.getenv("SANDBOX_CPU_SEC", "60"))

# Payload limits
MAX_BODY_BYTES     = int(os.getenv("MAX_BODY_BYTES", str(10 * 1024 * 1024)))   # 10 MB
MAX_RESPONSE_BYTES = int(os.getenv("MAX_RESPONSE_BYTES", str(10 * 1024 * 1024)))
MAX_LOG_BYTES      = int(os.getenv("MAX_LOG_BYTES", str(512 * 1024)))           # 512 KB

for d in [DATA_DIR, VENV_DIR, SCRIPTS_DIR, HISTORY_DIR, BUILDS_DIR]:
    d.mkdir(parents=True, exist_ok=True)
