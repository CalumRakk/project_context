import logging
from pathlib import Path
from typing import Optional

import typer
from typing_extensions import Annotated

from project_context.commands.bridge import interactive_session
from project_context.core.database import DatabaseSession
from project_context.core.profile_mg import ProfileManager
from project_context.core.project_context import ProjectContext
from project_context.core.snapshot_mg import SnapshotManager
from project_context.ops import create_or_update_chat, restore_chat_backup_if_exists
from project_context.services.api_drive import GoogleDriveManager
from project_context.services.auth_service import AuthService

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
    profile_config = profile_manager.load_profile_data(profile_name)

    creds = AuthService.authenticate(
        profile_config.token_path, profile_config.secret_path
    )
    api = GoogleDriveManager(creds)

    with ProjectContext(profile_config.email, project_path) as workspace:
        with DatabaseSession(workspace):
            snapshot_mgr = SnapshotManager(api, workspace)
            snapshot_mgr.initialize_schema()

            restore_chat_backup_if_exists(api, workspace)

            create_or_update_chat(api, workspace)

            interactive_session(api, workspace)
