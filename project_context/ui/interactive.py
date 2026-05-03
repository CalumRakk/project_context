import signal
import sys
from pathlib import Path

from project_context.api_drive import AIStudioDriveManager
from project_context.history import SnapshotManager
from project_context.ui.commands import SessionContext, registry
from project_context.utils import (
    UI,
    console,
)


def interactive_session(api: AIStudioDriveManager, state: dict, project_path: Path):
    UI.info("Sesión interactiva iniciada. Escribe [bold]help[/] para comandos.")

    ctx = SessionContext(
        api=api,
        state=state,
        project_path=project_path,
        monitor=SnapshotManager(api, project_path, state)
    )

    def handle_exit(sig, frame):
        UI.info("Cerrando sesión de forma segura...")
        ctx.stop_monitor()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_exit)  # Ctrl+C
    if sys.platform != "win32":
        signal.signal(signal.SIGTERM, handle_exit)

    ctx.start_monitor()

    UI.info(f"[Chat] Iniciando sesión con chat_id {state.get('chat_id')}...")
    consecutive_errors = 0

    while True:
        try:
            command_line = console.input("[bold green]>> [/]").strip()
            if not command_line:
                continue

            parts = command_line.split(" ", 1)
            command_name = parts[0].lower()
            args = parts[1] if len(parts) > 1 else ""

            handler = registry.commands.get(command_name)

            if handler:
                consecutive_errors = 0
                should_continue = handler(ctx, args)
                if should_continue is False:
                    break
            else:
                print(f"Comando desconocido: '{command_name}'")

        except (EOFError, KeyboardInterrupt):
            UI.info("Saliendo...")
            ctx.stop_monitor()
            break
        except Exception as e:
            UI.error(f"Error de ejecución: {e}")

            consecutive_errors += 1
            if consecutive_errors > 10:
                UI.error("Demasiados errores consecutivos. Saliendo...")
                ctx.stop_monitor()
                sys.exit(1)

            ctx.start_monitor()
