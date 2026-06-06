from pathlib import Path
from typing import Optional

import typer
from typing_extensions import Annotated

from project_context.ops import create_or_update_chat, restore_chat_backup_if_exists
from project_context.profiles import ProfileManager
from project_context.services.api_drive import GoogleDriveManager
from project_context.services.auth_service import AuthService
from project_context.ui import UI
from project_context.workspace import ProjectContext


def update_command(
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
    Sincroniza los cambios del código del proyecto con Google Drive y sale de inmediato.
    """
    project_path = Path.cwd() if project_path is None else project_path

    auth = AuthService()
    profile_manager = ProfileManager()

    with ProjectContext(project_path) as workspace:
        profile_name = profile_manager.resolve_profile_name(use_profile)
        profile_config = profile_manager.load_profile_data(profile_name)

        creds = auth.authenticate(profile_config.token_path, profile_config.secret_path)

        api = GoogleDriveManager(creds)

        restore_chat_backup_if_exists(api, workspace)

        create_or_update_chat(api, workspace)

        UI.success("Sincronización de contexto completada.")
