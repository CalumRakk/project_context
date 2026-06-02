import logging
from pathlib import Path
from typing import Optional

import typer
from typing_extensions import Annotated

from project_context.auth_service import AuthService
from project_context.ops import initialize_project_context, update_context
from project_context.ui.interactive import interactive_session
from project_context.workspace import ProjectContext

logger = logging.getLogger(__name__)


def run_command(
    use_profile: Annotated[
        Optional[str],
        typer.Option(
            "--use",
            help="Usa un perfil específico temporalmente para esta ejecución.",
        ),
    ] = None,
):
    """
    Sincroniza el proyecto actual con Google Drive e inicia la sesión interactiva (shell).
    """
    project_path = Path.cwd()

    with ProjectContext(project_path) as workspace:
        session = AuthService.initialize_session(
            profile_override=use_profile,
            project_path=project_path,
        )

        api = session.api
        state = session.state

        if state is None:
            state = initialize_project_context(api, workspace)
        else:
            state = update_context(api, workspace, state)

        workspace.save_project_context_state(state)
        interactive_session(api, state, workspace)
