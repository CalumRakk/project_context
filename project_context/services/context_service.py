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

    def build_filename_context(self) -> str:
        """Genera el nombre estándar para el archivo de contexto en Drive."""
        return self.project_context.project_path.name + "_context.txt"

    def create_context_document(self, filename: str, context: Context) -> ContextRemote:
        mimetype = "text/plain"
        file = self.api.create_file(
            folder_id=self.api.ai_studio_folder,
            file_name=filename,
            content=context.text,
            mime_type=mimetype,
        )
        return ContextRemote(context=context, file_id=file.id)

    def update_context_document(self, context: Context, file_id: str) -> ContextRemote:
        mimetype = "text/plain"
        file = self.api.update_file(file_id, context.text, mimetype)
        return ContextRemote(context=context, file_id=file.id)

    def create_or_update_chat(self) -> None:
        folder_id = self.api.ai_studio_folder
        state = self.project_context.load_state()
        context_filename = self.build_filename_context()

        logger.info("Generando contexto unificado del proyecto...")
        context = self.project_context.generate_context()
        logger.info(
            f"Contexto generado: {context.token_count} tokens aprox. | MD5: {context.md5sum}"
        )

        # Asegurar Documento de contexto
        if state.file_id is None or not self.api.can_access_file(state.file_id):
            logger.info("Creando nuevo documento de contexto en Drive...")
            context_remote = self.create_context_document(context_filename, context)
            state.file_id = context_remote.file_id
            state.save()
            logger.info(f"Documento de contexto creado con ID: {state.file_id}")
        else:
            logger.info(
                f"Actualizando documento de contexto existente: {state.file_id}"
            )
            context_remote = self.update_context_document(context, state.file_id)

        # Asegurar Chat de AI Studio
        if state.chat_id is None or not self.api.can_access_file(state.chat_id):
            logger.info("Creando nuevo Chat en Drive...")
            chat_filename = self.build_filename_chat()
            initial_chat = ChunkFactory.build_initial_chat(context_remote)
            file = self.api.create_chat(
                folder_id=folder_id, file_name=chat_filename, chat_data=initial_chat
            )
            state.chat_id = file.id
            state.save()
            logger.info(f"Nuevo Chat creado en Drive: {state.chat_id}")
            UI.success(f"Se creó nuevo Chat ID: [dim]{state.chat_id}[/]")
        else:
            logger.info(f"Verificando y sincronizando Chat activo: {state.chat_id}")
            chat = self.api.get_chat(state.chat_id)

            # --- CORRECCIÓN CRÍTICA AQUÍ ---
            # Asegurar que el chunk de contexto exista y apunte a state.file_id
            context_chunk_found = False
            for chunk in chat.chunkedPrompt.chunks:
                if chunk.is_document or hasattr(chunk, "driveDocument"):
                    chunk.file_id = state.file_id
                    chunk.tokenCount = context.token_count
                    context_chunk_found = True
                    break

            if not context_chunk_found:
                # Si por alguna razón el chat no tenía el documento incrustado, se inserta al inicio
                new_chunk = ChunkFactory.create_file(
                    state.file_id, role="user", tokens=context.token_count
                )
                chat.chunkedPrompt.chunks.insert(0, new_chunk)

            self.api.update_chat(state.chat_id, chat)
            UI.success("Contexto del Chat actualizado y sincronizado.")

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
