from rich.table import Table

from project_context.commands.interactive.register import SessionContext
from project_context.ops import create_or_update_chat, restore_chat_backup_if_exists
from project_context.ui import UI, console
from project_context.utils import get_context_tree


def cmd_clear(ctx: SessionContext, args: list[str]):
    """Limpia el historial de la conversación manteniendo el contexto inicial."""

    state = ctx.project_context.load_state()
    if state.chat_id is None:
        UI.error("No se encontró una sesión de chat activa para limpiar el historial.")
        return

    if restore_chat_backup_if_exists(ctx.api, ctx.project_context):
        UI.info("Restaurado chat original desde el respaldo local.")
    else:
        ctx.api.clear_chat(state.chat_id)
        UI.success("Historial de mensajes limpiado en Drive.")


def cmd_update(ctx: SessionContext, args: list[str]):
    """Actualiza el contenido del archivo de contexto en Drive."""

    create_or_update_chat(ctx.api, ctx.project_context)

    state = ctx.project_context.load_state()
    clean_args_list = [
        arg for arg in args if arg not in ["--force", "-f", "force", "--run", "-r"]
    ]
    has_focus = bool(state.context_items.files or state.context_items.folders)
    if "tree" in clean_args_list or has_focus:
        UI.info("Árbol de archivos enviado:")
        tree_str = get_context_tree(
            ctx.project_context.project_path, state.context_items
        )
        print(f"\n[dim cyan]{tree_str}[/]\n")


def cmd_save(ctx: SessionContext, args: list[str]):
    """Crea de forma manual un snapshot de respaldo etiquetado con un mensaje."""
    if not args:
        UI.warn(
            "Debes proveer una descripción para el snapshot: `save mi_cambio_importante`"
        )
        return

    message = " ".join(args)
    UI.info("Iniciando la creación del snapshot...")

    timestamp = ctx.snapshot_manager.create_named_snapshot(message, category="user")

    if timestamp:
        UI.success(f"Snapshot guardado exitosamente. ID: [bold cyan]{timestamp}[/]")
    else:
        UI.error(
            "No se pudo crear el snapshot. Asegúrate de tener una sesión de chat activa."
        )


def cmd_restore(ctx: SessionContext, args: list[str]):
    """Restaura un snapshot guardado a partir de su ID."""
    if not args:
        UI.warn("Debes proveer el ID del snapshot a restaurar: `restore <id>`")
        return

    snapshot_id = args[0]
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


def cmd_history(ctx: SessionContext, args: list[str]):
    """Muestra el historial de snapshots guardados en la base de datos de forma paginada."""

    snapshots = ctx.snapshot_manager.list_snapshots()

    if not snapshots:
        UI.info("No se encontraron snapshots registrados en este proyecto.")
        return

    PAGE_SIZE = 5
    total_snaps = len(snapshots)
    total_pages = (total_snaps + PAGE_SIZE - 1) // PAGE_SIZE

    page = 1
    show_all = False

    # Procesar argumentos del comando
    if args:
        arg = args[0].lower()
        if arg in ("--all", "-a", "all"):
            show_all = True
        elif arg.isdigit():
            page = int(arg)
            if page < 1 or page > total_pages:
                UI.warn(f"Página fuera de rango. Rango disponible: 1 a {total_pages}.")
                return
        else:
            UI.warn(
                "Uso sugerido: [dim]history [número_página][/] o [dim]history --all[/]"
            )
            return

    # Segmentar la lista de snapshots a mostrar
    if show_all:
        items_to_show = snapshots
        title_suffix = " (Todos)"
    else:
        start_idx = (page - 1) * PAGE_SIZE
        end_idx = start_idx + PAGE_SIZE
        items_to_show = snapshots[start_idx:end_idx]
        title_suffix = f" (Página {page}/{total_pages})"

    # Construir tabla visual
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

        # Color diferenciador por tipo de snapshot
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

    # Mostrar sugerencias de navegación si quedan más registros
    if not show_all and page < total_pages:
        UI.tip(
            f"Hay {total_snaps - (page * PAGE_SIZE)} snapshots adicionales ocultos.",
            commands=[f"history {page + 1}", "history --all"],
        )
