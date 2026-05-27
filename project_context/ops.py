import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from project_context.api_drive import AIStudioDriveManager, ChunkFactory
from project_context.schema import (
    ChatIAStudio,
    ChunkedPrompt,
    ChunksDocument,
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


def create_default_run_settings() -> RunSettings:
    """Retorna una configuración de RunSettings con valores iniciales explícitos y seguros."""
    thinkingLevel = "THINKING_MEDIUM"
    return RunSettings(
        model="models/gemini-3.5-flash",
        temperature=1.0,
        topP=0.95,
        topK=64,
        maxOutputTokens=65536,
        thinkingBudget=None,
        thinkingLevel=thinkingLevel,
    )


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

    chunks = []
    context_chunk, content_md5 = sync_context(api, project_path)

    prompt_chunk = ChunkFactory.create_text(resolve_prompt(project_path), role="user")
    model_chunk = ChunkFactory.create_text(RESPONSE_TEMPLATE, role="model")

    chunks.append(context_chunk)
    chunks.append(prompt_chunk)
    chunks.append(model_chunk)

    chat_data = ChatIAStudio(
        runSettings=create_default_run_settings(),
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

    initial_state = {
        "path": str(project_path),
        "last_modified": project_path.stat().st_mtime,
        "md5": content_md5,
        "chat_id": chat_id,
        "file_id": context_chunk.file_id,
    }
    UI.success(f"Proyecto inicializado con Chat ID: [dim]{chat_id}[/]")
    return initial_state


def update_context(api: AIStudioDriveManager, project_path: Path, state: Dict) -> Dict:
    chat_id = state.get("chat_id", "")
    file_id = state.get("file_id", "")

    # Autocuración: Si los archivos en Drive no existen, recrear de forma limpia preservando lo local
    try:
        context_exists = api.gdm.get_file_metadata(file_id) if file_id else None
        chat_exists = api.gdm.get_file_metadata(chat_id) if chat_id else None
    except Exception:
        context_exists = None
        chat_exists = None

    if not context_exists or not chat_exists:
        UI.warn(
            "(!) Los archivos de la sesión activa en Google Drive no están disponibles."
        )
        UI.info("Re-inicializando entorno en la nube preservando tu historial local...")

        # Generar nuevo contexto
        context_chunk, content_md5 = sync_context(api, project_path)

        # Reconstruir chat base
        from project_context.schema import (
            ChatIAStudio,
            ChunkedPrompt,
            SystemInstruction,
        )

        chunks = [
            context_chunk,
            ChunkFactory.create_text(resolve_prompt(project_path), role="user"),
            ChunkFactory.create_text(RESPONSE_TEMPLATE, role="model"),
        ]

        chat_data = ChatIAStudio(
            runSettings=create_default_run_settings(),
            systemInstruction=SystemInstruction(),
            chunkedPrompt=ChunkedPrompt(chunks=chunks, pendingInputs=[]),
        )

        chat_filename = project_path.name + "_chat.prompt"
        new_chat_id = api.create_chat_file(file_name=chat_filename, chat_data=chat_data)
        if not new_chat_id:
            raise ValueError("No se pudo re-inicializar el chat en Google Drive.")

        state["chat_id"] = new_chat_id
        state["file_id"] = context_chunk.file_id
        state["md5"] = content_md5

        UI.success(
            f"¡Sesión re-inicializada con éxito! Nuevo Chat ID: [dim]{new_chat_id}[/]"
        )
        return state

    # Obtenemos los items seleccionados
    context_items = state.get("context_items", {"files": [], "folders": []})
    has_custom_focus = bool(context_items.get("files") or context_items.get("folders"))

    scope_name = (
        "Enfoque Específico (Stage)" if has_custom_focus else "Raíz del proyecto"
    )

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
                if isinstance(chunk, ChunksDocument) and chunk.file_id == file_id:
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


def extract_chat_assets(
    api: AIStudioDriveManager, chat_id: str
) -> Tuple[ChatIAStudio, Dict]:
    """
    Descarga el JSON del chat y los contenidos binarios referenciados.
    Retorna el chat y un diccionario con los assets en memoria.
    """
    chat_data = api.get_chat_ia_studio(chat_id)
    if not chat_data:
        raise ValueError(f"No se pudo descargar el chat con ID {chat_id}")

    assets = {}
    for chunk in chat_data.chunkedPrompt.chunks:
        file_id = chunk.file_id if chunk.is_file_reference else None

        if file_id and file_id not in assets:
            try:
                # Obtener la metadata explícita para asegurar que tenemos el mimeType original
                metadata = (
                    api.gdm.service.files()
                    .get(fileId=file_id, fields="id, name, mimeType")
                    .execute()
                )

                content_bytes = api.gdm.get_file_content(file_id)
                if content_bytes:
                    assets[file_id] = {
                        "name": metadata.get("name", f"asset_{file_id}"),
                        "mimeType": metadata.get(
                            "mimeType", "application/octet-stream"
                        ),
                        "bytes": content_bytes,
                    }
                else:
                    UI.warn(f"No se pudo descargar el binario del archivo: {file_id}")
            except Exception as e:
                UI.error(f"Error descargando el asset {file_id}: {e}")

    return chat_data, assets


def transfer_chat_to_profile(
    api: AIStudioDriveManager, state: Dict, project_path: Path, target_profile: str
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
        UI.warn(
            "El perfil destino ya tiene un chat para este proyecto. Creando snapshot de respaldo..."
        )
        backup_monitor = SnapshotManager(new_api, project_path, target_state)
        backup_monitor.create_named_snapshot("Backup previo a migración entrante")

    UI.info("Subiendo archivos al nuevo Drive y generando mapa de IDs...")
    id_map = {}
    for old_id, asset_data in assets.items():
        new_file = new_api.gdm.upload_binary_to_drive(
            folder_id=new_api.ai_studio_folder,
            file_name=asset_data["name"],
            content=asset_data["bytes"],
            mime_type=asset_data["mimeType"],
        )
        if new_file and "id" in new_file:
            id_map[old_id] = new_file["id"]
        else:
            raise ValueError(
                f"Fallo al subir el archivo {asset_data['name']} al nuevo perfil."
            )

    UI.info("Parcheando JSON del chat con los nuevos IDs de Drive...")
    for chunk in chat_data.chunkedPrompt.chunks:
        if chunk.is_file_reference and chunk.file_id in id_map:
            chunk.file_id = id_map[chunk.file_id]

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
        "context_items": state.get("context_items", {"files": [], "folders": []}),
    }

    return new_api, new_state


def parse_story_file(file_path: Path) -> Dict:
    """
    Lee un archivo Markdown, busca las etiquetas <mejora>...</mejora>
    y determina la intención del usuario basándose en el texto circundante.
    """
    if not file_path.exists():
        raise FileNotFoundError(f"El archivo {file_path.name} no existe.")

    content = file_path.read_text(encoding="utf-8")

    # Buscar todas las etiquetas para advertir si hay más de una
    matches = list(
        re.finditer(r"<mejora>(.*?)</mejora>", content, re.DOTALL | re.IGNORECASE)
    )

    if not matches:
        raise ValueError(
            f"No se encontró la etiqueta <mejora>...</mejora> en {file_path.name}."
        )

    if len(matches) > 1:
        UI.warn(
            f"Se encontraron {len(matches)} etiquetas <mejora>. Se utilizará solo la ÚLTIMA encontrada."
        )

    # Tomamos la última etiqueta como la activa
    match = matches[-1]
    instruction = match.group(1).strip()

    pre_text = content[: match.start()]
    post_text = content[match.end() :]

    # Función auxiliar para limpiar encabezados markdown y ver si realmente hay texto
    def clean_md(text: str) -> str:
        return re.sub(r"(?m)^#+ .*$", "", text).strip()

    clean_pre = clean_md(pre_text)
    clean_post = clean_md(post_text)

    # Determinar modo
    if not clean_pre and not clean_post:
        mode = "nuevo"
        anchor_pre = ""
        anchor_post = ""
    elif clean_pre and not clean_post:
        mode = "continuacion"
        # Tomamos los últimos ~800 caracteres útiles como ancla
        anchor_pre = clean_pre[-800:].strip()
        anchor_post = ""
    else:
        mode = "edicion"
        anchor_pre = clean_pre[-800:].strip() if clean_pre else ""
        anchor_post = clean_post[:800].strip() if clean_post else ""

    return {
        "mode": mode,
        "instruction": instruction,
        "anchor_pre": anchor_pre.split("\n")[-1],
        "anchor_post": anchor_post,
    }


def generate_story_prompt(parsed_data: Dict, file_name: str) -> str:
    """
    Construye el prompt exacto que se enviará a la IA según el modo detectado.
    """
    mode = parsed_data["mode"]
    instruction = parsed_data["instruction"]

    base_prompt = f"Actúa como un co-escritor creativo. Tu objetivo es trabajar en el archivo `{file_name}` que se encuentra en el contexto adjunto.\n\n"
    base_rule = (
        "usando como fuente el texto encerrado en las etiqueta `<mejora>` y `</mejora>`. "
        "Mantén la coherencia con el contexto global y pioriza escribir dialogos.\n\n"
    )

    if mode == "nuevo":
        return f"Ayúdame a escribir la primera escena del archivo `{file_name} ` desde cero, {base_rule}"

    elif mode == "continuacion":
        return (
            f"Ayúdame a continuar desarrollando la historia del `{file_name}`, {base_rule}"
            + "La mejora empieza exactamente despues del siguiente texto:\n"
            "```text\n"
            f"{parsed_data['anchor_pre']}\n"
            "```\n\n"
        )

    elif mode == "edicion":
        return (
            base_prompt
            + "Ayúdame a editar e integrar una nueva idea en el medio de la historia de este archivo.\n"
            "Tienes que desarrollar y mejorar el siguiente borrador, agregando diálogos o descripciones si es necesario, "
            "y hacer que encaje perfectamente como puente entre el texto anterior y el texto posterior.\n\n"
            "Instrucciones / Borrador a mejorar:\n"
            f"{instruction}\n\n"
            "--- TEXTO ANTERIOR ---\n"
            "```text\n"
            f"{parsed_data['anchor_pre']}\n"
            "```\n\n"
            "--- TEXTO POSTERIOR ---\n"
            "```text\n"
            f"{parsed_data['anchor_post']}\n"
            "```\n"
        )

    return ""


def apply_story_update(
    api: AIStudioDriveManager,
    project_path: Path,
    state: Dict,
    media_root_hint: Optional[Path] = None,
) -> Dict:
    """
    Actualiza el contexto general, analiza el archivo de historia ancla,
    resuelve y sincroniza las imágenes de su etiqueta <mejora>,
    y actualiza de forma atómica la estructura del chat en Google Drive.
    """
    from project_context.utils import extract_image_references_from_text

    anchor_rel_path = state.get("story_anchor")
    if not anchor_rel_path:
        raise ValueError("No hay un ancla de historia definida en el estado.")

    anchor_file = project_path / anchor_rel_path
    UI.info(f"Analizando intención en el archivo ancla: [cyan]{anchor_rel_path}[/]")

    # Parsear el archivo local
    try:
        parsed_data = parse_story_file(anchor_file)
    except Exception as e:
        UI.error(str(e))
        return state

    UI.info(f"Intención detectada: [bold magenta]{parsed_data['mode'].upper()}[/]")
    instruction_text = parsed_data["instruction"]

    # Extraer y resolver referencias a imágenes de la etiqueta <mejora>
    refs = extract_image_references_from_text(instruction_text)
    resolved_images = []

    for ref_text, is_wiki in refs:
        # Intentar resolver relativo al directorio del archivo ancla
        candidate = (anchor_file.parent / ref_text).resolve()

        # Si es WikiLink y no se encuentra, probar con la carpeta de medios de la sesión
        if not candidate.exists() and is_wiki and media_root_hint:
            candidate = (media_root_hint / ref_text).resolve()

        # Alternativa: Buscar relativo a la raíz del proyecto
        if not candidate.exists():
            candidate = (project_path / ref_text).resolve()

        if candidate.exists() and candidate.is_file():
            resolved_images.append((candidate, ref_text))
        else:
            UI.warn(
                f"Referencia visual ignorada (no se encontró en el disco): '{ref_text}'"
            )

    # Sincronizar las imágenes en Drive (Reutilizando existentes)
    image_chunks = []
    if resolved_images:
        UI.info(f"Sincronizando {len(resolved_images)} recursos visuales detectados...")
        image_chunks = sync_story_images(api, project_path, resolved_images)

    # Generar el Prompt de Texto Dinámico
    anchor_file_path = anchor_file.relative_to(project_path).as_posix()
    story_prompt = generate_story_prompt(parsed_data, anchor_file_path)

    # Sincronizar contexto global del código fuente (SSoT)
    state = update_context(api, project_path, state)

    # Modificar el Chat en Drive
    chat_id = state["chat_id"]
    chat_data = api.get_chat_ia_studio(chat_id)
    if not chat_data:
        raise ValueError(f"No se pudo descargar el chat con ID {chat_id}")

    UI.info("Actualizando bloques de prompt del chat e integrando recursos visuales...")

    # Conservamos los 3 primeros bloques iniciales (Documento Contexto, System Prompt, Acuse de recibo)
    base_chunks = chat_data.chunkedPrompt.chunks[:3]

    # Añadir Bloque 4: La instrucción dinámica
    new_instruction_chunk = ChunkFactory.create_text(story_prompt, role="user")
    base_chunks.append(new_instruction_chunk)

    # Añadir los bloques de imágenes alternados (Texto de Ruta + Drive Image) si existen
    if image_chunks:
        base_chunks.extend(image_chunks)

    # Asignar la lista reconstruida y limpiar inputs pendientes
    chat_data.chunkedPrompt.chunks = base_chunks
    chat_data.chunkedPrompt.pendingInputs = []

    # Escribir el nuevo JSON en Drive
    if api.update_chat_file(chat_id, chat_data):
        UI.success(
            "¡Chat preparado! Ve a AI Studio, REFRESCA LA PÁGINA (F5) y presiona RUN."
        )
    else:
        UI.error("Error al actualizar el chat de historia en Drive.")

    return state


def sync_story_images(
    api: AIStudioDriveManager,
    project_path: Path,
    resolved_images: List[Tuple[Path, str]],
) -> list:
    """
    Sincroniza un listado de imágenes locales con Drive de forma ligera.
    Si la imagen ya existe por nombre en Drive, se reutiliza su ID sin volver a subir bytes.
    Retorna una lista con la estructura de bloques alternados para el Chat.
    """
    media_chunks = []
    for img_path, original_ref in resolved_images:
        drive_name = f"ctx_{img_path.name}"

        # Comprobar existencia previa usando metadatos rápidos
        drive_file = api.gdm.find_item_by_name(
            drive_name, parent_id=api.ai_studio_folder
        )

        # Subir binario únicamente si no existía en la carpeta de Drive
        if not drive_file:
            UI.info(f"Subiendo nueva imagen a Google Drive: {img_path.name}...")
            with open(img_path, "rb") as f:
                content = f.read()
                mime = f"image/{img_path.suffix[1:].replace('jpg', 'jpeg')}"
                drive_file = api.gdm.upload_binary_to_drive(
                    api.ai_studio_folder, drive_name, content, mime
                )
        else:
            UI.info(f"Reutilizando imagen existente en Drive: [dim]{drive_name}[/]")

        if drive_file:
            # Creamos el par de bloques estructurados:
            # Bloque A: Texto indicador con la referencia que usó el usuario
            prompt_chunk = ChunkFactory.create_text(
                f"Archivo visual: {original_ref}", role="user"
            )
            # Bloque B: El elemento visual de imagen multimodal con su ID de Drive
            image_chunk = ChunkFactory.create_image(drive_file["id"], role="user")

            media_chunks.append(prompt_chunk)
            media_chunks.append(image_chunk)

    return media_chunks
