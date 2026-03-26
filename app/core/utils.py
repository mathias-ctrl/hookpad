"""
HookPad — Utilitários centrais
"""
import ast
import re
import sys
import secrets
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from core.config import VENV_DIR


# ─── Tempo ───────────────────────────────────────────────────────────────────
def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def expiration_to_datetime(expiration: str) -> Optional[str]:
    if not expiration or expiration == "never":
        return None
    mapping = {"1h": 1 / 24, "24h": 1, "7d": 7, "30d": 30}
    days = mapping.get(expiration)
    if days is None:
        return None
    return (utcnow() + timedelta(days=days)).isoformat()


# ─── Slug ────────────────────────────────────────────────────────────────────
def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-") or "script"


def sanitize_slug(slug: str) -> str:
    """Garante slug válido para rota."""
    slug = re.sub(r"[^a-z0-9\-_/{}]", "", slug.lower())
    return slug or "script"


# ─── Python stdlib — não instalar ────────────────────────────────────────────
STDLIB = {
    "os", "sys", "re", "json", "math", "time", "datetime", "random", "hashlib",
    "base64", "pathlib", "typing", "collections", "itertools", "functools",
    "string", "io", "copy", "enum", "abc", "uuid", "logging", "traceback",
    "subprocess", "threading", "asyncio", "http", "urllib", "email", "csv",
    "xml", "html", "sqlite3", "pickle", "struct", "socket", "ssl", "hmac",
    "secrets", "dataclasses", "contextlib", "warnings", "inspect", "ast",
    "builtins", "types", "weakref", "gc", "platform", "shutil", "tempfile",
    "glob", "fnmatch", "stat", "errno", "signal", "atexit", "textwrap",
    "pprint", "decimal", "fractions", "statistics", "array", "heapq", "bisect",
    "zoneinfo", "calendar", "locale", "gettext", "argparse", "configparser",
    "concurrent", "multiprocessing", "queue", "operator", "numbers", "cmath",
}

IMPORT_MAP = {
    "cv2": "opencv-python", "PIL": "Pillow", "sklearn": "scikit-learn",
    "bs4": "beautifulsoup4", "yaml": "PyYAML", "dotenv": "python-dotenv",
    "attr": "attrs", "dateutil": "python-dateutil", "jwt": "PyJWT",
    "psycopg2": "psycopg2-binary", "magic": "python-magic",
    "serial": "pyserial", "usb": "pyusb", "gi": "PyGObject",
    "pydub": "pydub", "boto3": "boto3", "google": "google-cloud",
    "pymupdf": "PyMuPDF", "fitz": "PyMuPDF",
}


def get_pip_name(module: str) -> str:
    return IMPORT_MAP.get(module, module)


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


# ─── Assinatura do main() ────────────────────────────────────────────────────
def parse_main_signature(code: str) -> list[dict]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            params = []
            args = node.args
            defaults = args.defaults
            num_args = len(args.args)
            offset = num_args - len(defaults)
            for i, arg in enumerate(args.args):
                if arg.arg == "self":
                    continue
                param = {"name": arg.arg, "type": "str", "default": None, "has_default": False}
                if arg.annotation:
                    ann = ast.unparse(arg.annotation)
                    if ann in ("bytes", "bytes | None"):
                        param["type"] = "file"
                    elif ann == "int":
                        param["type"] = "int"
                    elif ann == "float":
                        param["type"] = "float"
                    elif ann == "bool":
                        param["type"] = "bool"
                    elif ann in ("dict", "Dict"):
                        param["type"] = "dict"
                    elif ann in ("list", "List"):
                        param["type"] = "list"
                di = i - offset
                if 0 <= di < len(defaults):
                    param["has_default"] = True
                    d = defaults[di]
                    if isinstance(d, ast.Constant):
                        param["default"] = d.value
                        if isinstance(d.value, bool):
                            param["type"] = "bool"
                        elif isinstance(d.value, int):
                            param["type"] = "int"
                        elif isinstance(d.value, float):
                            param["type"] = "float"
                    elif isinstance(d, ast.Dict):
                        param["type"] = "dict"
                        param["default"] = {}
                    elif isinstance(d, ast.List):
                        param["type"] = "list"
                        param["default"] = []
                params.append(param)
            return params
    return []


# ─── Venv ────────────────────────────────────────────────────────────────────
def get_venv_python(script_id: str) -> Path:
    venv_path = VENV_DIR / script_id
    if sys.platform == "win32":
        return venv_path / "Scripts" / "python.exe"
    return venv_path / "bin" / "python"


def ensure_venv(script_id: str) -> Path:
    venv_path = VENV_DIR / script_id
    python_bin = get_venv_python(script_id)
    if not python_bin.exists():
        subprocess.run(
            [sys.executable, "-m", "venv", str(venv_path)],
            check=True, capture_output=True
        )
    return python_bin


def install_packages(python_bin: Path, packages: list[str]) -> tuple[list[str], str]:
    if not packages:
        return [], ""
    pip_names = [get_pip_name(p) for p in packages]
    result = subprocess.run(
        [str(python_bin), "-m", "pip", "install", "--quiet",
         "--disable-pip-version-check"] + pip_names,
        capture_output=True, text=True, timeout=120,
    )
    return pip_names, result.stderr if result.returncode != 0 else ""


# ─── Token ───────────────────────────────────────────────────────────────────
def check_token_valid(script: dict, provided: Optional[str]) -> bool:
    if not script.get("token"):
        return False
    if provided != script["token"]:
        return False
    expires_at = script.get("token_expires_at")
    if expires_at:
        exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        exp = exp.replace(tzinfo=None)
        if utcnow() > exp:
            return False
    return True
