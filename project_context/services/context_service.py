import json
import logging

from project_context.core.project_context import ProjectContext
from project_context.core.schemas import (
    ChatIAStudio,
    Context,
    ContextRemote,
)
from project_context.services.api_drive import (
    ChunkFactory,
    GoogleDriveManager,
)
from project_context.ui import UI

logger = logging.getLogger(__name__)


class ContextService:
    """Servicio encargado de sincronizar y gestionar el contexto del proyecto en Google Drive."""

    def __init__(self, api: GoogleDriveManager, project_context: ProjectContext):
        self.api = api
        self.project_context = project_context

    def build_filename_chat(self) -> str:
        """Genera el nombre estándar para el archivo de chat en Drive."""
        return self.project_context.project_path.name + "_chat.prompt"

    def create_context_document(self, filename: str, context: Context) -> ContextRemote:
        """Crea el documento de contexto en Google Drive."""
        mimetype = "text/plain"
        file = self.api.create_file(
            folder_id=self.api.ai_studio_folder,
            file_name=filename,
            content=context.text,
            mime_type=mimetype,
        )
        return ContextRemote(context=context, file_id=file.id)

    def update_context_document(self, context: Context, file_id: str) -> ContextRemote:
        """Actualiza el documento de contexto existente en Google Drive."""
        mimetype = "text/plain"
        file = self.api.update_file(file_id, context.text, mimetype)
        return ContextRemote(context=context, file_id=file.id)

    def create_or_update_chat(self) -> None:
        """
        Sincroniza el contexto local con Google Drive. Crea o actualiza tanto
        el archivo de contexto como el de la sesión de chat activa.
        """
        folder_id = self.api.ai_studio_folder

        if not self.api.can_access_file(folder_id):
            raise ValueError(
                f"No se pudo acceder a la carpeta {folder_id} en Google Drive."
            )

        state = self.project_context.load_state()
        filename = self.build_filename_chat()
        context = self.project_context.generate_context()

        # 1. Gestionar documento de contexto maestro
        if state.file_id is None or not self.api.can_access_file(state.file_id):
            context_remote = self.create_context_document(filename, context)
            state.file_id = context_remote.file_id
            state.save()
        else:
            context_remote = self.update_context_document(context, state.file_id)

        # 2. Gestionar sesión de chat de AI Studio
        if state.chat_id is None or not self.api.can_access_file(state.chat_id):
            chat_filename = self.build_filename_chat()
            initial_chat = ChunkFactory.build_initial_chat(context_remote)
            file = self.api.create_chat(
                folder_id=folder_id, file_name=chat_filename, chat_data=initial_chat
            )
            state.chat_id = file.id
            state.save()
            UI.success(f"Se creó nuevo Chat ID: [dim]{state.chat_id}[/]")
        else:
            chat = self.api.get_chat(state.chat_id)
            chat.reset_context_document_tokencount()
            self.api.update_chat(state.chat_id, chat)
            UI.success("Contexto del Chat actualizado.")

    def restore_backup_if_exists(self) -> bool:
        """Restaura el chat original si se detecta un archivo de respaldo local."""
        backup_path = self.project_context.local_dir / "chat_backup.prompt"

        if not backup_path.exists():
            return False

        state = self.project_context.load_state()
        if state.chat_id is None:
            UI.info("No se encontró un chat_id para restaurar el modo commit.")
            return False

        UI.info(
            "Se detectó respaldo del chat de un modo commit anterior. Restaurando chat original..."
        )

        content = backup_path.read_text(encoding="utf-8")
        chat_data = ChatIAStudio(**json.loads(content))
        chat_data.reset_context_document_tokencount()
        self.api.update_chat(state.chat_id, chat_data)

        backup_path.unlink()

        UI.success("Chat restaurado con éxito.")
        return True
