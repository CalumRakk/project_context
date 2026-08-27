import shlex
import sys
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import NestedCompleter, PathCompleter, WordCompleter
from prompt_toolkit.history import InMemoryHistory

from project_context.commands.interactive import bootstrap_interactive_registry
from project_context.commands.interactive.register import (
    InteractiveRegistry,
    SessionContext,
)
from project_context.core.exceptions import ProjectContextError
from project_context.core.profile_mg import ProfileManager
from project_context.core.project_context import ProjectContext
from project_context.services.api_drive import GoogleDriveManager
from project_context.ui import UI


def create_interactive_completer(
    project_path: Path, registry: InteractiveRegistry
) -> NestedCompleter:
    """Construye un completador jerárquico dinámico a partir de las opciones

    y argumentos declarados en el registro interactivo.
    """
    profile_manager = ProfileManager()
    profiles = profile_manager.list_profiles()

    project_path_completer = PathCompleter(
        expanduser=True, get_paths=lambda: [str(project_path)]
    )
    profile_completer = WordCompleter(profiles)

    nested_dict = {}

    for primary_name, meta in registry.get_commands_map().items():
        cmd_branch = {}

        # Mapear opciones y flags fijas del comando
        for opt in meta.options:
            for name in opt.names:
                cmd_branch[name] = None

        # Resolver y asociar completadores posicionales dinámicos
        positional_completer = None
        for arg in meta.arguments:
            if arg.completer_type == "path":
                positional_completer = project_path_completer
            elif arg.completer_type == "profile":
                positional_completer = profile_completer
            elif arg.completer_type == "snapshot":
                try:
                    from project_context.core.database import DatabaseSession, Snapshot

                    active_email = profile_manager.get_active_profile_name()
                    if active_email:
                        profile_data = profile_manager.load_profile_data(active_email)
                        proj_ctx = ProjectContext(profile_data.email, project_path)
                        with DatabaseSession(proj_ctx):
                            snaps = [
                                str(s.id)
                                for s in Snapshot.select(Snapshot.id).order_by(
                                    Snapshot.id.desc()
                                )
                            ]
                        positional_completer = WordCompleter(snaps)
                except Exception:
                    pass

        if positional_completer:
            if not cmd_branch:
                nested_dict[primary_name] = positional_completer
            else:
                for key in list(cmd_branch.keys()):
                    cmd_branch[key] = positional_completer
                nested_dict[primary_name] = cmd_branch
        else:
            nested_dict[primary_name] = cmd_branch if cmd_branch else None

    # AMPLIACIÓN DE AUTOCOMPLETADO ANIDADO PARA CONTEXTO
    context_completions = {
        # Enfoque local y excepciones
        "set": project_path_completer,
        "add": project_path_completer,
        "remove": project_path_completer,
        "rm": project_path_completer,
        # Filtros de ruido y poda
        "exclude": project_path_completer,
        "unexclude": None,
        # Paquetes externos
        "link": None,
        "unlink": None,
        # Control e inspección
        "status": None,
        "tree": None,
        "reset": None,
        "clear": None,
    }
    nested_dict["context"] = context_completions
    nested_dict["ctx"] = context_completions

    return NestedCompleter.from_nested_dict(nested_dict)


def interactive_session(
    api: GoogleDriveManager,
    project_context: ProjectContext,
):

    ctx = SessionContext(api=api, project_context=project_context)

    state = project_context.load_state()

    url = f"https://aistudio.google.com/prompts/{state.chat_id}"
    UI.success(f"Chat activo: {url}")
    UI.info("Escribe [green]help[/] para comandos.")
    UI.info("Escribe [green]update[/] para sincronizar los cambios con Drive.")

    registry = bootstrap_interactive_registry()
    completer = create_interactive_completer(project_context.project_path, registry)

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
