import logging
import os
import re
from pathlib import Path
from typing import List, Optional, Tuple

from project_context.commands.interactive.register import SessionContext
from project_context.core.project_context import ProjectContext
from project_context.core.schemas import ChunkImage, ChunkText
from project_context.services.api_drive import ChunkFactory, GoogleDriveManager
from project_context.utils import (
    UI,
    extract_image_references_from_text,
)

logger = logging.getLogger(__name__)


def parse_story_file(file_path: Path) -> dict:
    """
    Lee un archivo Markdown, busca la última etiqueta <mejora>...</mejora>
    y determina la intención del usuario basándose en el texto circundante.
    """
    if not file_path.exists():
        raise FileNotFoundError(f"El archivo {file_path.name} no existe.")

    content = file_path.read_text(encoding="utf-8")

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

    match = matches[-1]
    instruction = match.group(1).strip()

    pre_text = content[: match.start()]
    post_text = content[match.end() :]

    def clean_md(text: str) -> str:
        return re.sub(r"(?m)^#+ .*$", "", text).strip()

    clean_pre = clean_md(pre_text)
    clean_post = clean_md(post_text)

    # Extraer las líneas no vacías para obtener anclas precisas
    pre_lines = [line.strip() for line in clean_pre.splitlines() if line.strip()]
    post_lines = [line.strip() for line in clean_post.splitlines() if line.strip()]

    if not clean_pre and not clean_post:
        mode = "nuevo"
        anchor_pre = ""
        anchor_post = ""
    elif clean_pre and not clean_post:
        mode = "continuacion"
        anchor_pre = pre_lines[-1] if pre_lines else ""
        anchor_post = ""
    else:
        mode = "edicion"
        anchor_pre = pre_lines[-1] if pre_lines else ""
        anchor_post = post_lines[0] if post_lines else ""

    return {
        "mode": mode,
        "instruction": instruction,
        "anchor_pre": anchor_pre,
        "anchor_post": anchor_post,
    }


def generate_story_prompt(parsed_data: dict, file_name: str) -> str:
    """
    Construye el prompt exacto que se enviará a la IA según el modo detectado.
    """
    mode = parsed_data["mode"]

    base_rule = (
        "usando como fuente el texto encerrado en las etiqueta `<mejora>` y `</mejora>`. "
        "Mantén la coherencia con el contexto global y prioriza escribir diálogos.\n\n"
    )

    if mode == "nuevo":
        return f"Ayúdame a escribir la primera escena del archivo `{file_name}` desde cero, {base_rule}"

    elif mode == "continuacion":
        return (
            f"Ayúdame a continuar desarrollando la historia del `{file_name}`, {base_rule}"
            + "La mejora empieza exactamente después del siguiente texto:\n"
            "```text\n"
            f"{parsed_data['anchor_pre']}\n"
            "```\n\n"
        )

    elif mode == "edicion":
        return (
            f"Ayúdame a reescribir y mejorar la historia del `{file_name}`, {base_rule}"
            + "Empieza a editar exactamente desde el siguiente texto:\n"
            "```text\n"
            f"{parsed_data['anchor_post']}\n"
            "```\n\n"
        )

    return ""


def _ensure_image_chunk_pair(
    api: GoogleDriveManager, img_path: Path, reference_str: str
) -> list:
    """
    Comprueba si una imagen existe en la carpeta de Drive; si no, la sube.
    Retorna la lista con los bloques del prompt y de la imagen.
    """
    drive_name = f"ctx_{img_path.name}"
    drive_file = api.find_item_by_name(drive_name, parent_id=api.ai_studio_folder)

    if not drive_file:
        UI.info(f"Subiendo nueva imagen a Google Drive: {img_path.name}...")
        content = img_path.read_bytes()
        suffix = img_path.suffix.lower()
        mime = f"image/{suffix[1:].replace('jpg', 'jpeg')}"

        drive_file_info = api.create_file(
            folder_id=api.ai_studio_folder,
            file_name=drive_name,
            content=content,
            mime_type=mime,
        )
        file_id = drive_file_info.id
    else:
        UI.info(f"Reutilizando imagen existente en Drive: [dim]{drive_name}[/]")
        file_id = drive_file["id"]

    prompt = ChunkFactory.create_text(f"Archivo visual: {reference_str}", role="user")
    image = ChunkFactory.create_image(file_id, role="user")
    return [prompt, image]


def sync_story_images(
    api: GoogleDriveManager,
    project_path: Path,
    resolved_images: List[Tuple[Path, str]],
) -> list:
    """
    Sincroniza un listado de imágenes locales con Drive de forma ligera.
    """
    media_chunks = []
    for img_path, original_ref in resolved_images:
        chunks = _ensure_image_chunk_pair(api, img_path, original_ref)
        media_chunks.extend(chunks)
    return media_chunks


def apply_story_update(
    api: GoogleDriveManager,
    project_context: ProjectContext,
    story_anchor_rel: str,
    ctx: Optional[SessionContext] = None,
):
    """
    Actualiza el contexto, analiza la historia ancla,
    sincroniza recursos visuales y actualiza el chat en Drive.
    """

    anchor_file = project_context.project_path / story_anchor_rel
    UI.info(f"Analizando intención en el archivo ancla: [cyan]{story_anchor_rel}[/]")

    parsed_data = parse_story_file(anchor_file)

    UI.info(f"Intención detectada: [bold magenta]{parsed_data['mode'].upper()}[/]")
    instruction_text = parsed_data["instruction"]

    refs = extract_image_references_from_text(instruction_text)
    resolved_images = []

    for ref_text, is_wiki in refs:
        candidate = (anchor_file.parent / ref_text).resolve()

        if not candidate.exists() and is_wiki:
            from project_context.utils import get_potential_media_folders

            media_folders = get_potential_media_folders(project_context.project_path)
            for folder in media_folders:
                temp_cand = (folder / ref_text).resolve()
                if temp_cand.exists() and temp_cand.is_file():
                    candidate = temp_cand
                    break

        if not candidate.exists():
            candidate = (project_context.project_path / ref_text).resolve()

        if candidate.exists() and candidate.is_file():
            resolved_images.append((candidate, ref_text))
        else:
            UI.warn(
                f"Referencia visual ignorada (no se encontró en el disco): '{ref_text}'"
            )

    image_chunks = []
    if resolved_images:
        UI.info(f"Sincronizando {len(resolved_images)} recursos visuales detectados...")
        image_chunks = sync_story_images(
            api, project_context.project_path, resolved_images
        )

    anchor_file_path = anchor_file.relative_to(project_context.project_path).as_posix()
    story_prompt = generate_story_prompt(parsed_data, anchor_file_path)

    # Actualizamos el contexto maestro y el archivo del chat
    assert ctx is not None
    ctx.context_service.create_or_update_chat()

    state = project_context.load_state()
    chat_id = state.chat_id
    if not chat_id:
        raise ValueError(
            "No se pudo obtener un chat_id válido tras actualizar el contexto."
        )

    chat_data = api.get_chat(chat_id)

    UI.info("Actualizando bloques de prompt del chat e integrando recursos visuales...")

    # Mantenemos únicamente los 3 primeros bloques iniciales
    base_chunks = chat_data.chunkedPrompt.chunks[:3]

    new_instruction_chunk = ChunkFactory.create_text(story_prompt, role="user")
    base_chunks.append(new_instruction_chunk)

    if image_chunks:
        base_chunks.extend(image_chunks)

    chat_data.chunkedPrompt.chunks = base_chunks
    chat_data.chunkedPrompt.pendingInputs = []

    api.update_chat(chat_id, chat_data)
    UI.success(
        "¡Chat preparado! Ve a AI Studio, REFRESCA LA PÁGINA (F5) y presiona RUN."
    )


def assemble_chat_to_markdown(
    api: GoogleDriveManager,
    project_context: ProjectContext,
    target_rel_path: str,
) -> Path:
    """
    Descarga el chat activo, filtra pensamientos, descarga imágenes vinculadas a disco
    y ensambla la conversación formateada con encabezados claros de turnos.
    """
    state = project_context.load_state()
    if not state.chat_id:
        raise ValueError("No hay una sesión de chat activa para ensamblar.")

    UI.info("Descargando historial de la conversación desde Google Drive...")
    chat_data = api.get_chat(state.chat_id)
    chunks = chat_data.chunkedPrompt.chunks

    # Omitimos los primeros 3 chunks de configuración inicial
    conv_chunks = chunks[3:] if len(chunks) > 3 else []
    if not conv_chunks:
        raise ValueError(
            "El chat no contiene mensajes de conversación suficientes para ensamblar."
        )

    target_file = (project_context.project_path / target_rel_path).resolve()
    target_file.parent.mkdir(parents=True, exist_ok=True)

    # Carpeta donde se guardarán los recursos visuales descargados
    images_dir = project_context.project_path / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    turn_blocks = []
    downloaded_images = 0
    last_role = None

    for chunk in conv_chunks:
        # FILTRADO: Omitir pensamientos internos del modelo (isThought)
        if getattr(chunk, "isThought", False):
            continue

        role = getattr(chunk, "role", "user")

        # PROCESAMIENTO DE IMÁGENES
        if isinstance(chunk, ChunkImage) or chunk.is_image:
            file_id = chunk.file_id
            if file_id:
                meta = api.get_metadata(file_id)
                img_name = meta.filename if meta else f"image_{file_id}.png"
                local_img_path = images_dir / img_name

                # Descarga si no existe localmente
                if not local_img_path.exists():
                    UI.info(f"Descargando recurso visual: [cyan]{img_name}[/]...")
                    img_bytes = api.get_file_content(file_id)
                    local_img_path.write_bytes(img_bytes)
                    downloaded_images += 1
                else:
                    UI.info(f"Reutilizando imagen local existente: [dim]{img_name}[/]")

                rel_img_path = os.path.relpath(
                    local_img_path, target_file.parent
                ).replace("\\", "/")
                img_markdown = f"![{img_name}]({rel_img_path})"

                # Control de encabezado según cambio de turno
                if role != last_role:
                    header_title = "### 👤 Jugador" if role == "user" else "### 🎭 IA"
                    turn_blocks.append(f"\n---\n\n{header_title}\n\n{img_markdown}")
                    last_role = role
                else:
                    turn_blocks.append(img_markdown)
            continue

        # PROCESAMIENTO DE TEXTO NARRATIVO
        if isinstance(chunk, ChunkText) or chunk.is_text:
            text = chunk.text.strip()  # type: ignore
            if not text:
                continue

            # Control de encabezado según cambio de turno
            if role != last_role:
                header_title = "### 👤 Jugador" if role == "user" else "### 🎭 IA"
                turn_blocks.append(f"\n---\n\n{header_title}\n\n{text}")
                last_role = role
            else:
                turn_blocks.append(text)

    # Unir todo el documento y limpiar separadores iniciales redundantes
    assembled_content = "\n\n".join(turn_blocks).strip()
    if assembled_content.startswith("---"):
        assembled_content = assembled_content.lstrip("-").strip()
    assembled_content += "\n"

    target_file.write_text(assembled_content, encoding="utf-8")

    if downloaded_images > 0:
        UI.success(f"Se descargaron {downloaded_images} imagen(es) en: [dim]images/[/]")

    return target_file
