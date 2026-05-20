import asyncio
import json
import threading
from typing import Set
import websockets
from project_context.utils import UI

class BrowserBridgeServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 8765):
        self.host = host
        self.port = port
        self.clients: Set[websockets.WebSocketServerProtocol] = set()
        self._loop: asyncio.AbstractEventLoop = None
        self._thread: threading.Thread = None
        self._server = None

    async def _register(self, websocket):
        self.clients.add(websocket)
        # UI.info(f"[Bridge] Cliente conectado desde {websocket.remote_address}")
        try:
            async for message in websocket:
                # Opcional: procesar mensajes entrantes de la extensión
                pass
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self.clients.remove(websocket)
            # UI.info("[Bridge] Cliente desconectado")

    async def _main_server(self):
        self._loop = asyncio.get_running_loop()
        try:
            async with websockets.serve(self._register, self.host, self.port) as server:
                self._server = server
                # Mantiene el servidor corriendo hasta que se cancele la tarea
                await asyncio.Future()
        except Exception as e:
            UI.error(f"[Bridge Error] Falló al iniciar el servidor en {self.host}:{self.port}: {e}")

    def start(self):
        """Inicia el servidor en un hilo de fondo."""
        if self._thread and self._thread.is_alive():
            return

        def run_loop():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(self._main_server())
            except asyncio.CancelledError:
                pass
            finally:
                loop.close()

        self._thread = threading.Thread(target=run_loop, daemon=True, name="BridgeServerThread")
        self._thread.start()
        UI.info(f"Servidor de puente iniciado en ws://{self.host}:{self.port}")

    def stop(self):
        """Detiene el servidor y cierra el bucle de eventos de forma limpia."""
        if not self._loop:
            return

        UI.info("Deteniendo servidor de puente...")

        # Programamos la detención en el bucle de eventos correspondiente
        async def shutdown():
            if self._server:
                self._server.close()
                await self._server.wait_closed()

            # Cancelar todas las tareas pendientes
            tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            for task in tasks:
                task.cancel()

        if self._loop.is_running():
            asyncio.run_coroutine_threadsafe(shutdown(), self._loop)

        if self._thread:
            self._thread.join(timeout=2.0)

    def broadcast_reload(self):
        """Envía de forma segura la orden de recarga a todos los clientes conectados."""
        if not self._loop or not self._loop.is_running():
            return

        message = json.dumps({"action": "reload"})

        async def send_to_all():
            if self.clients:
                # Usamos asyncio.gather para enviar concurrentemente a todos los navegadores
                await asyncio.gather(
                    *[client.send(message) for client in self.clients],
                    return_exceptions=True
                )

        # Inyectamos de forma segura la corrutina asíncrona desde nuestro hilo síncrono
        asyncio.run_coroutine_threadsafe(send_to_all(), self._loop)
