import re
from typing import cast

from project_context.exceptions import ChatSessionError, InvalidCommandArgumentError
from project_context.ops import apply_story_update, update_context
from project_context.schema import ChunksDocument, ChunksText
from project_context.ui.registry import SessionContext, registry
from project_context.utils import (
    UI,
    clear_stash,
    console,
    get_context_tree,
    human_to_int,
    load_stash,
    save_stash,
)


@registry.register("clear", require_chat=True)
def cmd_clear(ctx: SessionContext, args: list[str]):
    """Limpia el historial de la conversación manteniendo el contexto inicial."""
    if ctx.api.clear_chat_ia_studio(ctx.chat_id):
        UI.success("Historial de mensajes limpiado en Drive.")
    else:
        raise ChatSessionError(
            "No se pudo limpiar el historial del chat en Google Drive."
        )


@registry.register("clear:code", require_chat=True)
def cmd_clear_code(ctx: SessionContext, args: list[str]):
    """Elimina bloques de código de los mensajes para reducir el tamaño del chat."""
    clean_user = False
    if args:
        flag = args[0].lower()
        if flag in ["--all", "all", "user", "--user"]:
            clean_user = True

    UI.info("Creando snapshot de seguridad antes de limpiar código...")
    try:
        ctx.monitor.create_named_snapshot(
            "Backup automático antes de limpiar bloques de código"
        )
    except Exception as e:
        UI.warn(
            f"No se pudo crear el snapshot automático: {e}. Continuando con la operación..."
        )

    UI.info("Procesando bloques de texto en Google Drive...")
    cleaned_blocks_count = 0
    code_blocks_removed = 0

    try:
        with ctx.api.modify_chat(ctx.chat_id) as chat_data:
            for idx, chunk in enumerate(chat_data.chunkedPrompt.chunks):
                if idx <= 2:
                    continue

                if chunk.is_text:
                    chunk = cast(ChunksText, chunk)
                    role = getattr(chunk, "role", "user")
                    if role == "model" or (role == "user" and clean_user):
                        matches = re.findall(r"```[\s\S]*?```", chunk.text)
                        if matches:
                            code_blocks_removed += len(matches)
                            chunk.text = re.sub(
                                r"```[\s\S]*?```", "[Código omitido]", chunk.text
                            )
                            chunk.tokenCount = None
                            cleaned_blocks_count += 1

        if code_blocks_removed > 0:
            UI.success(
                f"Se eliminaron {code_blocks_removed} bloques de código en {cleaned_blocks_count} mensajes."
            )
            UI.info(
                "Por favor, recarga la pestaña de Google AI Studio (F5) para aplicar la vista limpia."
            )
        else:
            UI.info(
                "No se encontraron bloques de código para eliminar en los roles seleccionados."
            )

    except Exception as e:
        raise ChatSessionError(f"Error al limpiar los bloques de código: {e}")


@registry.register("update", require_chat=True)
def cmd_update(ctx: SessionContext, args: list[str]):
    """Actualiza el contenido del archivo de contexto en Drive."""
    clean_args_list = [
        arg for arg in args if arg not in ["--force", "-f", "force", "--run", "-r"]
    ]

    if ctx.state.get("story_mode"):
        UI.info("Modo historia activo. Procesando actualización...")
        new_state = apply_story_update(
            ctx.api, ctx.project_path, ctx.state, media_root_hint=ctx.session_media_root
        )
        ctx.update_state(new_state)
    else:
        new_state = update_context(ctx.api, ctx.project_path, ctx.state)
        ctx.update_state(new_state)

        has_focus = bool(
            ctx.context_items.get("files") or ctx.context_items.get("folders")
        )
        if "tree" in clean_args_list or has_focus:
            UI.info("Árbol de archivos enviado:")
            tree_str = get_context_tree(ctx.project_path, ctx.context_items)
            console.print(f"\n[dim cyan]{tree_str}[/dim cyan]\n")


@registry.register("tokens", require_chat=True)
def cmd_tokens(ctx: SessionContext, args: list[str]):
    """Actualiza manualmente el contador de tokens del archivo de contexto en Drive."""
    if not args:
        UI.warn("Uso: tokens <cantidad> (ej: tokens 150000 o tokens 150k)")
        return

    val = args[0]
    try:
        tokens = human_to_int(val)
    except Exception:
        raise InvalidCommandArgumentError(
            "Formato de tokens no válido. Usa números enteros o notaciones como '150k'."
        )

    UI.info(f"Actualizando contador de tokens a [bold]{tokens}[/] en Google Drive...")
    try:
        with ctx.api.modify_chat(ctx.chat_id) as chat_data:
            updated_metadata = False
            for chunk in chat_data.chunkedPrompt.chunks:
                if isinstance(chunk, ChunksDocument) and chunk.file_id == ctx.file_id:
                    chunk.tokenCount = tokens
                    updated_metadata = True
                    break

            if updated_metadata:
                UI.success(
                    f"Contador de tokens actualizado en Drive a: [bold]{tokens}[/]"
                )
            else:
                UI.warn(
                    "No se encontró el bloque de contexto del archivo en el chat actual."
                )
    except Exception as e:
        raise ChatSessionError(
            f"Error al intentar modificar los tokens en el chat: {e}"
        )


@registry.register("insert", "msg", require_chat=True)
def cmd_insert(ctx: SessionContext, args: list[str]):
    """Inserta un mensaje con rol de usuario o modelo en el historial de Drive."""
    if not args:
        UI.warn("Uso: insert [user|ia|model] <mensaje>")
        return

    role_input = args[0].lower()
    user_aliases = {"user", "usuario", "u"}
    model_aliases = {"model", "ia", "assistant", "modelo", "i"}

    if role_input in user_aliases:
        role = "user"
        message = " ".join(args[1:])
    elif role_input in model_aliases:
        role = "model"
        message = " ".join(args[1:])
    else:
        role = "user"
        message = " ".join(args)

    if not message:
        raise InvalidCommandArgumentError("El cuerpo del mensaje no puede estar vacío.")

    UI.info("Verificando la estructura del chat en Google Drive...")
    chat_data = ctx.api.get_chat_ia_studio(ctx.chat_id)
    if not chat_data:
        raise ChatSessionError("No se pudo obtener el historial del chat desde Drive.")

    chunks = chat_data.chunkedPrompt.chunks
    if chunks:
        last_chunk = chunks[-1]
        last_role = getattr(last_chunk, "role", None)

        if role == "user" and last_role == "user":
            raise InvalidCommandArgumentError(
                "El último bloque del chat ya es de tipo Usuario.\n"
                "Ejecuta 'RUN' en AI Studio para obtener la respuesta o inserta un mensaje de tipo 'ia' primero."
            )
        elif role == "model" and last_role == "model":
            raise InvalidCommandArgumentError(
                "El último bloque del chat ya es de tipo IA (Model).\n"
                "Inserta un mensaje de tipo 'user' primero para mantener la alternancia."
            )

    UI.info(f"Insertando bloque con rol '{role}'...")
    success = ctx.api.append_message(ctx.chat_id, message, role=role)

    if success:
        UI.success(f"Mensaje insertado con rol '{role}'.")
        UI.info(
            "Recuerda recargar la pestaña en Google AI Studio (F5) para aplicar los cambios."
        )
    else:
        raise ChatSessionError("Error al escribir el mensaje en Google Drive.")


@registry.register("run", "r", require_chat=True)
def cmd_run(ctx: SessionContext, args: list[str]):
    """Ejecuta o procesa la sesión actual."""
    # Validación automática por require_chat=True
    pass


@registry.register("vanish:on", require_chat=True, allow_in_vanish=True)
def cmd_vanish_on(ctx: SessionContext, args: list[str]):
    """Oculta temporalmente la conversación activa en Google Drive."""
    if ctx.state.get("vanished", False):
        UI.warn("El modo vanish ya se encuentra activo.")
        return

    UI.info("Guardando copia de seguridad del chat (Vanish Stash)...")
    chat_data = ctx.api.get_chat_ia_studio(ctx.chat_id)
    if not chat_data:
        raise ChatSessionError(
            "No se pudo descargar el chat desde Drive para realizar el respaldo."
        )

    save_stash(ctx.project_path, "vanish_stash.json", chat_data.model_dump_json())

    UI.info("Estableciendo pantalla limpia en Google Drive...")
    vanish_chunks = [ChunksText(text="✨ vanish off ✨", role="user")]
    chat_data.chunkedPrompt.chunks = vanish_chunks  # type:ignore
    chat_data.chunkedPrompt.pendingInputs = []

    if ctx.api.update_chat_file(ctx.chat_id, chat_data):
        ctx.state["vanished"] = True
        ctx.update_state(ctx.state)
        UI.success(
            "Modo Vanish activado. La conversación se encuentra oculta en Drive."
        )
        UI.info(
            "Recarga la pestaña en Google AI Studio (F5) para aplicar la vista limpia."
        )
    else:
        clear_stash(ctx.project_path, "vanish_stash.json")
        raise ChatSessionError(
            "Ocurrió un problema al actualizar el chat en Drive para activar vanish."
        )


@registry.register("vanish:off", require_chat=True, allow_in_vanish=True)
def cmd_vanish_off(ctx: SessionContext, args: list[str]):
    """Restaura la conversación y el contexto original en Google Drive."""
    if not ctx.state.get("vanished", False):
        UI.warn("El modo vanish no está activo en este momento.")
        return

    stashed_json = load_stash(ctx.project_path, "vanish_stash.json")
    if not stashed_json:
        ctx.state["vanished"] = False
        ctx.update_state(ctx.state)
        raise ChatSessionError(
            "No se encontró el archivo de respaldo de Vanish para restaurar."
        )

    UI.info("Restaurando conversación y contexto original...")
    success = ctx.api.gdm.update_file_from_memory(
        file_id=ctx.chat_id, content=stashed_json, mime_type=ctx.api.MIME_PROMPT
    )

    if success:
        clear_stash(ctx.project_path, "vanish_stash.json")
        ctx.state["vanished"] = False
        ctx.update_state(ctx.state)
        UI.success("¡Chat original restaurado con éxito! Saliendo del modo Vanish.")
        UI.info(
            "Recarga la pestaña en Google AI Studio (F5) para ver el chat recuperado."
        )
    else:
        raise ChatSessionError(
            "No se pudo escribir el archivo original en Drive para desactivar vanish."
        )


@registry.register("vanish", require_chat=True, allow_in_vanish=True)
def cmd_vanish(ctx: SessionContext, args: list[str]):
    """Alterna de forma automática (on/off) el modo vanish."""
    if args:
        sub = args[0].lower()
        if sub == "on":
            return cmd_vanish_on(ctx, args[1:])
        elif sub == "off":
            return cmd_vanish_off(ctx, args[1:])
        else:
            raise InvalidCommandArgumentError(
                "Subcomando inválido. Uso sugerido: 'vanish on' o 'vanish off'"
            )

    if ctx.state.get("vanished", False):
        return cmd_vanish_off(ctx, [])
    return cmd_vanish_on(ctx, [])
