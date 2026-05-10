from pathlib import Path
from typing import Dict, List, Optional, Tuple

from project_context.api_drive import AIStudioDriveManager, ChunkFactory
from project_context.schema import (
    ChatIAStudio,
    ChunkedPrompt,
    ChunksDocument,
    ChunksImage,
    ChunksText,
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


def initialize_project_context(api: AIStudioDriveManager, project_path: Path) -> Dict:
    UI.info("Primer uso para este proyecto. [bold]Creando contexto inicial...[/]")
    # Genera el contexto y las imágenes asociadas
    chunks = []
    context_chunk, content_md5 = sync_context(api, project_path)

    prompt_chunk = ChunkFactory.create_text(resolve_prompt(project_path), role="user")
    model_chunk = ChunkFactory.create_text(RESPONSE_TEMPLATE, role="model")

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
    chat_id = state.get("chat_id")
    if not chat_id:
        raise ValueError("No se encontró 'chat_id' en el estado del proyecto.")

    # Obtenemos los items seleccionados
    context_items = state.get("context_items", {"files": [], "folders": []})
    has_custom_focus = bool(context_items.get("files") or context_items.get("folders"))

    scope_name = "Enfoque Específico (Stage)" if has_custom_focus else "Raíz del proyecto"

    UI.info(f"Escaneando cambios en [blue]{scope_name}[/]...")

    # Generamos el contexto usando la lógica Híbrida
    content, new_tokens = generate_context(project_path, context_items=context_items)
    path_context = save_context(project_path, content)
    current_md5 = compute_md5(path_context)

    # Si el MD5 es igual al guardado, no enviamos nada a Drive para ahorrar ancho de banda
    if current_md5 == state.get("md5"):
        UI.warn("El contenido del contexto es idéntico al actual en Drive.")
        state["last_modified"] = project_path.stat().st_mtime
        return state

    UI.info("Cambios o nuevo enfoque detectado. Actualizando contexto en Drive...")

    file_id = state.get("file_id")
    assert file_id is not None
    api.gdm.update_file_from_memory(file_id, content, "text/plain")

    UI.info("Actualizando metadatos del chat (Token Count)...")
    try:
        with api.modify_chat(chat_id) as chat_data:
            updated_metadata = False
            for chunk in chat_data.chunkedPrompt.chunks:
                if (
                    isinstance(chunk, ChunksDocument)
                    and chunk.driveDocument.id == file_id
                ):
                    chunk.tokenCount = new_tokens
                    updated_metadata = True
                    break

            if updated_metadata:
                UI.info(f"Metadatos actualizados: [bold]{new_tokens}[/] tokens.")
            else:
                UI.warn(
                    "No se pudo encontrar el bloque de contexto en el chat para actualizar tokens."
                )
    except Exception as e:
        UI.error(f"Fallo al actualizar los tokens en el chat: {e}")

    state["last_modified"] = project_path.stat().st_mtime
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

    chat_file = ChunkFactory.create_file(
        file_id=document["id"], role="user", tokens=expected_tokens
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
            prompt = ChunkFactory.create_text(
                f"Archivo visual: {rel_path}", role="user"
            )
            image = ChunkFactory.create_image(drive_file["id"], role="user")
            media_chunks.append(prompt)
            media_chunks.append(image)
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

    context_chunk = ChunkFactory.create_file(
        file_id, role="user", tokens=expected_tokens
    )
    prompt_chunk = ChunkFactory.create_text(resolve_prompt(project_path), role="user")
    model_chunk = ChunkFactory.create_text(RESPONSE_TEMPLATE, role="model")

    new_chunks = []
    new_chunks.append(context_chunk)
    new_chunks.append(prompt_chunk)
    new_chunks.append(model_chunk)

    try:
        with api.modify_chat(chat_id) as chat_data:
            chat_data.chunkedPrompt.chunks = new_chunks
            chat_data.chunkedPrompt.pendingInputs = []

        UI.success("¡Chat y contexto reconstruido con exito!")
    except Exception as e:
        UI.error(f"Error crítico al guardar la reconstrucción del chat: {e}")
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


def extract_chat_assets(api: AIStudioDriveManager, chat_id: str) -> Tuple[ChatIAStudio, Dict]:
    """
    Descarga el JSON del chat y los contenidos binarios referenciados.
    Retorna el chat y un diccionario con los assets en memoria.
    """
    chat_data = api.get_chat_ia_studio(chat_id)
    if not chat_data:
        raise ValueError(f"No se pudo descargar el chat con ID {chat_id}")

    assets = {}
    for chunk in chat_data.chunkedPrompt.chunks:
        file_id = None
        if isinstance(chunk, ChunksDocument) or hasattr(chunk, "driveDocument"):
            file_id = chunk.driveDocument.id # type: ignore
        elif isinstance(chunk, ChunksImage) or hasattr(chunk, "driveImage"):
            file_id = chunk.driveImage.id # type: ignore

        if file_id and file_id not in assets:
            try:
                # Obtener la metadata explícita para asegurar que tenemos el mimeType original
                metadata = api.gdm.service.files().get(
                    fileId=file_id, fields="id, name, mimeType"
                ).execute()

                content_bytes = api.gdm.get_file_content(file_id)
                if content_bytes:
                    assets[file_id] = {
                        "name": metadata.get("name", f"asset_{file_id}"),
                        "mimeType": metadata.get("mimeType", "application/octet-stream"),
                        "bytes": content_bytes
                    }
                else:
                    UI.warn(f"No se pudo descargar el binario del archivo: {file_id}")
            except Exception as e:
                UI.error(f"Error descargando el asset {file_id}: {e}")

    return chat_data, assets


def transfer_chat_to_profile(
    api: AIStudioDriveManager,
    state: Dict,
    project_path: Path,
    target_profile: str
) -> Tuple[AIStudioDriveManager, Dict]:
    """
    Realiza la migración de cuenta, sube los archivos, parchea el JSON
    y establece el nuevo estado seguro.
    """
    from project_context.history import SnapshotManager
    from project_context.utils import load_project_context_state, profile_manager

    UI.info("Extrayendo chat y archivos desde el Perfil Actual (A)...")
    chat_data, assets = extract_chat_assets(api, state["chat_id"])

    old_file_id = state.get("file_id")
    old_md5 = state.get("md5")

    UI.info(f"Transicionando al perfil destino: {target_profile} (B)...")
    profile_manager.set_active_profile(target_profile)

    try:
        new_api = AIStudioDriveManager()
    except Exception as e:
        raise RuntimeError(f"Fallo en autenticación del perfil '{target_profile}': {e}")

    # Snapshot de seguridad si el Perfil B ya tenía un historial para este proyecto
    target_state = load_project_context_state(project_path)
    if target_state and target_state.get("chat_id"):
        UI.warn("El perfil destino ya tiene un chat para este proyecto. Creando snapshot de respaldo...")
        backup_monitor = SnapshotManager(new_api, project_path, target_state)
        backup_monitor.create_named_snapshot("Backup previo a migración entrante")

    UI.info("Subiendo archivos al nuevo Drive y generando mapa de IDs...")
    id_map = {}
    for old_id, asset_data in assets.items():
        new_file = new_api.gdm.upload_binary_to_drive(
            folder_id=new_api.ai_studio_folder,
            file_name=asset_data["name"],
            content=asset_data["bytes"],
            mime_type=asset_data["mimeType"]
        )
        if new_file and "id" in new_file:
            id_map[old_id] = new_file["id"]
        else:
            raise ValueError(f"Fallo al subir el archivo {asset_data['name']} al nuevo perfil.")

    UI.info("Parcheando JSON del chat con los nuevos IDs de Drive...")
    for chunk in chat_data.chunkedPrompt.chunks:
        if isinstance(chunk, ChunksDocument) or hasattr(chunk, "driveDocument"):
            if chunk.driveDocument.id in id_map: # type: ignore
                chunk.driveDocument.id = id_map[chunk.driveDocument.id] # type: ignore
        elif isinstance(chunk, ChunksImage) or hasattr(chunk, "driveImage"):
            if chunk.driveImage.id in id_map: # type: ignore
                chunk.driveImage.id = id_map[chunk.driveImage.id] # type: ignore

    UI.info("Generando nuevo entorno de chat en Google AI Studio...")
    chat_filename = project_path.name + "_chat.prompt"
    new_chat_id = new_api.create_chat_file(file_name=chat_filename, chat_data=chat_data)

    if not new_chat_id:
        raise ValueError("No se pudo crear el archivo de chat en el Perfil B.")

    # Generar el nuevo estado, conservando filtros y MD5 originales para sincronizaciones futuras
    new_state = {
        "path": str(project_path),
        "last_modified": project_path.stat().st_mtime,
        "md5": old_md5,
        "chat_id": new_chat_id,
        "file_id": id_map.get(old_file_id) if old_file_id else None,
        "context_items": state.get("context_items", {"files": [], "folders": []})
    }

    return new_api, new_state
