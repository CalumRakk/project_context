from pathlib import Path
from typing import Optional

import typer
from filelock import FileLock, Timeout
from typing_extensions import Annotated

from project_context.api_drive import AIStudioDriveManager
from project_context.ui.interactive import interactive_session
from project_context.utils import (
    UI,
    get_local_context_dir,
    load_project_context_state,
    profile_manager,
)


def shell_command(
    use_profile: Annotated[
        Optional[str],
        typer.Option(
            "--use", help="Usa un perfil específico temporalmente para esta ejecución."
        ),
    ] = None,
):
    """
    Entra directamente a la consola interactiva (shell) omitiendo el análisis local de archivos.
    """
    project_path = Path.cwd()
    local_dir = project_path / ".project_context"
    state_path = local_dir / "state.json"

    # Shell requiere una sesión previamente inicializada, de lo contrario abortamos con error descriptivo
    if not state_path.exists():
        typer.secho(
            "Error: Este directorio no ha sido inicializado como un proyecto de project_context.\n"
            "Por favor, ejecuta primero 'project_context run' o 'project_context update' para inicializarlo.",
            fg=typer.colors.RED,
            bold=True,
        )
        raise typer.Exit(code=1)

    local_dir = get_local_context_dir(project_path)
    lock_path = local_dir / "app.lock"

    lock = FileLock(lock_path, timeout=0)

    try:
        with lock:
            if use_profile:
                available_profiles = profile_manager.list_profiles()
                if use_profile not in available_profiles:
                    typer.secho(
                        f"Error: El perfil de usuario '{use_profile}' no existe.",
                        fg=typer.colors.RED,
                    )
                    typer.echo(f"Perfiles disponibles: {', '.join(available_profiles)}")
                    raise typer.Exit(code=1)

                profile_manager.set_temporary_profile(use_profile)
                typer.secho(
                    f"Usando perfil temporal: {use_profile}", fg=typer.colors.YELLOW
                )

            try:
                api = AIStudioDriveManager()
            except Exception as e:
                typer.secho(f"Error inicializando Drive: {e}", fg=typer.colors.RED)
                raise typer.Exit(code=1)

            state = load_project_context_state(project_path)

            if state is None or not state.get("chat_id"):
                typer.secho(
                    "Error: No se encontró información del chat en el estado local.\n"
                    "Por favor, ejecuta primero 'project_context run' para sincronizar tu proyecto.",
                    fg=typer.colors.RED,
                )
                raise typer.Exit(code=1)

            interactive_session(api, state, project_path)

    except Timeout:
        UI.error(
            "Ya existe una instancia de project_context operando activamente en este proyecto.\n"
            "Por favor, cierra la sesión abierta en la otra terminal antes de iniciar una nueva."
        )
        raise typer.Exit(code=1)
