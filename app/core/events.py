"""
HookPad — Broadcaster de eventos SSE (Server-Sent Events)

Gerencia conexões ativas e publica eventos para todos os clientes.
Sem Redis, sem dependências externas — tudo em memória.

Uso:
    from core.events import broadcaster

    # publicar
    broadcaster.publish("execution.created", {"execution_id": "abc", "script_id": "xyz"})

    # no endpoint SSE
    async for chunk in broadcaster.subscribe():
        yield chunk
"""
import asyncio
import json
import logging
import time
from typing import AsyncGenerator

log = logging.getLogger("hookpad.events")

# Tipos de evento suportados
EVENT_EXECUTION_CREATED = "execution.created"
EVENT_EXECUTION_UPDATED = "execution.updated"
EVENT_SCRIPT_UPDATED    = "script.updated"
EVENT_TOKEN_EXPIRED     = "token.expired"
EVENT_BUILD_UPDATED     = "build.updated"
EVENT_HEARTBEAT         = "heartbeat"

HEARTBEAT_INTERVAL = 20  # segundos


class _Broadcaster:
    """
    Gerenciador SSE em memória.
    Cada cliente conectado tem sua própria asyncio.Queue.
    """

    def __init__(self):
        self._queues: set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()

    # ── Publish ───────────────────────────────────────────────────────────────
    def publish(self, event_type: str, payload: dict) -> None:
        """
        Publica evento para todos os clientes conectados.
        Thread-safe: pode ser chamado de qualquer thread (inclusive workers).
        """
        message = _format_event(event_type, payload)

        # Tenta usar o loop do uvicorn se já estiver rodando
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.call_soon_threadsafe(self._put_all, message)
                return
        except RuntimeError:
            pass

        # Fallback: coloca direto (já estamos no loop)
        self._put_all(message)

    def _put_all(self, message: str) -> None:
        dead: list[asyncio.Queue] = []
        for q in self._queues:
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self._queues.discard(q)

    # ── Subscribe ─────────────────────────────────────────────────────────────
    async def subscribe(self) -> AsyncGenerator[str, None]:
        """
        Generator assíncrono que produz chunks SSE para um cliente.
        Gerencia registro/remoção da fila automaticamente.
        Envia heartbeat periódico para manter a conexão viva.
        """
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        async with self._lock:
            self._queues.add(q)
        log.debug(f"SSE client connected ({len(self._queues)} total)")

        try:
            # Envia evento de conexão confirmada
            yield _format_event("connected", {"status": "ok"})

            while True:
                try:
                    # Aguarda mensagem com timeout para heartbeat
                    message = await asyncio.wait_for(q.get(), timeout=HEARTBEAT_INTERVAL)
                    yield message
                except asyncio.TimeoutError:
                    # Nenhuma mensagem no período → heartbeat
                    yield _format_event(EVENT_HEARTBEAT, {"ts": int(time.time())})
                except asyncio.CancelledError:
                    break

        except GeneratorExit:
            pass
        finally:
            async with self._lock:
                self._queues.discard(q)
            log.debug(f"SSE client disconnected ({len(self._queues)} total)")

    # ── Info ──────────────────────────────────────────────────────────────────
    @property
    def connection_count(self) -> int:
        return len(self._queues)


def _format_event(event_type: str, payload: dict) -> str:
    """
    Formata mensagem no protocolo SSE:
        event: <type>\\n
        data: <json>\\n
        \\n
    """
    data = json.dumps(payload, ensure_ascii=False, default=str)
    return f"event: {event_type}\ndata: {data}\n\n"


# Instância global — importada por todos os módulos que precisam publicar
broadcaster = _Broadcaster()
