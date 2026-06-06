import shlex
import sys
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import NestedCompleter, PathCompleter, WordCompleter
from prompt_toolkit.history import InMemoryHistory

from project_context.commands.interactive import bootstrap_interactive_registry
from project_context.commands.interactive.register import SessionContext
from project_context.exceptions import ProjectContextError
from project_context.profiles import ProfileManager
from project_context.services.api_drive import GoogleDriveManager
from project_context.ui import UI
from project_context.workspace import ProjectContext


def create_interactive_completer(
    project_path: Path, commands: list[str]
) -> NestedCompleter:
    """
    Construye un completador jerárquico dinámico a partir de las claves del registro.
    """
    profile_manager = ProfileManager()
    profiles = profile_manager.list_profiles()
    project_path_completer = PathCompleter(
        expanduser=True, get_paths=lambda: [str(project_path)]
    )

    nested_dict = {}

    # Agrupar subcomandos definidos por namespace (separados por ':')
    for cmd_name in commands:
        if ":" in cmd_name:
            parent, sub = cmd_name.split(":", 1)
            if parent not in nested_dict or not isinstance(nested_dict[parent], dict):
                nested_dict[parent] = {}

            if parent in ["context", "story", "images"] and sub in [
                "add",
                "rm",
                "remove",
            ]:
                nested_dict[parent][sub] = project_path_completer
            else:
                nested_dict[parent][sub] = None

    for cmd_name in commands:
        if ":" not in cmd_name:
            if cmd_name not in nested_dict:
                if cmd_name in ["story", "images"]:
                    nested_dict[cmd_name] = project_path_completer
                elif cmd_name == "transfer":
                    nested_dict[cmd_name] = WordCompleter(profiles)
                else:
                    nested_dict[cmd_name] = None

    return NestedCompleter.from_nested_dict(nested_dict)


def interactive_session(api: GoogleDriveManager, workspace: ProjectContext):
    ctx = SessionContext(api=api, workspace=workspace)
    chat_id = workspace.chat_id

    from project_context.ops import restore_chat_backup_if_exists

    restore_chat_backup_if_exists(api, workspace)

    url = f"https://aistudio.google.com/prompts/{chat_id}"
    UI.success(f"Chat iniciado: {url}")
    UI.info("Escribe [green]help[/] para comandos.")
    UI.info("Escribe [green]update[/] para sincronizar los cambios con Drive.")

    registry = bootstrap_interactive_registry()
    commands_list = list(registry._commands.keys())
    completer = create_interactive_completer(workspace.project_path, commands_list)

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
            break
        except ProjectContextError as e:
            UI.error(str(e))
            consecutive_errors += 1
            if consecutive_errors > 10:
                UI.error("Demasiados errores consecutivos. Saliendo de forma segura...")
                sys.exit(1)
        except Exception as e:
            UI.error(f"Error inesperado de ejecución: {e}")
            consecutive_errors += 1
            if consecutive_errors > 10:
                UI.error(
                    "Demasiados inesperados errores consecutivos. Saliendo de forma segura..."
                )
                sys.exit(1)
