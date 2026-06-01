from pathlib import Path
from typing import Optional

import typer
from typing_extensions import Annotated

from project_context.api_drive import AIStudioDriveManager
from project_context.auth_flow import verify_and_populate_context
from project_context.schema import ValidationRequirement, get_session_payload
from project_context.ui.interactive import interactive_session
from project_context.workspace import ProjectContext


def shell_command(
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
    Entra directamente a la consola interactiva (shell) omitiendo el análisis local de archivos.
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

        if state is None or not state.chat_id:
            typer.secho(
                "Error: No se encontró información del chat en el estado local.\n"
                "Por favor, ejecuta primero 'project_context run' para sincronizar tu proyecto.",
                fg=typer.colors.RED,
                bold=True,
            )
            raise typer.Exit(code=1)

        assert isinstance(api, AIStudioDriveManager), (
            "El API no ha sido inicializado correctamente."
        )
        interactive_session(api, state, project_path, workspace)
