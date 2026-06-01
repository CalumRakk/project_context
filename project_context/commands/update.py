from pathlib import Path
from typing import Optional

import typer
from typing_extensions import Annotated

from project_context.api_drive import AIStudioDriveManager
from project_context.auth_flow import verify_and_populate_context
from project_context.ops import initialize_project_context, update_context
from project_context.schema import ValidationRequirement, get_session_payload
from project_context.ui.ui import UI
from project_context.workspace import ProjectContext


def update_command(
    ctx: typer.Context,
    use_profile: Annotated[
        Optional[str],
        typer.Option(
            "--use",
            help="Usa un perfil específico temporalmente para esta ejecución.",
        ),
    ] = None,
):
    """
    Sincroniza los cambios del código del proyecto con Google Drive y sale de inmediato.
    """
    project_path = Path.cwd()

    with ProjectContext(project_path) as workspace:
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
            state = initialize_project_context(api, workspace)
        else:
            state = update_context(api, workspace, state)

        workspace.save_project_context_state(state)
        UI.success("Sincronización de contexto completada con éxito.")
