import json
import logging
from typing import List

from project_context.commands.interactive.register import SessionContext
from project_context.core.exceptions import ChatSessionError
from project_context.core.schemas import ChunkText
from project_context.ui import UI

logger = logging.getLogger(__name__)


def cmd_commit(ctx: SessionContext, args: List[str]):
    """Genera una sugerencia de commit con base en el diff de Git actual."""

    backup_path = ctx.project_context.local_dir / "chat_backup.prompt"
    state = ctx.project_context.load_state()

    if state.chat_id is None:
        raise ChatSessionError("No se encontró una sesión de chat activa.")

    # Comprobar si el usuario quiere restaurar manualmente
    if args and args[0].lower() in ["--clear", "-r", "restore"]:
        if backup_path.exists():
            UI.info("Restaurando chat original desde el respaldo local...")
            try:
                content = backup_path.read_text(encoding="utf-8")
                from project_context.core.schemas import ChatIAStudio

                chat_data = ChatIAStudio(**json.loads(content))
                ctx.api.update_chat(state.chat_id, chat_data)
                backup_path.unlink()
                UI.success("Chat original restaurado con éxito.")
            except Exception as e:
                UI.error(f"No se pudo restaurar el chat: {e}")
        else:
            UI.warn("No se encontró ningún respaldo local de chat para restaurar.")
        return

    is_command_all = args and args[0].lower() in ["-a", "--all", "all"]

    if is_command_all:
        ctx.git.stage_all_changes()

    diff = ctx.git.get_diff_cached()
    if not diff:
        if ctx.git.has_unstaged_changes():
            UI.warn(
                "No hay archivos en stage (git add), pero hay modificaciones locales."
            )
            confirm = (
                input("¿Quieres añadirlos todos al stage ahora? (s/n): ")
                .strip()
                .lower()
            )
            if confirm == "s":
                ctx.git.stage_all_changes()
                diff = ctx.git.get_diff_cached()
                if not diff:
                    raise ChatSessionError(
                        "No se pudo generar el diff de Git después del stage."
                    )
            else:
                UI.info("Operación cancelada.")
                return
        else:
            UI.warn("El repositorio está limpio. No hay cambios pendientes.")
            return

    UI.info("Obteniendo cambios de Git...")

    # Consumimos la lógica delegada en el nuevo servicio
    prompt_text = ctx.commit_service.generate_prompt()
    if not prompt_text:
        raise ChatSessionError("No se pudo generar el prompt de sugerencia de commit.")

    # Obtener chat actual
    UI.info("Descargando configuración del chat remoto...")
    chat_data = ctx.api.get_chat(state.chat_id)
    if not chat_data:
        raise ChatSessionError("No se pudo descargar el chat de Drive.")

    # Respaldar chat original (SÓLO si no existe ya un backup)
    if not backup_path.exists():
        UI.info("Guardando respaldo del chat original en disco...")
        try:
            backup_path.write_text(
                chat_data.model_dump_json(exclude_none=True, exclude_unset=True),
                encoding="utf-8",
            )
        except Exception as e:
            raise ChatSessionError(f"No se pudo escribir el archivo de respaldo: {e}")
    else:
        UI.info(
            "Ya existe un respaldo de chat activo en disco. Actualizando sugerencia sobre la sesión de commit existente."
        )

    # Buscar el chunk del documento de contexto (para mantenerlo)
    context_chunk = None
    for chunk in chat_data.chunkedPrompt.chunks:
        if getattr(chunk, "role", "") == "user" and hasattr(chunk, "driveDocument"):
            if chunk.file_id == state.file_id:
                context_chunk = chunk
                break

    # Construir chat minimalista para el commit
    UI.info("Configurando chat minimalista con modelo rápido...")
    fast_chunks = []
    if context_chunk:
        fast_chunks.append(context_chunk)

    fast_chunks.append(ChunkText(text=prompt_text, role="user"))

    # Cambiamos a un modelo rápido y sanitizamos
    chat_data.runSettings.model = "models/gemini-3.1-flash-lite"
    chat_data.runSettings.sanitize()

    chat_data.chunkedPrompt.chunks = fast_chunks
    chat_data.chunkedPrompt.pendingInputs = []

    # Actualizar el chat remoto
    ctx.api.update_chat(state.chat_id, chat_data)
    UI.success("¡Modo commit activado!")
    UI.info("Ve a Google AI Studio, REFRESCA LA PÁGINA (F5) y presiona RUN.")
    UI.info(
        "Para salir de este modo, escribe: [bold yellow]commit --restore[/] o [bold yellow]update[/]."
    )
