from pathlib import Path
from typing import Optional

import typer
from typing_extensions import Annotated

from project_context.auth_service import AuthService
from project_context.ui.interactive import interactive_session
from project_context.ui.presenters import AuthConsolePresenter
from project_context.workspace import ProjectContext


def shell_command(
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
        try:
            session = AuthService.initialize_session(
                profile_override=use_profile,
                project_path=project_path,
            )
        except Exception as e:
            AuthConsolePresenter.handle_error(e)

        api = session.api
        state = session.state

        if state is None or not state.chat_id:
            typer.secho(
                "Error: No se encontró información del chat en el estado local.\n"
                "Por favor, ejecuta primero 'project_context run' para sincronizar tu proyecto.",
                fg=typer.colors.RED,
                bold=True,
            )
            raise typer.Exit(code=1)

        interactive_session(api, state, workspace)
