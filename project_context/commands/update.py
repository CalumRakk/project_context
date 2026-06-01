from pathlib import Path
from typing import Optional

import typer
from filelock import FileLock, Timeout
from typing_extensions import Annotated

from project_context.api_drive import AIStudioDriveManager
from project_context.auth_flow import verify_and_populate_context
from project_context.ops import initialize_project_context, update_context
from project_context.schema import ValidationRequirement, get_session_payload
from project_context.utils import (
    UI,
    get_local_context_dir,
    save_project_context_state,
)


def update_command(
    ctx: typer.Context,
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
            verify_and_populate_context(
                ctx,
                requirement=ValidationRequirement.FULL_AUTH,
                profile_override=use_profile,
            )

            payload = get_session_payload(ctx)
            api = payload.api
            state = payload.state

            assert isinstance(api, AIStudioDriveManager), (
                "El API no ha sido inicializado correctamente."
            )
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
