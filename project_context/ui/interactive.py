import signal
import sys
from pathlib import Path

from project_context.api_drive import AIStudioDriveManager
from project_context.exceptions import ProjectContextError
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

            # Ejecución centralizada a través del despachador
            should_continue = registry.execute(command_name, ctx, args)
            consecutive_errors = 0  # Se reinicia el contador si se completó la instrucción

            if should_continue is False:

                break

        except (EOFError, KeyboardInterrupt):
            UI.info("Saliendo...")
            ctx.stop_monitor()

            break
        except ProjectContextError as e:
            # Captura de excepciones específicas de negocio
            UI.error(str(e))
            consecutive_errors += 1
            if consecutive_errors > 10:
                UI.error("Demasiados errores consecutivos. Saliendo de forma segura...")
                ctx.stop_monitor()

                sys.exit(1)
        except Exception as e:
            # Fallos generales imprevistos o de comunicación profunda
            UI.error(f"Error inesperado de ejecución: {e}")
            consecutive_errors += 1
            if consecutive_errors > 10:
                UI.error("Demasiados errores inesperados consecutivos. Saliendo de forma segura...")
                ctx.stop_monitor()

                sys.exit(1)
