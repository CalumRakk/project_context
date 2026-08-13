import logging
import re
from pathlib import Path
from typing import List, Optional, Tuple

from project_context.commands.interactive.register import SessionContext
from project_context.core.project_context import ProjectContext
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

    if not clean_pre and not clean_post:
        mode = "nuevo"
        anchor_pre = ""
        anchor_post = ""
    elif clean_pre and not clean_post:
        mode = "continuacion"
        anchor_pre = clean_pre[-800:].strip()
        anchor_post = ""
    else:
        mode = "edicion"
        anchor_pre = clean_pre[-800:].strip() if clean_pre else ""
        anchor_post = clean_post[:800].strip() if clean_post else ""

    return {
        "mode": mode,
        "instruction": instruction,
        "anchor_pre": anchor_pre.split("\n")[-1] if anchor_pre else "",
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
            f"Ayúdame a editar e integrar una nueva idea en la historia del `{file_name}`, {base_rule}"
            + "La mejora empieza exactamente después del siguiente texto:\n"
            "```text\n"
            f"{parsed_data['anchor_pre']}\n"
            "```\n\n"
            "El texto despues de las etiquetas no lo incluyas en tu respuesta. Esto lo haré manualmente.\n\n"
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
    ctx: Optional[SessionContext] = None,  # TODO: solucion magica a mejorar.
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
