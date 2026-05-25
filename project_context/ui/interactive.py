import shlex
import signal
import sys
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import NestedCompleter, PathCompleter, WordCompleter
from prompt_toolkit.history import InMemoryHistory

from project_context.api_drive import AIStudioDriveManager
from project_context.exceptions import ProjectContextError
from project_context.history import SnapshotManager
from project_context.ui.commands import SessionContext, registry
from project_context.utils import UI, profile_manager


def create_interactive_completer(
    project_path: Path, commands: list[str]
) -> NestedCompleter:
    """
    Construye un completador jerárquico dinámico mapeando subcomandos y
    archivos relativos al proyecto bajo análisis.
    """
    # Obtener los perfiles creados para autocompletar en el comando 'transfer'
    profiles = profile_manager.list_profiles()

    # Configurar el completador de rutas para que solo busque dentro del proyecto actual
    project_path_completer = PathCompleter(
        expanduser=True, get_paths=lambda: [str(project_path)]
    )

    # Mapear la estructura de comandos con sus respectivos sub-autocompletadores
    nested_dict = {
        "context": {
            "add": project_path_completer,
            "rm": project_path_completer,
            "remove": project_path_completer,
            "ls": None,
            "list": None,
            "reset": None,
        },
        "monitor": {
            "on": None,
            "off": None,
        },
        "vanish": {
            "on": None,
            "off": None,
        },
        "commit": {
            "clear": None,
            "done": None,
            "restore": None,
            "rm": None,
            "all": None,
        },
        "story": project_path_completer,
        "images": project_path_completer,
        "transfer": WordCompleter(profiles),
    }

    # Registrar de manera plana el resto de los comandos que no tienen lógica anidada
    for cmd in commands:
        if cmd not in nested_dict:
            nested_dict[cmd] = None

    return NestedCompleter.from_nested_dict(nested_dict)


def interactive_session(api: AIStudioDriveManager, state: dict, project_path: Path):
    UI.info("Sesión interactiva iniciada. Escribe [bold]help[/] para comandos.")

    ctx = SessionContext(
        api=api,
        state=state,
        project_path=project_path,
        monitor=SnapshotManager(api, project_path, state),
    )

    def handle_exit(sig, frame):
        UI.info("Cerrando sesión de forma segura...")
        ctx.stop_monitor()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_exit)
    if sys.platform != "win32":
        signal.signal(signal.SIGTERM, handle_exit)

    ctx.start_monitor()

    UI.info(f"[Chat] Iniciando sesión con chat_id {state.get('chat_id')}...")

    # Extraer lista de comandos directamente del registro del proyecto
    commands_list = list(registry.commands.keys())
    completer = create_interactive_completer(project_path, commands_list)

    # Inicializar la sesión de prompt_toolkit con completador e historial en memoria
    session = PromptSession(completer=completer, history=InMemoryHistory())

    consecutive_errors = 0

    while True:
        try:
            command_line = session.prompt(">> ").strip()
            if not command_line:
                continue

            try:
                parts = shlex.split(command_line)
            except ValueError as e:
                UI.error(f"Sintaxis de argumentos inválida: {e}")
                continue

            if not parts:
                continue

            command_name = parts[0].lower()
            args_list = parts[1:]

            should_continue = registry.execute(command_name, ctx, args_list)
            consecutive_errors = 0

            if should_continue is False:
                break

        except (EOFError, KeyboardInterrupt):
            UI.info("Saliendo...")
            ctx.stop_monitor()
            break
        except ProjectContextError as e:
            UI.error(str(e))
            consecutive_errors += 1
            if consecutive_errors > 10:
                UI.error("Demasiados errores consecutivos. Saliendo de forma segura...")
                ctx.stop_monitor()
                sys.exit(1)
        except Exception as e:
            UI.error(f"Error inesperado de ejecución: {e}")
            consecutive_errors += 1
            if consecutive_errors > 10:
                UI.error(
                    "Demasiados errores inesperados consecutivos. Saliendo de forma segura..."
                )
                ctx.stop_monitor()
                sys.exit(1)
