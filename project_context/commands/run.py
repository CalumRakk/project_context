import logging
from pathlib import Path
from typing import Optional

import typer
from typing_extensions import Annotated

from project_context.commands.bridge import interactive_session
from project_context.core.database import DatabaseSession
from project_context.core.profile_mg import ProfileManager
from project_context.core.project_context import ProjectContext
from project_context.services.api_drive import GoogleDriveManager
from project_context.services.auth_service import AuthService
from project_context.services.context_service import ContextService

logger = logging.getLogger(__name__)


def run_command(
    project_path: Annotated[
        Optional[Path],
        typer.Option(
            "--project-path",
            help="Ruta del proyecto a sincronizar con Google Drive.",
        ),
    ] = None,
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
    project_path = Path.cwd() if project_path is None else project_path

    profile_manager = ProfileManager()
    profile_name = profile_manager.resolve_profile_name(use_profile)
    profile = profile_manager.load_profile_data(profile_name)

    creds = AuthService.authenticate(profile.token_path, profile.secret_path)
    api = GoogleDriveManager(creds)

    with ProjectContext(profile.email, project_path) as projectcontext:
        with DatabaseSession(projectcontext):
            context_service = ContextService(api, projectcontext)
            context_service.restore_backup_if_exists()
            context_service.create_or_update_chat()

            interactive_session(api, projectcontext)
