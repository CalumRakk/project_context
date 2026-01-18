from pathlib import Path
from typing import Dict, Optional, Tuple

from project_context.api_drive import AIStudioDriveManager
from project_context.schema import (
    ChatIAStudio,
    ChunkedPrompt,
    ChunksDocument,
    ChunksImage,
    ChunksText,
    DriveDocument,
    RunSettings,
    SystemInstruction,
)
from project_context.utils import (
    RESPONSE_TEMPLATE,
    compute_md5,
    generate_context,
    get_filtered_files,
    has_files_modified_since,
    resolve_prompt,
    save_context,
)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def initialize_project_context(api: AIStudioDriveManager, project_path: Path) -> Dict:
    print("Primer uso para este proyecto. Creando contexto inicial...")
    # Genera el contexto y las imágenes asociadas
    chunks = []
    context_chunk, content_md5 = sync_context(api, project_path)
    media_chunks = sync_images(api, project_path)

    prompt_chunk = ChunksText(
        text=resolve_prompt(project_path), role="user", tokenCount=None
    )
    model_chunk = ChunksText(text=RESPONSE_TEMPLATE, role="model", tokenCount=None)

    chunks.append(context_chunk)
    chunks.extend(media_chunks)
    chunks.append(prompt_chunk)
    chunks.append(model_chunk)

    # Crea el chat en AI Studio
    chat_data = ChatIAStudio(
        runSettings=RunSettings(),
        systemInstruction=SystemInstruction(),
        chunkedPrompt=ChunkedPrompt(
            chunks=chunks,
            pendingInputs=[],
        ),
    )

    chat_filename = project_path.name + "_chat.prompt"
    chat_id = api.create_chat_file(file_name=chat_filename, chat_data=chat_data)
    if not chat_id:
        raise ValueError("No se pudo crear el chat en Google Drive.")

    # Define el estado inicial del proyecto
    initial_state = {
        "path": str(project_path),
        "last_modified": project_path.stat().st_mtime,
        "md5": content_md5,
        "chat_id": chat_id,
        "file_id": context_chunk.driveDocument.id,
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


def sync_context(
    api: AIStudioDriveManager, project_path: Path
) -> Tuple[ChunksDocument, str]:
    content, expected_tokens = generate_context(project_path)
    path_context = save_context(project_path, content)
    content_md5 = compute_md5(path_context)

    # Sube el project_context.txt
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
    drive_document = DriveDocument(id=document["id"])
    chat_file = ChunksDocument(
        driveDocument=drive_document, role="user", tokenCount=expected_tokens
    )
    return chat_file, content_md5


def sync_images(
    api, project_path: Path, specific_files: Optional[list[Path]] = None
) -> list:
    """Sincroniza imágenes específicas o todo el proyecto."""
    if specific_files is None:
        valid_images = get_filtered_files(project_path, IMAGE_EXTENSIONS)
    else:
        valid_images = [f for f in specific_files if f.exists()]

    media_chunks = []
    for img_path in valid_images:
        rel_path = img_path.relative_to(project_path)
        drive_name = f"ctx_{img_path.name}"

        drive_file = api.gdm.find_item_by_name(
            drive_name, parent_id=api.ai_studio_folder
        )
        if not drive_file:
            with open(img_path, "rb") as f:
                content = f.read()
                mime = f"image/{img_path.suffix[1:].replace('jpg', 'jpeg')}"
                drive_file = api.gdm.upload_binary_to_drive(
                    api.ai_studio_folder, drive_name, content, mime
                )

        if drive_file:
            media_chunks.append(
                ChunksText(text=f"Archivo visual: {rel_path}", role="user")
            )
            media_chunks.append(
                ChunksImage(driveImage=DriveDocument(id=drive_file["id"]), role="user")
            )
    return media_chunks


def rebuild_project_context(
    api: AIStudioDriveManager, project_path: Path, state: Dict
) -> Dict:
    """
    Realiza un Hard Reset del chat pero REUTILIZA los IDs de archivos existentes en Drive.
    Actualiza el contenido del context.txt y reconstruye la lista de chunks.
    """
    file_id = state.get("file_id")
    chat_id = state.get("chat_id")

    if not file_id or not chat_id:
        raise ValueError(
            "No se encontraron los IDs necesarios en el estado para reconstruir."
        )

    print(f"Reinciciando chat {chat_id} y actualizando contexto {file_id}...")

    content, expected_tokens = generate_context(project_path)
    path_context = save_context(project_path, content)
    current_md5 = compute_md5(path_context)

    print("Actualizando archivo de contexto en Drive...")
    api.gdm.update_file_from_memory(file_id, content, "text/plain")

    context_chunk = ChunksDocument(
        driveDocument=DriveDocument(id=file_id), role="user", tokenCount=expected_tokens
    )
    prompt_chunk = ChunksText(
        text=resolve_prompt(project_path), role="user", tokenCount=None
    )
    model_chunk = ChunksText(text=RESPONSE_TEMPLATE, role="model", tokenCount=None)

    # Reconstruimos la lista de chunks desde cero
    new_chunks = []
    new_chunks.append(context_chunk)
    new_chunks.append(prompt_chunk)
    new_chunks.append(model_chunk)

    # Descargar el Chat actual
    chat_data = api.get_chat_ia_studio(chat_id)
    if not chat_data:
        raise ValueError("No se pudo recuperar el chat de Drive.")

    chat_data.chunkedPrompt.chunks = new_chunks
    chat_data.chunkedPrompt.pendingInputs = []

    # 6. Guardar el chat actualizado en Drive
    if api.update_chat_file(chat_id, chat_data):
        print("¡Hard Reset completado con éxito!")
    else:
        raise ValueError("Error al guardar la reconstrucción del chat.")

    state["last_modified"] = project_path.stat().st_mtime
    state["md5"] = current_md5

    return state
