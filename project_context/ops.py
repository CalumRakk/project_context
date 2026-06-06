import logging
from pathlib import Path
from typing import Optional

from project_context.schema import (
    ChunksDocument,
    Context,
    ContextRemote,
)
from project_context.services.api_drive import (
    ChunkFactory,
    GoogleDriveManager,
)
from project_context.services.git_ops import get_diff_message
from project_context.utils import (
    COMMIT_TASK_MARKER,
    UI,
)
from project_context.workspace import ProjectContext

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
logger = logging.getLogger(__name__)


def generate_commit_prompt_text(project_path: Path) -> Optional[str]:
    """Genera el prompt completo para la tarea de commit."""
    diff_content = get_diff_message(project_path)

    if not diff_content:
        return None

    # Prependemos el marcador estándar de commit
    prompt_text = (
        f"{COMMIT_TASK_MARKER}\n\n"
        "Actúa como un desarrollador senior con amplia experiencia en la redacción de mensajes de commit siguiendo las mejores prácticas Conventional Commits. "
        "Tienes adjunto a este chat el contexto del proyecto para que entiendas la arquitectura general.\n\n"
        "He realizado los siguientes cambios (git diff --cached):\n\n"
        "```diff\n"
        f"{diff_content}\n"
        "```\n\n"
        "Con base en esos cambios, sugiéreme un único mensaje de commit conciso, en español, que resuma de forma clara y profesional los puntos más relevantes. "
        "No me des explicaciones, solo devuélveme el mensaje final listo para copiar y pegar. \n"
        "Formato deseado: <tipo>(<alcance>): <descripción>"
    )
    return prompt_text


def build_filename_chat(project_path: Path) -> str:
    # TODO: buscar una buena ubicacion.
    return project_path.name + "_chat.prompt"


def create_context_document(
    api: GoogleDriveManager, filename: str, context: Context
) -> ContextRemote:
    """Crea el documento de contexto en google Drive y devuelve objeto."""

    mimetype = "text/plain"
    file_id = api.create_file_from_memory(
        folder_id=api.ai_studio_folder,
        file_name=filename,
        content=context.text,
        mime_type=mimetype,
    )
    return ContextRemote(context=context, file_id=file_id)


def update_context_document(api: GoogleDriveManager, context: Context, file_id: str):
    mimetype = "text/plain"
    file_id = api.update_file_from_memory(file_id, context.text, mimetype)
    return ContextRemote(context=context, file_id=file_id)


def create_or_update_chat(api: GoogleDriveManager, projectcontext: ProjectContext):
    folder_id = api.ai_studio_folder

    if not api.can_access_file(folder_id):
        raise ValueError(
            f"No se pudo acceder a la carpeta {folder_id} en Google Drive."
        )

    # Trabajo con el Archivo de Contexto
    file_id = projectcontext.file_id
    filename = build_filename_chat(projectcontext.project_path)
    context = projectcontext.generate_context()

    if not api.can_access_file(projectcontext.file_id):
        context_remote = create_context_document(api, filename, context)
        file_id_final = context_remote.file_id
    else:
        context_remote = update_context_document(api, context, file_id)
        file_id_final = file_id

    # Trabajo con el Archivo de Chat
    chat_id = projectcontext.chat_id
    if api.can_access_file(chat_id):
        chat = api.get_chat(chat_id)
        for chunk in chat.chunkedPrompt.chunks:
            if isinstance(chunk, ChunksDocument) and chunk.file_id == file_id:
                chunk.tokenCount = None  # type: ignore - Fuerza el recuento de tokens
                break
        api.update_chat(chat_id, chat)
        chat_id_final = chat_id
        UI.success(f"Proyecto actualizado con Chat ID: [dim]{chat_id}[/]")
    else:
        chat_filename = build_filename_chat(projectcontext.project_path)
        initial_chat = ChunkFactory.build_initial_chat(context_remote)
        chat_id_final = api.create_chat(
            folder_id=folder_id, file_name=chat_filename, chat_data=initial_chat
        )
        UI.success(f"Proyecto inicializado con Chat ID: [dim]{chat_id}[/]")

    projectcontext.update_state(
        chat_id=chat_id_final,
        file_id=file_id_final,
        file_md5=context_remote.context.md5sum,
    )


def restore_chat_backup_if_exists(
    api: GoogleDriveManager, workspace: ProjectContext
) -> bool:
    """Restaura el chat original si se detecta un archivo de respaldo local."""
    backup_path = workspace.local_dir / "chat_backup.prompt"
    if backup_path.exists():
        try:
            import json

            from project_context.schema import ChatIAStudio

            UI.info(
                "Detectado respaldo de chat local. Restaurando sesión original en Drive..."
            )
            content = backup_path.read_text(encoding="utf-8")
            chat_data = ChatIAStudio(**json.loads(content))
            api.update_chat(workspace.chat_id, chat_data)
            backup_path.unlink()
            UI.success("Sesión original restaurada en Drive con éxito.")
            return True
        except Exception as e:
            UI.error(f"No se pudo auto-restaurar el chat de respaldo: {e}")
    return False
