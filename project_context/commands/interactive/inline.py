from project_context.commands.interactive.register import ParsedArgs, SessionContext
from project_context.core.exceptions import ChatSessionError
from project_context.ui import UI


def cmd_inline(ctx: SessionContext, args: ParsedArgs):
    """Inserta un texto o pregunta codificado en base64 en formato InlineFile dentro del chat activo."""
    state = ctx.project_context.load_state()
    if not state.chat_id:
        raise ChatSessionError(
            "No se encontró una sesión de chat activa para insertar el texto."
        )

    # Unimos todos los argumentos por espacio para permitir escribir textos largos sin comillas obligatorias
    text_content = " ".join(args.args).strip()
    if not text_content:
        UI.warn("Uso sugerido: inline <escribe tu pregunta o texto aquí>")
        return

    mime_type = "text/plain"

    UI.info("Codificando texto para conversión inline (text/plain)...")

    try:
        content_bytes = text_content.encode("utf-8")
    except Exception as e:
        UI.error(f"No se pudo procesar el texto: {e}")
        return

    UI.info("Descargando estructura de la sesión del chat remoto...")
    chat_data = ctx.api.get_chat(state.chat_id)

    # Crear el bloque inline a partir de los bytes del texto
    inline_chunk = ctx.api.create_inline_file(
        data_bytes=content_bytes,
        mime_type=mime_type,
        role="user",
    )
    chat_data.chunkedPrompt.chunks.append(inline_chunk)

    UI.info("Actualizando chat en Google Drive...")
    ctx.api.update_chat(state.chat_id, chat_data)

    preview = f"{text_content[:40]}..." if len(text_content) > 40 else text_content
    UI.success(
        f"Texto registrado de manera exitosa como InlineFile: [dim]'{preview}'[/]"
    )
    UI.info(
        "Por favor, actualiza la interfaz de Google AI Studio (F5) para visualizar los cambios."
    )
