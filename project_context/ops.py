from pathlib import Path
from typing import Dict, List, Optional, Tuple

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
    COMMIT_TASK_MARKER,
    RESPONSE_TEMPLATE,
    UI,
    compute_md5,
    extract_image_references,
    generate_context,
    get_diff_message,
    get_filtered_files,
    has_files_modified_since,
    resolve_prompt,
    save_context,
)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def generate_commit_prompt_text(project_path: Path) -> Optional[str]:
    """
    Genera el prompt completo para la tarea de commit.
    Retorna None si no hay cambios en stage.
    """
    diff_content = get_diff_message(project_path)

    if not diff_content:
        return None

    prompt_text = (
        f"{COMMIT_TASK_MARKER}\n"
        "Actúa como un desarrollador senior con amplia experiencia en la redacción de mensajes de commit siguiendo las mejores prácticas. "
        "El archivo context_project.txt contiene el contexto del proyecto:\n\n"
        "He realizado los siguientes cambios (git diff --cached):\n\n"
        "```diff\n"
        f"{diff_content}\n"
        "```\n\n"
        "Con base en esos cambios, sugiéreme un mensaje de commit conciso, en español, que resuma de forma clara y profesional los puntos más relevantes. "
        "El mensaje debe ocupar un solo párrafo y reflejar la intención del cambio sin omitir detalles importantes.\n\n"
        "Formato deseado: <tipo>(<alcance>): <descripción>"
    )
    return prompt_text


def initialize_project_context(api: AIStudioDriveManager, project_path: Path) -> Dict:
    UI.info("Primer uso para este proyecto. [bold]Creando contexto inicial...[/]")
    # Genera el contexto y las imágenes asociadas
    chunks = []
    context_chunk, content_md5 = sync_context(api, project_path)

    prompt_chunk = ChunksText(
        text=resolve_prompt(project_path), role="user", tokenCount=None
    )
    model_chunk = ChunksText(text=RESPONSE_TEMPLATE, role="model", tokenCount=None)

    chunks.append(context_chunk)
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
    UI.success(f"Proyecto inicializado con Chat ID: [dim]{chat_id}[/]")
    return initial_state


def update_context(api: AIStudioDriveManager, project_path: Path, state: Dict) -> Dict:
    last_modified_saved = state.get("last_modified", 0)
    chat_id = state.get("chat_id")
    if not chat_id:
        raise ValueError("No se encontró 'chat_id' en el estado del proyecto.")

    context_scope = state.get("context_scope")
    target_path = project_path / context_scope if context_scope else project_path
    scope_name = context_scope if context_scope else "Raíz del proyecto"

    UI.info(f"Escaneando cambios en [blue]{scope_name}[/]...")

    if not has_files_modified_since(last_modified_saved, target_path):
        UI.success(f"El contexto ({scope_name}) está actualizado.")
        return state

    UI.info(f"Cambios detectados. Generando nuevo contexto y calculando tokens...")

    content, new_tokens = generate_context(project_path, target_path=target_path)
    path_context = save_context(project_path, content)
    current_md5 = compute_md5(path_context)

    if current_md5 == state.get("md5"):
        UI.warn("El contenido es idéntico.")
        state["last_modified"] = target_path.stat().st_mtime
        return state

    UI.info("Sincronizando archivo de texto en Drive...")
    file_id = state.get("file_id")
    assert file_id is not None
    api.gdm.update_file_from_memory(file_id, content, "text/plain")

    # ACTUALIZAR METADATOS EN EL CHAT (Tokens y Referencia)
    UI.info("Actualizando metadatos del chat (Token Count)...")
    chat_data = api.get_chat_ia_studio(chat_id)
    if chat_data:
        updated_metadata = False
        # TODO: Mejorar la forma de encontrar el chunk o centralizarla.
        for chunk in chat_data.chunkedPrompt.chunks:
            if hasattr(chunk, "driveDocument") and chunk.driveDocument.id == file_id:  # type: ignore
                chunk.tokenCount = new_tokens
                updated_metadata = True
                break

        if updated_metadata:
            api.update_chat_file(chat_id, chat_data)
            UI.info(f"Metadatos actualizados: [bold]{new_tokens}[/] tokens.")
        else:
            UI.warn(
                "No se pudo encontrar el bloque de contexto en el chat para actualizar tokens."
            )

    state["last_modified"] = target_path.stat().st_mtime
    state["md5"] = current_md5
    UI.success(f"Sincronización de enfoque ({scope_name}) completada.")

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
    Realiza un Reset del chat pero REUTILIZA los IDs de archivos existentes en Drive.
    Actualiza el contenido del context.txt y reconstruye la lista de chunks.
    """
    file_id = state.get("file_id")
    chat_id = state.get("chat_id")

    UI.info(f"Iniciando [bold red]Reset[/] del chat [dim]{chat_id}[/]...")

    if not file_id or not chat_id:
        raise ValueError(
            "No se encontraron los IDs necesarios en el estado para reconstruir."
        )

    UI.info("Generando nuevo contexto con Gitingest...")

    content, expected_tokens = generate_context(project_path)
    path_context = save_context(project_path, content)
    current_md5 = compute_md5(path_context)

    UI.info("Actualizando archivo de contexto maestro...")
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
        UI.success("¡Chat y contexto reconstruido con exito!")
    else:
        UI.error("Error crítico al guardar la reconstrucción del chat.")
        raise ValueError("Error al guardar la reconstrucción del chat.")

    state["last_modified"] = project_path.stat().st_mtime
    state["md5"] = current_md5

    return state


def find_pending_commit_tasks(chat_data: ChatIAStudio):
    chunks = chat_data.chunkedPrompt.chunks
    tasks_found = []

    for i, chunk in enumerate(chunks):
        if isinstance(chunk, ChunksText) and COMMIT_TASK_MARKER in chunk.text:
            # Hemos encontrado el mensaje enviado por el CLI
            has_response = False
            if i + 1 < len(chunks):
                next_chunk = chunks[i + 1]
                if getattr(next_chunk, "role", None) == "model":
                    has_response = True

            tasks_found.append(
                {"index": i, "has_response": has_response, "chunk": chunk}
            )
    return tasks_found


def resolve_image_paths(
    project_path: Path,
    source_file_rel_path: str,
    media_root_hint: Optional[Path] = None,
) -> Tuple[List[Path], List[str]]:
    """
    Dada una ruta de archivo fuente (ej: README.md), extrae referencias a imágenes
    e intenta resolver sus rutas absolutas.

    Retorna:
        - List[Path]: Lista de imágenes encontradas y existentes.
        - List[str]: Lista de nombres/referencias que NO se pudieron encontrar.
    """
    target_file = project_path / source_file_rel_path
    if not target_file.exists():
        raise FileNotFoundError(f"El archivo {source_file_rel_path} no existe.")

    refs = extract_image_references(target_file)
    if not refs:
        return [], []

    found_paths = []
    missing_refs = []

    for ref_text, is_wiki in refs:
        # Relativo al archivo fuente
        candidate = (target_file.parent / ref_text).resolve()

        # Si es WikiLink y falló, probar con el hint (carpeta de medios)
        if not candidate.exists() and is_wiki and media_root_hint:
            candidate = (media_root_hint / ref_text).resolve()

        # Validación final
        if candidate.exists() and candidate.is_file():
            if candidate not in found_paths:
                found_paths.append(candidate)
        else:
            missing_refs.append(ref_text)

    return found_paths, missing_refs
