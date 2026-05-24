import time

from project_context.exceptions import ChatSessionError, InvalidCommandArgumentError
from project_context.ops import apply_story_update, update_context
from project_context.schema import ChunksDocument, ChunksText
from project_context.ui.registry import SessionContext, registry
from project_context.utils import (
    UI,
    clear_vanish_stash,
    console,
    get_context_tree,
    human_to_int,
    load_vanish_stash,
    save_vanish_stash,
)


@registry.register("clear", require_chat=True)
def cmd_clear(ctx: SessionContext, args: str):
    chat_id = ctx.state.get("chat_id")
    if not chat_id:
        raise ChatSessionError("No hay un chat activo en el estado del proyecto.")

    if ctx.api.clear_chat_ia_studio(chat_id):
        if ctx.bridge_server and ctx.bridge_server.clients:
            UI.success("Historial de mensajes limpiado en Drive. Recargando pestaña...")
            ctx.bridge_server.broadcast_reload(chat_id)
        else:
            UI.success("Historial de mensajes limpiado en Drive.")
    else:
        raise ChatSessionError("No se pudo limpiar el historial del chat en Google Drive.")


@registry.register("update", require_chat=True)
def cmd_update(ctx: SessionContext, args: str):
    chat_id = ctx.state.get("chat_id")
    args_list = args.strip().split()

    force = "--force" in args_list or "-f" in args_list or "force" in args_list
    run_after_update = "--run" in args_list or "-r" in args_list

    # Filtrar argumentos para limpiar la presentación
    clean_args_list = [arg for arg in args_list if arg not in ["--force", "-f", "force", "--run", "-r"]]
    clean_args = " ".join(clean_args_list)

    tab_is_focused = False

    if ctx.bridge_server and chat_id and not force:
        status = ctx.bridge_server.check_if_input_empty(chat_id, timeout=1.5)
        tab_is_focused = status.get("focused", False)

        if tab_is_focused:
            if not status.get("isEmpty", True):
                raise InvalidCommandArgumentError(
                    "Sincronización abortada: Tienes texto escrito en el input de AI Studio.\n"
                    "Usa 'update --force' para ignorar este aviso o limpia el input en el navegador."
                )
        else:
            UI.warn("La pestaña del chat no está activa/enfocada en tu navegador.")
            if run_after_update:
                raise InvalidCommandArgumentError("No se puede ejecutar de forma automática si la pestaña no está enfocada.")
            UI.info("El contexto se sincronizará en Google Drive, pero deberás recargar manualmente (F5) al regresar al navegador.")

    if ctx.state.get("story_mode"):
        UI.info("Modo historia activo. Procesando actualización...")
        new_state = apply_story_update(
            ctx.api,
            ctx.project_path,
            ctx.state,
            media_root_hint=ctx.session_media_root
        )
        ctx.update_state(new_state)
    else:
        new_state = update_context(ctx.api, ctx.project_path, ctx.state)
        ctx.update_state(new_state)

        has_focus = bool(ctx.state.get("context_items", {}).get("files") or ctx.state.get("context_items", {}).get("folders"))
        if clean_args == "tree" or has_focus:
            UI.info("Árbol de archivos enviado:")
            tree_str = get_context_tree(ctx.project_path, ctx.state.get("context_items"))
            console.print(f"\n[dim cyan]{tree_str}[/dim cyan]\n")

    if ctx.bridge_server and chat_id and tab_is_focused:
        UI.info("Enviando señal de recarga al navegador...")
        ctx.bridge_server.broadcast_reload(chat_id)

        if run_after_update:
            time.sleep(1.0)
            UI.info("Esperando que la página recargue para presionar RUN automáticamente...")
            run_status = ctx.bridge_server.trigger_browser_run(chat_id)
            if run_status.get("success"):
                UI.success(run_status.get("message", ""))
            else:
                raise ChatSessionError(f"Fallo en la ejecución automática: {run_status.get('message')}")


@registry.register("tokens", require_chat=True)
def cmd_tokens(ctx: SessionContext, args: str):
    val = args.strip()
    if not val:
        UI.warn("Uso: tokens <cantidad> (ej: tokens 150000 o tokens 150k)")
        return

    try:
        tokens = human_to_int(val)
    except Exception:
        raise InvalidCommandArgumentError("Formato de tokens no válido. Usa números enteros o notaciones como '150k'.")

    chat_id = ctx.state.get("chat_id")
    file_id = ctx.state.get("file_id")

    if not chat_id or not file_id:
        raise ChatSessionError("Falta información del chat o del archivo de contexto en el estado actual.")

    UI.info(f"Actualizando contador de tokens a [bold]{tokens}[/] en Google Drive...")
    try:
        with ctx.api.modify_chat(chat_id) as chat_data:
            updated_metadata = False
            for chunk in chat_data.chunkedPrompt.chunks:
                if isinstance(chunk, ChunksDocument) and chunk.file_id == file_id:
                    chunk.tokenCount = tokens
                    updated_metadata = True
                    break

            if updated_metadata:
                UI.success(f"Contador de tokens actualizado en Drive a: [bold]{tokens}[/]")
            else:
                UI.warn("No se encontró el bloque de contexto del archivo en el chat actual.")
    except Exception as e:
        raise ChatSessionError(f"Error al intentar modificar los tokens en el chat: {e}")


@registry.register("insert", "msg", require_chat=True)
def cmd_insert(ctx: SessionContext, args: str):
    args = args.strip()
    if not args:
        UI.warn("Uso: insert [user|ia|model] <mensaje>")
        return

    parts = args.split(" ", 1)
    role_input = parts[0].lower()

    user_aliases = {"user", "usuario", "u"}
    model_aliases = {"model", "ia", "assistant", "modelo", "i"}

    if role_input in user_aliases:
        role = "user"
        message = parts[1].strip() if len(parts) > 1 else ""
    elif role_input in model_aliases:
        role = "model"
        message = parts[1].strip() if len(parts) > 1 else ""
    else:
        role = "user"
        message = args

    if not message:
        raise InvalidCommandArgumentError("El cuerpo del mensaje no puede estar vacío.")

    chat_id = ctx.state.get("chat_id")
    if not chat_id:
        raise ChatSessionError("No se encontró un chat_id válido en el estado actual.")

    UI.info("Verificando la estructura del chat en Google Drive...")
    chat_data = ctx.api.get_chat_ia_studio(chat_id)
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
    success = ctx.api.append_message(chat_id, message, role=role)

    if success:
        UI.success(f"Mensaje insertado con rol '{role}'.")
        if ctx.bridge_server and ctx.bridge_server.clients:
            UI.info("Puente activo detectado. Recargando pestaña...")
            ctx.bridge_server.broadcast_reload(chat_id)

            if role == "user":
                time.sleep(1.2)
                UI.info("Iniciando ejecución remota en Google AI Studio...")
                run_status = ctx.bridge_server.trigger_browser_run(chat_id)
                if run_status.get("success"):
                    UI.success(run_status.get("message", ""))
                else:
                    raise ChatSessionError(f"Fallo en ejecución automática: {run_status.get('message')}")
        else:
            UI.info("Recuerda recargar la pestaña en Google AI Studio (F5) para aplicar los cambios.")
    else:
        raise ChatSessionError("Error al escribir el mensaje en Google Drive.")


@registry.register("run", "r", require_chat=True)
def cmd_run(ctx: SessionContext, args: str):
    chat_id = ctx.state.get("chat_id")
    if not chat_id:
        raise ChatSessionError("No se encontró el chat_id en el estado actual.")

    if not ctx.bridge_server:
        raise ChatSessionError("El servidor de puente no está activo o inicializado.")

    UI.info("Iniciando ejecución remota en AI Studio (esperando activación del botón)...")
    status = ctx.bridge_server.trigger_browser_run(chat_id)

    if status.get("success"):
        UI.success(status.get("message", "Ejecución iniciada con éxito."))
    else:
        raise ChatSessionError(f"No se pudo completar la ejecución: {status.get('message')}")


@registry.register("vanish", require_chat=True, allow_in_vanish=True)
def cmd_vanish(ctx: SessionContext, args: str):
    subcommand = args.strip().lower()

    if not subcommand:
        is_vanished = ctx.state.get("vanished", False)
        subcommand = "off" if is_vanished else "on"

    if subcommand == "on":
        if ctx.state.get("vanished", False):
            UI.warn("El modo vanish ya se encuentra activo.")
            return

        chat_id = ctx.state.get("chat_id")
        if not chat_id:
            raise ChatSessionError("No se detectó un chat activo en el estado del proyecto.")

        UI.info("Guardando copia de seguridad del chat (Vanish Stash)...")
        chat_data = ctx.api.get_chat_ia_studio(chat_id)
        if not chat_data:
            raise ChatSessionError("No se pudo descargar el chat desde Drive para realizar el respaldo.")

        save_vanish_stash(ctx.project_path, chat_data.model_dump_json())

        UI.info("Estableciendo pantalla limpia en Google Drive...")
        vanish_chunks = [
            ChunksText(
                text="✨ vanish off ✨",
                role="user"
            )
        ]
        chat_data.chunkedPrompt.chunks = vanish_chunks  # type:ignore
        chat_data.chunkedPrompt.pendingInputs = []

        if ctx.api.update_chat_file(chat_id, chat_data):
            ctx.state["vanished"] = True
            ctx.update_state(ctx.state)
            UI.success("Modo Vanish activado. La conversación se encuentra oculta en Drive.")

            if ctx.bridge_server and ctx.bridge_server.clients:
                UI.info("Enviando señal de recarga al navegador...")
                ctx.bridge_server.broadcast_reload(chat_id)
            else:
                UI.info("Recarga la pestaña en Google AI Studio (F5) para aplicar la vista limpia.")
        else:
            clear_vanish_stash(ctx.project_path)
            raise ChatSessionError("Ocurrió un problema al actualizar el chat en Drive para activar vanish.")

    elif subcommand == "off":
        if not ctx.state.get("vanished", False):
            UI.warn("El modo vanish no está activo en este momento.")
            return

        chat_id = ctx.state.get("chat_id")
        stashed_json = load_vanish_stash(ctx.project_path)

        if not stashed_json:
            ctx.state["vanished"] = False
            ctx.update_state(ctx.state)
            raise ChatSessionError("No se encontró el archivo de respaldo de Vanish para restaurar.")

        UI.info("Restaurando conversación y contexto original...")
        assert chat_id is not None
        success = ctx.api.gdm.update_file_from_memory(
            file_id=chat_id,
            content=stashed_json,
            mime_type=ctx.api.MIME_PROMPT
        )

        if success:
            clear_vanish_stash(ctx.project_path)
            ctx.state["vanished"] = False
            ctx.update_state(ctx.state)
            UI.success("¡Chat original restaurado con éxito! Saliendo del modo Vanish.")

            if ctx.bridge_server and ctx.bridge_server.clients:
                UI.info("Enviando señal de recarga al navegador...")
                ctx.bridge_server.broadcast_reload(chat_id)
            else:
                UI.info("Recarga la pestaña en Google AI Studio (F5) para ver el chat recuperado.")
        else:
            raise ChatSessionError("No se pudo escribir el archivo original en Drive para desactivar vanish.")
    else:
        raise InvalidCommandArgumentError("Subcomando inválido. Uso sugerido: 'vanish on' o 'vanish off'")
