"""
HookPad v2 — Python Script Webhook Runner
Fixes: thread safety, async executor, pre-install, param injection,
       per-file scripts, schedule validation, utcnow, history pagination
"""

import os
import sys
import ast
import uuid
import json
import shutil
import secrets
import subprocess
import traceback
import threading
import asyncio
import re
from functools import partial
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request, Header, Depends
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator

# ─── Config ──────────────────────────────────────────────────────────────────
DATA_DIR     = Path(os.getenv("DATA_DIR", "./scripts_data"))
VENV_DIR     = DATA_DIR / "venvs"
SCRIPTS_DIR  = DATA_DIR / "scripts"
ADMIN_TOKEN  = os.getenv("ADMIN_TOKEN", "admin-mude-isso")
BASE_URL     = os.getenv("BASE_URL", "http://localhost:8000")
TIMEOUT      = int(os.getenv("EXEC_TIMEOUT", "30"))
HISTORY_DIR  = DATA_DIR / "history"

# Arquivo legado (será migrado automaticamente)
SCRIPTS_FILE_LEGACY = DATA_DIR / "scripts.json"

for d in [DATA_DIR, VENV_DIR, SCRIPTS_DIR, HISTORY_DIR]:
    d.mkdir(parents=True, exist_ok=True)

SETTINGS_FILE = DATA_DIR / "settings.json"

# ─── Thread safety ────────────────────────────────────────────────────────────
_scripts_lock = threading.Lock()

# ─── App ─────────────────────────────────────────────────────────────────────
app = FastAPI(title="HookPad", docs_url=None, redoc_url=None)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ─── Helpers de tempo ────────────────────────────────────────────────────────
def utcnow() -> datetime:
    """Retorna datetime UTC naive. Compatível com isoformat() em todo o código."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

# ─── Scheduler ───────────────────────────────────────────────────────────────
scheduler_thread = None
scheduler_running = False

def start_scheduler():
    global scheduler_thread, scheduler_running
    scheduler_running = True
    scheduler_thread = threading.Thread(target=scheduler_loop, daemon=True)
    scheduler_thread.start()

def scheduler_loop():
    import time
    while scheduler_running:
        try:
            scripts = load_scripts()
            now = utcnow()
            for sid, s in scripts.items():
                if not s.get("enabled", True):
                    continue
                if s.get("trigger", "webhook") != "schedule":
                    continue
                schedule = s.get("schedule_interval", "")
                if not schedule:
                    continue
                last_run = s.get("last_schedule_run")
                interval_minutes = schedule_to_minutes(schedule)
                if interval_minutes is None:
                    continue
                if last_run:
                    last_dt = datetime.fromisoformat(last_run)
                    if (now - last_dt).total_seconds() < interval_minutes * 60:
                        continue
                run_script(sid, s["code"], {}, triggered_by="schedule")
                with _scripts_lock:
                    scripts2 = load_scripts()
                    if sid in scripts2:
                        scripts2[sid]["last_schedule_run"] = now.isoformat()
                        save_script(sid, scripts2[sid])
        except Exception:
            pass
        time.sleep(30)

def schedule_to_minutes(schedule: str) -> Optional[int]:
    mapping = {"5min": 5, "1h": 60, "daily": 1440, "weekly": 10080}
    return mapping.get(schedule)

# ─── Storage ─────────────────────────────────────────────────────────────────
def _migrate_legacy():
    """Migra scripts.json antigo para arquivos individuais."""
    if not SCRIPTS_FILE_LEGACY.exists():
        return
    try:
        old = json.loads(SCRIPTS_FILE_LEGACY.read_text())
        for sid, s in old.items():
            dest = SCRIPTS_DIR / f"{sid}.json"
            if not dest.exists():
                dest.write_text(json.dumps(s, indent=2, ensure_ascii=False))
        SCRIPTS_FILE_LEGACY.rename(SCRIPTS_FILE_LEGACY.with_suffix(".json.bak"))
    except Exception:
        pass

def load_scripts() -> dict:
    scripts = {}
    for f in SCRIPTS_DIR.glob("*.json"):
        try:
            s = json.loads(f.read_text())
            scripts[f.stem] = s
        except Exception:
            pass
    return scripts

def save_script(sid: str, data: dict):
    (SCRIPTS_DIR / f"{sid}.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False)
    )

def save_scripts(scripts: dict):
    for sid, data in scripts.items():
        save_script(sid, data)

def load_settings() -> dict:
    if not SETTINGS_FILE.exists():
        return {"history_days": 30}
    return json.loads(SETTINGS_FILE.read_text())

def save_settings(settings: dict):
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2))

def save_run(script_id: str, result: dict):
    history_file = HISTORY_DIR / f"{script_id}.json"
    runs = []
    if history_file.exists():
        try:
            runs = json.loads(history_file.read_text())
        except Exception:
            runs = []
    runs.insert(0, result)
    settings = load_settings()
    days = settings.get("history_days", 30)
    cutoff = utcnow() - timedelta(days=days)
    runs = [r for r in runs if datetime.fromisoformat(r["timestamp"]) > cutoff]
    runs = runs[:1000]
    history_file.write_text(json.dumps(runs, indent=2, ensure_ascii=False))

def load_runs(script_id: str) -> list:
    history_file = HISTORY_DIR / f"{script_id}.json"
    if not history_file.exists():
        return []
    try:
        return json.loads(history_file.read_text())
    except Exception:
        return []

def load_all_runs(limit: int = 50, offset: int = 0) -> list:
    all_runs = []
    scripts = load_scripts()
    for sid in scripts:
        runs = load_runs(sid)
        for r in runs:
            r["script_id"] = sid
            r["script_name"] = scripts[sid].get("name", sid)
        all_runs.extend(runs)
    all_runs.sort(key=lambda x: x["timestamp"], reverse=True)
    return all_runs[offset:offset + limit]

def clear_history(script_id: Optional[str] = None):
    if script_id:
        history_file = HISTORY_DIR / f"{script_id}.json"
        if history_file.exists():
            history_file.unlink()
    else:
        for f in HISTORY_DIR.glob("*.json"):
            f.unlink()

# ─── Auth ─────────────────────────────────────────────────────────────────────
def require_admin(x_admin_token: str = Header(...)):
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Token admin inválido")

def check_token_valid(script: dict, provided: str) -> bool:
    if not script.get("token"):
        return False
    if provided != script["token"]:
        return False
    expires_at = script.get("token_expires_at")
    if expires_at:
        exp = datetime.fromisoformat(expires_at)
        if utcnow() > exp:
            return False
    return True

# ─── Models ──────────────────────────────────────────────────────────────────
VALID_SCHEDULES = {"5min", "1h", "daily", "weekly"}

class ScriptCreate(BaseModel):
    name: str
    description: str = ""
    code: str
    method: str = "POST"
    enabled: bool = True
    trigger: str = "webhook"
    schedule_interval: Optional[str] = None

    @field_validator("schedule_interval")
    @classmethod
    def validate_schedule(cls, v):
        if v is not None and v not in VALID_SCHEDULES:
            raise ValueError(f"schedule_interval deve ser um de: {sorted(VALID_SCHEDULES)}")
        return v

class ScriptUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    code: Optional[str] = None
    method: Optional[str] = None
    enabled: Optional[bool] = None
    trigger: Optional[str] = None
    schedule_interval: Optional[str] = None

    @field_validator("schedule_interval")
    @classmethod
    def validate_schedule(cls, v):
        if v is not None and v not in VALID_SCHEDULES:
            raise ValueError(f"schedule_interval deve ser um de: {sorted(VALID_SCHEDULES)}")
        return v

class GenerateTokenRequest(BaseModel):
    expiration: Optional[str] = None

class SettingsUpdate(BaseModel):
    history_days: Optional[int] = None

# ─── Helpers ─────────────────────────────────────────────────────────────────
STDLIB = {
    "os","sys","re","json","math","time","datetime","random","hashlib",
    "base64","pathlib","typing","collections","itertools","functools",
    "string","io","copy","enum","abc","uuid","logging","traceback",
    "subprocess","threading","asyncio","http","urllib","email","csv",
    "xml","html","sqlite3","pickle","struct","socket","ssl","hmac",
    "secrets","dataclasses","contextlib","warnings","inspect","ast",
    "builtins","types","weakref","gc","platform","shutil","tempfile",
    "glob","fnmatch","stat","errno","signal","atexit","textwrap",
    "pprint","decimal","fractions","statistics","array","heapq","bisect",
    "zoneinfo","calendar","locale","gettext","argparse","configparser",
}

IMPORT_MAP = {
    "cv2": "opencv-python", "PIL": "Pillow", "sklearn": "scikit-learn",
    "bs4": "beautifulsoup4", "yaml": "PyYAML", "dotenv": "python-dotenv",
    "attr": "attrs", "dateutil": "python-dateutil", "jwt": "PyJWT",
    "psycopg2": "psycopg2-binary", "magic": "python-magic",
    "serial": "pyserial", "usb": "pyusb", "gi": "PyGObject",
    "pydub": "pydub", "boto3": "boto3", "google": "google-cloud",
}

def extract_imports(code: str) -> list:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                modules.add(node.module.split(".")[0])
    return [m for m in modules if m not in STDLIB and not m.startswith("_")]

def detect_params(code: str) -> dict:
    params = {}
    pattern = r'__params__\.get\(["\'](\w+)["\'](?:,\s*([^)]+))?\)'
    for match in re.finditer(pattern, code):
        key = match.group(1)
        default = match.group(2)
        if default:
            default = default.strip().strip('"\'')
            try:
                default = int(default)
            except (ValueError, TypeError):
                try:
                    default = float(default)
                except (ValueError, TypeError):
                    pass
        else:
            default = ""
        params[key] = default
    return params

def get_pip_name(module: str) -> str:
    return IMPORT_MAP.get(module, module)

def get_venv_python(script_id: str) -> Path:
    venv_path = VENV_DIR / script_id
    if sys.platform == "win32":
        return venv_path / "Scripts" / "python.exe"
    return venv_path / "bin" / "python"

def ensure_venv(script_id: str) -> Path:
    venv_path = VENV_DIR / script_id
    python_bin = get_venv_python(script_id)
    if not python_bin.exists():
        subprocess.run([sys.executable, "-m", "venv", str(venv_path)], check=True)
    return python_bin

def install_packages(python_bin: Path, packages: list) -> tuple:
    if not packages:
        return [], ""
    pip_names = [get_pip_name(p) for p in packages]
    result = subprocess.run(
        [str(python_bin), "-m", "pip", "install", "--quiet", "--disable-pip-version-check"] + pip_names,
        capture_output=True, text=True, timeout=120
    )
    return pip_names, result.stderr if result.returncode != 0 else ""

def run_script(script_id: str, code: str, params: dict, triggered_by: str = "webhook") -> dict:
    import tempfile
    start = utcnow()
    installed = []
    install_log = ""
    try:
        python_bin = ensure_venv(script_id)
        imports = extract_imports(code)
        if imports:
            pkgs, err = install_packages(python_bin, imports)
            installed = pkgs
            install_log = err

        # FIX 4: json.dumps(k) escapa corretamente qualquer chave
        inject = f"__params__ = {json.dumps(params)}\n"
        for k, v in params.items():
            safe_key = re.sub(r'[^a-zA-Z0-9_]', '_', k)
            inject += f"{safe_key} = __params__.get({json.dumps(k)})\n"
        full_code = inject + "\n" + code

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write(full_code)
            tmp_path = f.name

        result = subprocess.run(
            [str(python_bin), tmp_path],
            capture_output=True, text=True, timeout=TIMEOUT
        )
        Path(tmp_path).unlink(missing_ok=True)
        duration = int((utcnow() - start).total_seconds() * 1000)

        run_result = {
            "id": str(uuid.uuid4())[:12],
            "success": result.returncode == 0,
            "stdout": result.stdout[:50000],
            "stderr": (install_log + result.stderr)[:10000],
            "duration_ms": duration,
            "installed_packages": installed,
            "timestamp": start.isoformat(),
            "triggered_by": triggered_by,
            "params": params,
        }
        save_run(script_id, run_result)
        return run_result

    except subprocess.TimeoutExpired:
        duration = int((utcnow() - start).total_seconds() * 1000)
        run_result = {
            "id": str(uuid.uuid4())[:12],
            "success": False,
            "stdout": "",
            "stderr": f"Timeout: execução excedeu {TIMEOUT}s",
            "duration_ms": duration,
            "installed_packages": installed,
            "timestamp": start.isoformat(),
            "triggered_by": triggered_by,
            "params": params,
        }
        save_run(script_id, run_result)
        return run_result
    except Exception:
        duration = int((utcnow() - start).total_seconds() * 1000)
        run_result = {
            "id": str(uuid.uuid4())[:12],
            "success": False,
            "stdout": "",
            "stderr": traceback.format_exc(),
            "duration_ms": duration,
            "installed_packages": installed,
            "timestamp": start.isoformat(),
            "triggered_by": triggered_by,
            "params": params,
        }
        save_run(script_id, run_result)
        return run_result

def expiration_to_datetime(expiration: str) -> Optional[str]:
    if not expiration or expiration == "never":
        return None
    mapping = {"1h": 1/24, "24h": 1, "7d": 7, "30d": 30}
    days = mapping.get(expiration)
    if days is None:
        return None
    return (utcnow() + timedelta(days=days)).isoformat()

def script_with_url(s: dict, sid: str) -> dict:
    result = dict(s)
    result["webhook_url"] = f"{BASE_URL}/hook/{sid}"
    result["detected_params"] = detect_params(s.get("code", ""))
    if result.get("token_expires_at"):
        exp = datetime.fromisoformat(result["token_expires_at"])
        result["token_expired"] = utcnow() > exp
    else:
        result["token_expired"] = False
    return result

# ─── Admin API — Scripts ─────────────────────────────────────────────────────
@app.get("/api/scripts", dependencies=[Depends(require_admin)])
def list_scripts():
    scripts = load_scripts()
    return [script_with_url(s, sid) for sid, s in scripts.items()]

@app.post("/api/scripts", dependencies=[Depends(require_admin)])
def create_script(body: ScriptCreate):
    sid = secrets.token_hex(8)
    data = {
        "id": sid,
        "name": body.name,
        "description": body.description,
        "code": body.code,
        "method": body.method.upper(),
        "enabled": body.enabled,
        "trigger": body.trigger,
        "schedule_interval": body.schedule_interval,
        "token": None,
        "token_expires_at": None,
        "token_expiration": None,
        "packages_ready": False,
        "created_at": utcnow().isoformat(),
        "updated_at": utcnow().isoformat(),
        "last_schedule_run": None,
    }
    with _scripts_lock:
        save_script(sid, data)
    return script_with_url(data, sid)

@app.get("/api/scripts/{script_id}", dependencies=[Depends(require_admin)])
def get_script(script_id: str):
    scripts = load_scripts()
    if script_id not in scripts:
        raise HTTPException(404, "Script não encontrado")
    return script_with_url(scripts[script_id], script_id)

@app.put("/api/scripts/{script_id}", dependencies=[Depends(require_admin)])
def update_script(script_id: str, body: ScriptUpdate):
    with _scripts_lock:
        scripts = load_scripts()
        if script_id not in scripts:
            raise HTTPException(404, "Script não encontrado")
        s = scripts[script_id]
        for field, val in body.model_dump(exclude_none=True).items():
            s[field] = val
        if body.code is not None:
            s["packages_ready"] = False
        s["updated_at"] = utcnow().isoformat()
        save_script(script_id, s)
    return script_with_url(s, script_id)

@app.delete("/api/scripts/{script_id}", dependencies=[Depends(require_admin)])
def delete_script(script_id: str):
    with _scripts_lock:
        scripts = load_scripts()
        if script_id not in scripts:
            raise HTTPException(404, "Script não encontrado")
        script_file = SCRIPTS_DIR / f"{script_id}.json"
        if script_file.exists():
            script_file.unlink()
    venv_path = VENV_DIR / script_id
    if venv_path.exists():
        shutil.rmtree(venv_path)
    clear_history(script_id)
    return {"ok": True}

# ─── Install deps ─────────────────────────────────────────────────────────────
@app.post("/api/scripts/{script_id}/install", dependencies=[Depends(require_admin)])
async def install_script_deps(script_id: str):
    """Pré-instala dependências do script. Chamar automaticamente ao salvar."""
    scripts = load_scripts()
    if script_id not in scripts:
        raise HTTPException(404, "Script não encontrado")
    s = scripts[script_id]

    def _do_install():
        python_bin = ensure_venv(script_id)
        imports = extract_imports(s["code"])
        pkgs, err = install_packages(python_bin, imports)
        return pkgs, err

    loop = asyncio.get_event_loop()
    pkgs, err = await loop.run_in_executor(None, _do_install)

    with _scripts_lock:
        scripts2 = load_scripts()
        if script_id in scripts2:
            scripts2[script_id]["packages_ready"] = (err == "")
            save_script(script_id, scripts2[script_id])

    return {"installed": pkgs, "error": err, "ok": err == ""}

# ─── Token ───────────────────────────────────────────────────────────────────
@app.post("/api/scripts/{script_id}/generate-token", dependencies=[Depends(require_admin)])
def generate_token(script_id: str, body: GenerateTokenRequest):
    with _scripts_lock:
        scripts = load_scripts()
        if script_id not in scripts:
            raise HTTPException(404, "Script não encontrado")
        token = secrets.token_urlsafe(32)
        expires_at = expiration_to_datetime(body.expiration)
        scripts[script_id]["token"] = token
        scripts[script_id]["token_expires_at"] = expires_at
        scripts[script_id]["token_expiration"] = body.expiration or "never"
        scripts[script_id]["updated_at"] = utcnow().isoformat()
        save_script(script_id, scripts[script_id])
    return {"token": token, "expires_at": expires_at, "expiration": body.expiration or "never"}

@app.post("/api/scripts/{script_id}/revoke-token", dependencies=[Depends(require_admin)])
def revoke_token(script_id: str):
    with _scripts_lock:
        scripts = load_scripts()
        if script_id not in scripts:
            raise HTTPException(404, "Script não encontrado")
        scripts[script_id]["token"] = None
        scripts[script_id]["token_expires_at"] = None
        scripts[script_id]["token_expiration"] = None
        scripts[script_id]["updated_at"] = utcnow().isoformat()
        save_script(script_id, scripts[script_id])
    return {"ok": True}

# ─── Test ────────────────────────────────────────────────────────────────────
@app.post("/api/scripts/{script_id}/test", dependencies=[Depends(require_admin)])
async def test_script(script_id: str, request: Request):
    scripts = load_scripts()
    if script_id not in scripts:
        raise HTTPException(404, "Script não encontrado")
    s = scripts[script_id]
    params = {}
    try:
        body = await request.json()
        if isinstance(body, dict):
            params.update(body)
    except Exception:
        pass
    params.update(dict(request.query_params))

    # FIX 2: thread pool para não bloquear o event loop
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        partial(run_script, script_id, s["code"], params, "test")
    )
    return result

# ─── History ─────────────────────────────────────────────────────────────────
@app.get("/api/scripts/{script_id}/history", dependencies=[Depends(require_admin)])
def get_script_history(script_id: str):
    scripts = load_scripts()
    if script_id not in scripts:
        raise HTTPException(404, "Script não encontrado")
    return load_runs(script_id)

@app.delete("/api/scripts/{script_id}/history", dependencies=[Depends(require_admin)])
def clear_script_history(script_id: str):
    clear_history(script_id)
    return {"ok": True}

@app.get("/api/history", dependencies=[Depends(require_admin)])
def get_all_history(limit: int = 50, offset: int = 0):
    # FIX 8: paginação
    return load_all_runs(limit=limit, offset=offset)

@app.delete("/api/history", dependencies=[Depends(require_admin)])
def clear_all_history():
    clear_history()
    return {"ok": True}

# ─── Settings ────────────────────────────────────────────────────────────────
@app.get("/api/settings", dependencies=[Depends(require_admin)])
def get_settings():
    return load_settings()

@app.put("/api/settings", dependencies=[Depends(require_admin)])
def update_settings(body: SettingsUpdate):
    settings = load_settings()
    if body.history_days is not None:
        settings["history_days"] = body.history_days
    save_settings(settings)
    return settings

@app.get("/api/base-url")
def get_base_url():
    return {"base_url": BASE_URL}

# ─── Webhook ─────────────────────────────────────────────────────────────────
async def _execute_hook(script_id: str, request: Request):
    scripts = load_scripts()
    if script_id not in scripts:
        raise HTTPException(404, "Script não encontrado")
    s = scripts[script_id]

    if not s.get("enabled", True):
        raise HTTPException(403, "Script desativado")

    if s.get("trigger", "webhook") != "webhook":
        raise HTTPException(400, "Este script não está configurado como webhook")

    x_token = request.headers.get("x-token")
    q_token = request.query_params.get("token")
    provided = x_token or q_token

    if not check_token_valid(s, provided):
        if not s.get("token"):
            raise HTTPException(401, "Nenhum token gerado para este script")
        if s.get("token_expires_at") and utcnow() > datetime.fromisoformat(s["token_expires_at"]):
            raise HTTPException(401, "Token expirado")
        raise HTTPException(401, "Token inválido")

    params = dict(request.query_params)
    params.pop("token", None)
    for k, v in request.headers.items():
        if k.lower().startswith("x-") and k.lower() not in ("x-token", "x-admin-token"):
            params[k.lower().replace("x-", "", 1)] = v

    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            body = await request.json()
            if isinstance(body, dict):
                params.update(body)
        except Exception:
            pass
    elif "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
        form = await request.form()
        params.update(dict(form))

    # FIX 2: thread pool para não bloquear o event loop
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        partial(run_script, script_id, s["code"], params, "webhook")
    )
    return JSONResponse(result, status_code=200 if result["success"] else 500)

@app.get("/hook/{script_id}")
async def webhook_get(script_id: str, request: Request):
    return await _execute_hook(script_id, request)

@app.post("/hook/{script_id}")
async def webhook_post(script_id: str, request: Request):
    return await _execute_hook(script_id, request)

# ─── Frontend ─────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def frontend():
    with open(Path(__file__).parent / "index.html", encoding="utf-8") as f:
        return f.read()

# ─── Startup ──────────────────────────────────────────────────────────────────
@app.on_event("startup")
def on_startup():
    _migrate_legacy()
    start_scheduler()
