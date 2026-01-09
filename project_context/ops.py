from pathlib import Path
from typing import Dict

from project_context.api_drive import AIStudioDriveManager
from project_context.schema import (
    ChatIAStudio,
    ChunkedPrompt,
    ChunksDocument,
    ChunksText,
    DriveDocument,
    RunSettings,
    SystemInstruction,
)
from project_context.utils import (
    RESPONSE_TEMPLATE,
    compute_md5,
    generate_context,
    get_custom_prompt,
    has_files_modified_since,
    save_context,
)


def initialize_project_context(api: AIStudioDriveManager, project_path: Path) -> Dict:
    print("Primer uso para este proyecto. Creando contexto inicial...")
    content, expected_tokens = generate_context(project_path)
    path_context = save_context(project_path, content)
    content_md5 = compute_md5(path_context)

    mimetype = "text/plain"
    filename = project_path.name + "_context.txt"
    document = api.gdm.create_file_from_memory(
        folder_id=api.ai_studio_folder,
        file_name=filename,
        content=content,
        mime_type=mimetype,
    )
    if not document or "id" not in document:
        raise ValueError("No se pudo crear el archivo de contexto en Google Drive.")

    prompt_text = get_custom_prompt(project_path)
    drive_document = DriveDocument(id=document["id"])
    chat_file = ChunksDocument(
        driveDocument=drive_document, role="user", tokenCount=expected_tokens
    )
    chunks_text_prompt = ChunksText(text=prompt_text, role="user", tokenCount=None)
    chunks_text_response = ChunksText(
        text=RESPONSE_TEMPLATE, role="model", tokenCount=None
    )

    chat_data = ChatIAStudio(
        runSettings=RunSettings(),
        systemInstruction=SystemInstruction(),
        chunkedPrompt=ChunkedPrompt(
            chunks=[chat_file, chunks_text_prompt, chunks_text_response],
            pendingInputs=[],
        ),
    )

    chat_filename = project_path.name + "_chat.prompt"
    chat_id = api.create_chat_file(file_name=chat_filename, chat_data=chat_data)
    if not chat_id:
        raise ValueError("No se pudo crear el chat en Google Drive.")

    initial_state = {
        "path": str(project_path),
        "last_modified": project_path.stat().st_mtime,
        "md5": content_md5,
        "chat_id": chat_id,
        "file_id": document["id"],
    }
    return initial_state


def update_context(api: AIStudioDriveManager, project_path: Path, state: Dict) -> Dict:
    last_modified_saved = state.get("last_modified", 0)
    chat_id = state.get("chat_id")
    if not chat_id:
        raise ValueError("No se encontró 'chat_id' en el estado del proyecto.")

    print(f"Revisando si el proyecto en '{project_path}' ha cambiado...")

    if not has_files_modified_since(last_modified_saved, project_path):
        print("El proyecto no ha cambiado. No se requiere actualización.")
        return state

    print("El proyecto ha cambiado. Generando nuevo contexto...")
    content, _ = generate_context(project_path)
    path_context = save_context(project_path, content)
    current_md5 = compute_md5(path_context)

    if current_md5 == state.get("md5"):
        print("El contenido es idéntico (cambios irrelevantes).")
        state["last_modified"] = project_path.stat().st_mtime
        return state

    print("El contenido ha cambiado. Actualizando en Google Drive...")
    file_id = state.get("file_id")
    if not file_id:
        raise ValueError("No se encontró 'file_id' para actualizar.")

    api.gdm.update_file_from_memory(file_id, content, "text/plain")

    state["last_modified"] = project_path.stat().st_mtime
    state["md5"] = current_md5
    print("Contexto actualizado con Éxito.")

    return state
