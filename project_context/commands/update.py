from pathlib import Path
from typing import Optional

import typer
from typing_extensions import Annotated

from project_context.core.profile_mg import ProfileManager
from project_context.core.project_context import ProjectContext
from project_context.core.story_ops import apply_story_update
from project_context.services.api_drive import GoogleDriveManager
from project_context.services.auth_service import AuthService
from project_context.services.context_service import ContextService
from project_context.ui import UI


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

    profile_manager = ProfileManager()
    profile_name = profile_manager.resolve_profile_name(use_profile)
    profile = profile_manager.load_profile_data(profile_name)

    creds = AuthService.authenticate(profile.token_path, profile.secret_path)
    api = GoogleDriveManager(creds)

    with ProjectContext(profile.email, project_path) as projectcontext:
        context_service = ContextService(api, projectcontext)
        context_service.restore_backup_if_exists()

        anchor_file_path = projectcontext.local_dir / "story_anchor.txt"
        if anchor_file_path.exists():
            story_anchor_rel = anchor_file_path.read_text(encoding="utf-8").strip()
            UI.info(
                "Modo historia activo detectado en el proyecto. Reconstruyendo prompt del chat..."
            )

            apply_story_update(api, projectcontext, story_anchor_rel)
        else:
            context_service.create_or_update_chat()

        UI.success("Sincronización de contexto completada.")
