
"""
HookPad — Python Script Webhook Runner
Backend FastAPI com auto-install de deps e venv isolado por script.
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
from pathlib import Path
from datetime import datetime
from typing import Optional, Any

from fastapi import FastAPI, HTTPException, Request, Header, Depends
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ─── Config ─────────────────────────────────────────────────────────────────
DATA_DIR    = Path(os.getenv("DATA_DIR", "./scripts_data"))
VENV_DIR    = DATA_DIR / "venvs"
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "admin-mude-isso")
BASE_URL    = os.getenv("BASE_URL", "http://localhost:8000")
TIMEOUT     = int(os.getenv("EXEC_TIMEOUT", "30"))

DATA_DIR.mkdir(parents=True, exist_ok=True)
VENV_DIR.mkdir(parents=True, exist_ok=True)
SCRIPTS_FILE = DATA_DIR / "scripts.json"

# ─── App ─────────────────────────────────────────────────────────────────────
app = FastAPI(title="HookPad", docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Storage ─────────────────────────────────────────────────────────────────
def load_scripts() -> dict:
    if not SCRIPTS_FILE.exists():
        return {}
    return json.loads(SCRIPTS_FILE.read_text())

def save_scripts(scripts: dict):
    SCRIPTS_FILE.write_text(json.dumps(scripts, indent=2, ensure_ascii=False))

# ─── Auth ─────────────────────────────────────────────────────────────────────
def require_admin(x_admin_token: str = Header(...)):
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Token admin inválido")

def require_script_token(script_id: str, x_token: Optional[str] = Header(None), token: Optional[str] = None):
    scripts = load_scripts()
    if script_id not in scripts:
        raise HTTPException(status_code=404, detail="Script não encontrado")
    script = scripts[script_id]
    provided = x_token or token
    if provided != script["token"]:
        raise HTTPException(status_code=401, detail="Token inválido")
    return script

# ─── Models ─────────────────────────────────────────────────────────────────
class ScriptCreate(BaseModel):
    name: str
    description: str = ""
    code: str
    method: str = "POST"   # GET ou POST
    enabled: bool = True

class ScriptUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    code: Optional[str] = None
    method: Optional[str] = None
    enabled: Optional[bool] = None

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
    "__future__",
}

IMPORT_MAP = {
    "cv2": "opencv-python",
    "PIL": "Pillow",
    "sklearn": "scikit-learn",
    "bs4": "beautifulsoup4",
    "yaml": "PyYAML",
    "dotenv": "python-dotenv",
    "attr": "attrs",
    "dateutil": "python-dateutil",
    "jwt": "PyJWT",
    "MySQLdb": "mysqlclient",
    "psycopg2": "psycopg2-binary",
    "usaddress": "usaddress",
    "magic": "python-magic",
    "gi": "PyGObject",
    "wx": "wxPython",
    "pydub": "pydub",
    "tensorflow": "tensorflow",
    "tf": "tensorflow",
}

def extract_imports(code: str) -> list[str]:
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

def install_packages(python_bin: Path, packages: list[str]) -> tuple[list[str], str]:
    if not packages:
        return [], ""
    pip_names = [get_pip_name(p) for p in packages]
    result = subprocess.run(
        [str(python_bin), "-m", "pip", "install", "--quiet", "--disable-pip-version-check"] + pip_names,
        capture_output=True, text=True, timeout=120
    )
    return pip_names, result.stderr if result.returncode != 0 else ""

def run_script(script_id: str, code: str, params: dict) -> dict:
    start = datetime.utcnow()
    installed = []
    install_log = ""

    try:
        python_bin = ensure_venv(script_id)
        imports = extract_imports(code)
        if imports:
            pkgs, err = install_packages(python_bin, imports)
            installed = pkgs
            install_log = err

        # Injeta params como variáveis no topo do script
        inject = f"__params__ = {json.dumps(params)}\n"
        for k, v in params.items():
            safe_key = k.replace("-", "_").replace(" ", "_")
            inject += f"{safe_key} = __params__.get('{k}')\n"
        full_code = inject + "\n" + code

        with __import__("tempfile").NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write(full_code)
            tmp_path = f.name

        result = subprocess.run(
            [str(python_bin), tmp_path],
            capture_output=True, text=True, timeout=TIMEOUT
        )
        Path(tmp_path).unlink(missing_ok=True)

        duration = int((datetime.utcnow() - start).total_seconds() * 1000)
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout[:50000],
            "stderr": (install_log + result.stderr)[:10000],
            "duration_ms": duration,
            "installed_packages": installed,
            "timestamp": start.isoformat(),
        }

    except subprocess.TimeoutExpired:
        duration = int((datetime.utcnow() - start).total_seconds() * 1000)
        return {
            "success": False,
            "stdout": "",
            "stderr": f"Timeout: execução excedeu {TIMEOUT}s",
            "duration_ms": duration,
            "installed_packages": installed,
            "timestamp": start.isoformat(),
        }
    except Exception as e:
        duration = int((datetime.utcnow() - start).total_seconds() * 1000)
        return {
            "success": False,
            "stdout": "",
            "stderr": traceback.format_exc(),
            "duration_ms": duration,
            "installed_packages": installed,
            "timestamp": start.isoformat(),
        }

# ─── Admin API ───────────────────────────────────────────────────────────────

@app.get("/api/scripts", dependencies=[Depends(require_admin)])
def list_scripts():
    scripts = load_scripts()
    return [
        {**s, "webhook_url": f"{BASE_URL}/hook/{sid}"} 
        for sid, s in scripts.items()
    ]

@app.post("/api/scripts", dependencies=[Depends(require_admin)])
def create_script(body: ScriptCreate):
    scripts = load_scripts()
    sid = str(uuid.uuid4())[:8]
    token = secrets.token_urlsafe(32)
    scripts[sid] = {
        "id": sid,
        "name": body.name,
        "description": body.description,
        "code": body.code,
        "method": body.method.upper(),
        "enabled": body.enabled,
        "token": token,
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
    }
    save_scripts(scripts)
    return {**scripts[sid], "webhook_url": f"{BASE_URL}/hook/{sid}"}

@app.get("/api/scripts/{script_id}", dependencies=[Depends(require_admin)])
def get_script(script_id: str):
    scripts = load_scripts()
    if script_id not in scripts:
        raise HTTPException(404, "Script não encontrado")
    s = scripts[script_id]
    return {**s, "webhook_url": f"{BASE_URL}/hook/{script_id}"}

@app.put("/api/scripts/{script_id}", dependencies=[Depends(require_admin)])
def update_script(script_id: str, body: ScriptUpdate):
    scripts = load_scripts()
    if script_id not in scripts:
        raise HTTPException(404, "Script não encontrado")
    s = scripts[script_id]
    for field, val in body.model_dump(exclude_none=True).items():
        s[field] = val
    s["updated_at"] = datetime.utcnow().isoformat()
    scripts[script_id] = s
    save_scripts(scripts)
    return {**s, "webhook_url": f"{BASE_URL}/hook/{script_id}"}

@app.delete("/api/scripts/{script_id}", dependencies=[Depends(require_admin)])
def delete_script(script_id: str):
    scripts = load_scripts()
    if script_id not in scripts:
        raise HTTPException(404, "Script não encontrado")
    del scripts[script_id]
    save_scripts(scripts)
    venv_path = VENV_DIR / script_id
    if venv_path.exists():
        shutil.rmtree(venv_path)
    return {"ok": True}

@app.post("/api/scripts/{script_id}/rotate-token", dependencies=[Depends(require_admin)])
def rotate_token(script_id: str):
    scripts = load_scripts()
    if script_id not in scripts:
        raise HTTPException(404, "Script não encontrado")
    scripts[script_id]["token"] = secrets.token_urlsafe(32)
    scripts[script_id]["updated_at"] = datetime.utcnow().isoformat()
    save_scripts(scripts)
    return {"token": scripts[script_id]["token"]}

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
    result = run_script(script_id, s["code"], params)
    return result

# ─── Webhook ─────────────────────────────────────────────────────────────────

async def _execute_hook(script_id: str, request: Request, token: Optional[str]):
    scripts = load_scripts()
    if script_id not in scripts:
        raise HTTPException(404, "Script não encontrado")
    s = scripts[script_id]

    if not s.get("enabled", True):
        raise HTTPException(403, "Script desativado")

    # Verifica token: header X-Token ou query param ?token=
    x_token = request.headers.get("x-token")
    q_token = request.query_params.get("token")
    provided = x_token or q_token or token
    if provided != s["token"]:
        raise HTTPException(401, "Token inválido")

    # Coleta params de todas as fontes
    params = {}
    params.update(dict(request.query_params))
    params.pop("token", None)

    # Headers customizados (X- prefix)
    for k, v in request.headers.items():
        if k.lower().startswith("x-") and k.lower() != "x-token":
            params[k.lower().replace("x-", "", 1)] = v

    # Body (JSON, form ou raw)
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

    result = run_script(script_id, s["code"], params)
    status = 200 if result["success"] else 500
    return JSONResponse(result, status_code=status)

@app.get("/hook/{script_id}")
async def webhook_get(script_id: str, request: Request):
    return await _execute_hook(script_id, request, None)

@app.post("/hook/{script_id}")
async def webhook_post(script_id: str, request: Request):
    return await _execute_hook(script_id, request, None)

# ─── Frontend ─────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def frontend():
    with open(Path(__file__).parent / "index.html", encoding="utf-8") as f:
        return f.read()
