from rich.table import Table

from project_context.commands.interactive.register import ParsedArgs, SessionContext
from project_context.core.schemas import ChunkText
from project_context.ui import UI, console


def cmd_clear(ctx: SessionContext, args: ParsedArgs):
    """Limpia el historial de la conversación manteniendo el contexto inicial."""
    state = ctx.project_context.load_state()
    if state.chat_id is None:
        UI.error("No se encontró una sesión de chat activa para limpiar el historial.")
        return

    if ctx.context_service.restore_backup_if_exists():
        UI.info("Restaurado chat original desde el respaldo local.")
    else:
        ctx.api.clear_chat(state.chat_id)
        UI.success("Historial de mensajes limpiado en Drive.")


def cmd_update(ctx: SessionContext, args: ParsedArgs):
    """Actualiza el contexto general y reconstruye el chat."""
    anchor_file_path = ctx.project_context.local_dir / "story_anchor.txt"

    if anchor_file_path.exists():
        story_anchor_rel = anchor_file_path.read_text(encoding="utf-8").strip()
        UI.info("Modo historia detectado activo. Reconstruyendo prompt del chat...")
        from project_context.core.story_ops import apply_story_update

        apply_story_update(ctx.api, ctx.project_context, story_anchor_rel, ctx)
    else:
        ctx.context_service.create_or_update_chat()


def cmd_save(ctx: SessionContext, args: ParsedArgs):
    """Crea de forma manual un snapshot de respaldo etiquetado con un mensaje."""
    if not args.args:
        UI.warn(
            "Debes proveer una descripción para el snapshot: `save mi_cambio_importante`"
        )
        return

    message = " ".join(args.args)
    UI.info("Iniciando la creación del snapshot...")

    timestamp = ctx.snapshot_manager.create_named_snapshot(message, category="user")

    if timestamp:
        UI.success(f"Snapshot guardado exitosamente. ID: [bold cyan]{timestamp}[/]")
    else:
        UI.error(
            "No se pudo crear el snapshot. Asegúrate de tener una sesión de chat activa."
        )


def cmd_restore(ctx: SessionContext, args: ParsedArgs):
    """Restaura un snapshot guardado a partir de su ID."""
    snapshot_id = args.get_arg(0)
    assert snapshot_id is not None  # Verificado previamente en el parsing

    UI.info(f"Iniciando la restauración del snapshot {snapshot_id}...")

    success = ctx.snapshot_manager.restore_snapshot(snapshot_id)
    if success:
        UI.success(f"Snapshot {snapshot_id} restaurado con éxito.")
        UI.info(
            "Por favor, actualiza la interfaz de Google AI Studio (F5) si tienes el chat abierto."
        )
    else:
        UI.error(
            f"No se pudo restaurar el snapshot con ID '{snapshot_id}'. Verifica si el ID es correcto."
        )


def cmd_history(ctx: SessionContext, args: ParsedArgs):
    """Muestra el historial de snapshots guardados en la base de datos de forma paginada."""
    snapshots = ctx.snapshot_manager.list_snapshots()

    if not snapshots:
        UI.info("No se encontraron snapshots registrados en este proyecto.")
        return

    PAGE_SIZE = 5
    total_snaps = len(snapshots)
    total_pages = (total_snaps + PAGE_SIZE - 1) // PAGE_SIZE

    page = 1
    show_all = args.has_flag("--all")

    if not show_all and args.args:
        arg = args.get_arg(0)
        if arg and arg.isdigit():
            page = int(arg)
            if page < 1 or page > total_pages:
                UI.warn(f"Página fuera de rango. Rango disponible: 1 a {total_pages}.")
                return
        else:
            UI.warn(
                "Uso sugerido: [dim]history [número_página][/] o [dim]history --all[/]"
            )
            return

    if show_all:
        items_to_show = snapshots
        title_suffix = " (Todos)"
    else:
        start_idx = (page - 1) * PAGE_SIZE
        end_idx = start_idx + PAGE_SIZE
        items_to_show = snapshots[start_idx:end_idx]
        title_suffix = f" (Página {page}/{total_pages})"

    table = Table(
        title=f"[bold cyan]Historial de Snapshots{title_suffix}[/]",
        show_header=True,
        header_style="bold magenta",
        box=None,
    )
    table.add_column("ID", style="bold green", justify="right")
    table.add_column("Fecha/Hora", style="dim white")
    table.add_column("Categoría", style="cyan")
    table.add_column("Creador", style="blue")
    table.add_column("Mensaje/Descripción", style="white")

    for snap in items_to_show:
        category = snap.get("category", "user")

        category_color = "cyan"
        if category == "system":
            category_color = "orange1"
        elif category == "commit":
            category_color = "green"

        table.add_row(
            snap.get("timestamp", "N/A"),
            snap.get("human_time", "N/A"),
            f"[{category_color}]{category}[/]",
            snap.get("creator_email", "N/A"),
            snap.get("message", "Sin descripción"),
        )

    console.print("")
    console.print(table)
    console.print("")

    if not show_all and page < total_pages:
        UI.tip(
            f"Hay {total_snaps - (page * PAGE_SIZE)} snapshots adicionales ocultos.",
            commands=[f"history {page + 1}", "history --all"],
        )


def cmd_fixfinish(ctx: SessionContext, args: ParsedArgs):
    """Establece el valor 'finishReason' en 'STOP' para todos los bloques de texto del chat."""
    state = ctx.project_context.load_state()
    if not state.chat_id:
        UI.error("No se encontró una sesión de chat activa.")
        return

    UI.info("Descargando estructura del chat activo...")
    chat_data = ctx.api.get_chat(state.chat_id)
    if not chat_data:
        UI.error("No se pudo obtener el chat desde Google Drive.")
        return

    modified_count = 0
    for chunk in chat_data.chunkedPrompt.chunks:
        if isinstance(chunk, ChunkText):
            chunk.finishReason = "STOP"
            modified_count += 1

    if modified_count == 0:
        UI.warn("No se encontraron bloques ChunkText en el chat.")
        return

    UI.info(f"Actualizando {modified_count} bloque(s) de texto en Google Drive...")
    ctx.api.update_chat(state.chat_id, chat_data)

    UI.success(
        f"Se estableció 'finishReason' en 'STOP' para {modified_count} bloque(s)."
    )
    UI.info("Recarga la interfaz de Google AI Studio (F5) para ver los cambios.")
