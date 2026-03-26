"""
HookPad — Sandbox de execução isolado
Cada execução roda em subprocess separado com:
  - cwd temporário próprio
  - env vars controladas
  - timeout hard
  - captura de stdout/stderr
  - cleanup ao final
  - limites de recursos (Linux)
"""
import gc
import json
import re
import sys
import tempfile
import traceback
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core.config import (
    EXEC_TIMEOUT, MEM_LIMIT_MB, CPU_LIMIT_SEC,
    MAX_LOG_BYTES, MAX_RESPONSE_BYTES,
)
from core.utils import (
    ensure_venv, extract_imports, get_pip_name, install_packages,
    parse_main_signature, utcnow,
)


def _sandbox_preexec():
    """Aplica limites de recursos ao processo filho (Linux only)."""
    try:
        import resource
        mem_bytes = MEM_LIMIT_MB * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
        resource.setrlimit(resource.RLIMIT_CPU, (CPU_LIMIT_SEC, CPU_LIMIT_SEC))
        resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
    except Exception:
        pass


def _build_clean_env() -> dict:
    """Constrói env limpo para o processo filho — sem herdar secrets do host."""
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "HOME": tempfile.gettempdir(),
        "LANG": "en_US.UTF-8",
    }


def _build_script_source(code: str, params: dict) -> str:
    """
    Monta o código completo injetando params e chamando main() se existir.
    Nunca usa shell=True. Usa lista de args.
    """
    sig_params_types = {p["name"]: p["type"] for p in parse_main_signature(code)}

    header_lines = [
        "import base64 as __b64",
        f"__params__ = {json.dumps(params)}",
    ]
    for k, v in params.items():
        safe_key = re.sub(r"[^a-zA-Z0-9_]", "_", k)
        param_type = sig_params_types.get(k, "str")
        if param_type == "file":
            header_lines.append(
                f"{safe_key} = __b64.b64decode(__params__.get({json.dumps(k)}, ''))"
            )
        else:
            header_lines.append(f"{safe_key} = __params__.get({json.dumps(k)})")

    header = "\n".join(header_lines)

    has_main = "def main(" in code
    footer = ""
    if has_main:
        sig_params_list = parse_main_signature(code)
        call_args = [re.sub(r"[^a-zA-Z0-9_]", "_", p["name"]) for p in sig_params_list]
        call_str = ", ".join(call_args)
        footer_lines = [
            "",
            "import json as __json, base64 as __b64r, sys as __sys",
            "",
            "def __to_b64(v):",
            "    return __b64r.b64encode(v).decode('ascii')",
            "",
            "def __res_to_json(res):",
            "    typ = type(res)",
            "    if typ.__name__ == 'bytes':",
            "        return __json.dumps(__to_b64(res))",
            "    elif typ.__name__ == 'DataFrame':",
            "        try:",
            "            if typ.__module__ == 'pandas.core.frame':",
            "                res = res.values.tolist()",
            "            elif typ.__module__ == 'polars.dataframe.frame':",
            "                res = res.rows()",
            "        except Exception:",
            "            pass",
            "    elif typ.__name__ == 'dict':",
            "        for k, v in res.items():",
            "            if type(v).__name__ == 'bytes':",
            "                res[k] = __to_b64(v)",
            "    return __json.dumps(res, ensure_ascii=False, default=str)",
            "",
            "try:",
            f"    __result__ = main({call_str})",
            "    if __result__ is not None:",
            "        __sys.stdout.write('wm_res[success]:' + __res_to_json(__result__) + chr(10))",
            "        __sys.stdout.flush()",
            "except BaseException as __e:",
            "    import traceback as __tbtb",
            "    __tb_lines = __tbtb.format_tb(__e.__traceback__)",
            "    __tb_str = ''.join(__tb_lines)",
            "    __err = __json.dumps({'message': str(__e), 'name': __e.__class__.__name__, 'stack': __tb_str}, default=str)",
            "    __sys.stdout.write('wm_res[error]:' + __err + chr(10))",
            "    __sys.stdout.flush()",
        ]
        footer = "\n".join(footer_lines)

    return header + "\n" + code + "\n" + footer


def execute_script(
    script_id: str,
    code: str,
    params: dict,
    triggered_by: str = "webhook",
    timeout_ms: Optional[int] = None,
    skip_install: bool = False,
) -> dict:
    """
    Executa script em subprocess isolado.
    Retorna dict com todos os campos do execution.
    """
    timeout_sec = (timeout_ms // 1000) if timeout_ms else EXEC_TIMEOUT
    start = utcnow()
    installed: list[str] = []
    install_log = ""

    try:
        # ── Build/install (não instala durante request se skip_install=True) ──
        python_bin = ensure_venv(script_id)
        if not skip_install:
            imports = extract_imports(code)
            if imports:
                pkgs, err = install_packages(python_bin, imports)
                installed = pkgs
                install_log = err

        full_code = _build_script_source(code, params)

        # ── Cria temp dir e arquivo isolados ─────────────────────────────────
        with tempfile.TemporaryDirectory(prefix=f"hookpad_{script_id}_") as tmp_dir:
            tmp_path = Path(tmp_dir) / "script.py"
            tmp_path.write_text(full_code, encoding="utf-8")

            preexec = _sandbox_preexec if sys.platform != "win32" else None
            clean_env = _build_clean_env()

            proc = subprocess.run(
                [str(python_bin), str(tmp_path)],
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                cwd=tmp_dir,                    # cwd isolado
                env=clean_env,                  # env limpo
                preexec_fn=preexec,
            )
            raw_stdout = proc.stdout
            raw_stderr = proc.stderr
            raw_rc = proc.returncode
            del proc

        # ── Parseia protocolo wm_res ──────────────────────────────────────────
        script_result = None
        script_error = None
        clean_lines: list[str] = []

        for line in raw_stdout.splitlines():
            if line.startswith("wm_res[success]:"):
                try:
                    script_result = json.loads(line[len("wm_res[success]:"):])
                except Exception:
                    script_result = line[len("wm_res[success]:"):]
            elif line.startswith("wm_res[error]:"):
                try:
                    script_error = json.loads(line[len("wm_res[error]:"):])
                except Exception:
                    script_error = {"message": line[len("wm_res[error:]:"):]}
            else:
                clean_lines.append(line)

        stdout = "\n".join(clean_lines)
        stderr_full = install_log + raw_stderr
        if isinstance(script_error, dict):
            stderr_full += script_error.get("stack", "")

        # ── Aplica limites de log ─────────────────────────────────────────────
        stdout = stdout[:MAX_LOG_BYTES]
        stderr_full = stderr_full[:MAX_LOG_BYTES]

        success = raw_rc == 0 and script_error is None
        duration = int((utcnow() - start).total_seconds() * 1000)

        result = {
            "status": "success" if success else "failed",
            "success": success,
            "result": script_result,
            "error": script_error,
            "error_message": script_error.get("message") if isinstance(script_error, dict) else None,
            "error_type": script_error.get("name") if isinstance(script_error, dict) else None,
            "stdout": stdout,
            "stderr": stderr_full,
            "installed_packages": installed,
            "duration_ms": duration,
            "started_at": start.isoformat(),
            "finished_at": utcnow().isoformat(),
        }

        # Libera memória
        del raw_stdout, raw_stderr
        gc.collect()
        return result

    except subprocess.TimeoutExpired:
        duration = int((utcnow() - start).total_seconds() * 1000)
        return {
            "status": "timeout",
            "success": False,
            "result": None,
            "error": None,
            "error_message": f"Timeout: execução excedeu {timeout_sec}s",
            "error_type": "TimeoutExpired",
            "stdout": "",
            "stderr": f"Timeout: execução excedeu {timeout_sec}s",
            "installed_packages": installed,
            "duration_ms": duration,
            "started_at": start.isoformat(),
            "finished_at": utcnow().isoformat(),
        }
    except Exception:
        duration = int((utcnow() - start).total_seconds() * 1000)
        tb = traceback.format_exc()
        return {
            "status": "failed",
            "success": False,
            "result": None,
            "error": None,
            "error_message": tb,
            "error_type": "InternalError",
            "stdout": "",
            "stderr": tb,
            "installed_packages": installed,
            "duration_ms": duration,
            "started_at": start.isoformat(),
            "finished_at": utcnow().isoformat(),
        }
