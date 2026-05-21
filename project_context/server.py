import asyncio
import json
import threading
from typing import Dict, Set
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

        self._response_events: Dict[str, threading.Event] = {}
        # Guardaremos un diccionario con el estado detallado: {"isEmpty": bool, "focused": bool}
        self._responses: Dict[str, dict] = {}

    async def _register(self, websocket):
        self.clients.add(websocket)
        try:
            async for raw_message in websocket:
                try:
                    message = json.loads(raw_message)
                    action = message.get("action")
                    chat_id = message.get("chat_id")

                    if action == "reply_empty_status" and chat_id in self._response_events:
                        self._responses[chat_id] = {
                            "isEmpty": message.get("isEmpty", True),
                            "focused": message.get("focused", False)
                        }
                        self._response_events[chat_id].set()
                    elif action == "reply_tab_not_focused":
                        # El CLI intentó hacer reload pero la extensión detectó que no está enfocada
                        UI.info("[Bridge] El chat se actualizó en Drive, pero la pestaña no está enfocada en el navegador.")

                    elif action == "reply_run_status" and chat_id in self._response_events:
                        self._responses[chat_id] = {
                            "success": message.get("success", False),
                            "message": message.get("message", "")
                        }
                        self._response_events[chat_id].set()


                except Exception as e:
                    UI.error(f"[Bridge] Error procesando respuesta del cliente: {e}")
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self.clients.remove(websocket)
    def trigger_browser_run(self, chat_id: str, timeout: float = 16.0) -> dict:
        """
        Envía la orden a la extensión para presionar RUN y espera la confirmación.
        """
        default_status = {"success": False, "message": "Servidor de puente no conectado."}
        if not self.clients or not self._loop or not self._loop.is_running():
            return default_status

        event = threading.Event()
        self._response_events[chat_id] = event
        self._responses[chat_id] = default_status

        message = json.dumps({"action": "run", "chat_id": chat_id})

        async def send_query():
            for client in self.clients:
                await client.send(message)

        asyncio.run_coroutine_threadsafe(send_query(), self._loop)

        # Esperamos a que la extensión haga el polling e intente dar clic
        completed = event.wait(timeout=timeout)

        status = self._responses.pop(chat_id, default_status)
        self._response_events.pop(chat_id, None)

        return status

    async def _main_server(self):
        self._loop = asyncio.get_running_loop()
        try:
            async with websockets.serve(self._register, self.host, self.port) as server:
                self._server = server
                await asyncio.Future()
        except Exception as e:
            UI.error(f"[Bridge Error] Falló al iniciar el servidor en {self.host}:{self.port}: {e}")

    def start(self):
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
        if not self._loop:
            return
        UI.info("Deteniendo servidor de puente...")
        async def shutdown():
            if self._server:
                self._server.close()
                await self._server.wait_closed()
            tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            for task in tasks:
                task.cancel()

        if self._loop.is_running():
            asyncio.run_coroutine_threadsafe(shutdown(), self._loop)
        if self._thread:
            self._thread.join(timeout=2.0)

    def broadcast_reload(self, chat_id: str):
        """Envía de forma segura la orden de recarga filtrada por chat_id."""
        if not self._loop or not self._loop.is_running():
            return

        message = json.dumps({"action": "reload", "chat_id": chat_id})
        async def send_to_all():
            if self.clients:
                await asyncio.gather(
                    *[client.send(message) for client in self.clients],
                    return_exceptions=True
                )

        asyncio.run_coroutine_threadsafe(send_to_all(), self._loop)

    def check_if_input_empty(self, chat_id: str, timeout: float = 1.5) -> dict:
        """
        Consulta a la extensión si la pestaña activa y enfocada del chat_id está vacía.
        Retorna un diccionario indicando {'isEmpty': bool, 'focused': bool}.
        """
        default_status = {"isEmpty": True, "focused": False}

        if not self.clients or not self._loop or not self._loop.is_running():
            return default_status

        event = threading.Event()
        self._response_events[chat_id] = event
        self._responses[chat_id] = default_status

        message = json.dumps({"action": "query_empty_status", "chat_id": chat_id})

        async def send_query():
            for client in self.clients:
                await client.send(message)

        asyncio.run_coroutine_threadsafe(send_query(), self._loop)

        # Esperar respuesta de la extensión
        completed = event.wait(timeout=timeout)

        status = self._responses.pop(chat_id, default_status)
        self._response_events.pop(chat_id, None)

        return status
