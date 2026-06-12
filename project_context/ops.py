import json
import logging
from pathlib import Path
from typing import Optional

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
from project_context.services.git_ops import get_diff_message
from project_context.utils import (
    COMMIT_TASK_MARKER,
    UI,
)

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
    file = api.create_file(
        folder_id=api.ai_studio_folder,
        file_name=filename,
        content=context.text,
        mime_type=mimetype,
    )
    return ContextRemote(context=context, file_id=file.id)


def update_context_document(api: GoogleDriveManager, context: Context, file_id: str):
    mimetype = "text/plain"
    file = api.update_file(file_id, context.text, mimetype)
    return ContextRemote(context=context, file_id=file.id)


def create_or_update_chat(api: GoogleDriveManager, projectcontext: ProjectContext):
    folder_id = api.ai_studio_folder

    if not api.can_access_file(folder_id):
        raise ValueError(
            f"No se pudo acceder a la carpeta {folder_id} en Google Drive."
        )

    # Trabajo con el Archivo de Contexto
    state = projectcontext.load_state()
    filename = build_filename_chat(projectcontext.project_path)
    context = projectcontext.generate_context()

    if state.file_id is None or not api.can_access_file(state.file_id):
        context_remote = create_context_document(api, filename, context)
        state.file_id = context_remote.file_id
        state.save()
    else:
        update_context_document(api, context, state.file_id)

    # Trabajo con el Archivo de Chat
    if state.chat_id is None or not api.can_access_file(state.chat_id):
        chat_filename = build_filename_chat(projectcontext.project_path)
        initial_chat = ChunkFactory.build_initial_chat(context_remote)
        file = api.create_chat(
            folder_id=folder_id, file_name=chat_filename, chat_data=initial_chat
        )
        state.chat_id = file.id
        state.save()
        UI.success(f"Se creo nuevo Chat ID: [dim]{state.chat_id}[/]")
    else:
        chat = api.get_chat(state.chat_id)
        chat.reset_context_document_tokencount()
        api.update_chat(state.chat_id, chat)
        UI.success("Contexto del Chat actualizado.")


def restore_chat_backup_if_exists(
    api: GoogleDriveManager, project_context: ProjectContext
) -> bool:
    """Restaura el chat original si se detecta un archivo de respaldo local."""

    backup_path = project_context.local_dir / "chat_backup.prompt"

    if not backup_path.exists():
        return False

    state = project_context.load_state()
    if state.chat_id is None:
        # TODO: este mensaje acopla la funcion con la logica del commit. Analizar bien esto.
        UI.info("No se encontró un chat_id para restaurar el modo commit.")
        return False

    UI.info(
        "Se detectó respaldo del chat de un modo commit anterior. Restaurando chat original..."
    )

    content = backup_path.read_text(encoding="utf-8")
    chat_data = ChatIAStudio(**json.loads(content))
    chat_data.reset_context_document_tokencount()
    api.update_chat(state.chat_id, chat_data)

    backup_path.unlink()

    UI.success("Chat restaurado con éxito.")
    return True
