from pathlib import Path
from typing import Optional

import typer
from filelock import FileLock, Timeout
from typing_extensions import Annotated

from project_context.api_drive import AIStudioDriveManager
from project_context.ops import initialize_project_context, update_context
from project_context.utils import (
    UI,
    get_local_context_dir,
    load_project_context_state,
    profile_manager,
    safe_verify_profile,
    save_project_context_state,
)


def update_command(
    use_profile: Annotated[
        Optional[str],
        typer.Option(
            "--use", help="Usa un perfil específico temporalmente para esta ejecución."
        ),
    ] = None,
):
    """
    Sincroniza los cambios del código del proyecto con Google Drive y sale de inmediato.
    """
    project_path = Path.cwd()
    local_dir = project_path / ".project_context"
    state_path = local_dir / "state.json"

    is_initialized = state_path.exists()

    if not is_initialized:
        confirm = typer.confirm(
            "Este directorio no ha sido inicializado como un proyecto de project_context.\n"
            "¿Deseas inicializar un nuevo contexto de proyecto en la ruta actual?",
            default=True,
        )
        if not confirm:
            UI.info("Operación cancelada.")
            raise typer.Exit()

    local_dir = get_local_context_dir(project_path)
    lock_path = local_dir / "app.lock"

    lock = FileLock(lock_path, timeout=0)

    try:
        with lock:
            target_profile = use_profile or profile_manager.get_active_profile_name()

            safe_verify_profile(target_profile)

            if use_profile:
                profile_manager.set_temporary_profile(use_profile)
                UI.info(f"Usando perfil temporal: [bold]{use_profile}[/]")

            try:
                api = AIStudioDriveManager()
            except Exception as e:
                UI.error(f"Error inicializando Drive: {e}")
                raise typer.Exit(code=1)

            state = load_project_context_state(project_path)

            if state is None:
                state = initialize_project_context(api, project_path)
            else:
                state = update_context(api, project_path, state)

            save_project_context_state(project_path, state)
            UI.success("Sincronización de contexto completada con éxito.")

    except Timeout:
        UI.error(
            "Ya existe una instancia de project_context operando activamente en este proyecto.\n"
            "Por favor, cierra la sesión abierta en la otra terminal antes de iniciar una nueva."
        )
        raise typer.Exit(code=1)
