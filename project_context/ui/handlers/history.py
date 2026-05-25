import typer
from rich.table import Table

from project_context.ui.registry import SessionContext, registry
from project_context.utils import UI, console


@registry.register("save", require_chat=True)
def cmd_save(ctx: SessionContext, args: list[str]):
    """Crea de forma manual un snapshot de respaldo etiquetado con un mensaje."""
    if not args:
        UI.warn("Debes proveer un mensaje: `save mi_cambio_importante`")
    else:
        ctx.monitor.create_named_snapshot(" ".join(args))


@registry.register("monitor", require_chat=True)
def cmd_monitor(ctx: SessionContext, args: list[str]):
    """Activa o desactiva el monitoreo automático de cambios en segundo plano."""
    subcommand = args[0].lower() if args else ""
    if subcommand == "on":
        ctx.monitor.start_monitoring()
        if not ctx.state.get("monitor_active"):
            ctx.state["monitor_active"] = True
            ctx.update_state(ctx.state)
    elif subcommand == "off":
        ctx.stop_monitor()
        if ctx.state.get("monitor_active"):
            ctx.state["monitor_active"] = False
            ctx.update_state(ctx.state)
    else:
        UI.warn("Uso: monitor on | off")


@registry.register("history", require_chat=True)
def cmd_history(ctx: SessionContext, args: list[str]):
    """Muestra de forma paginada y administra los snapshots disponibles."""
    page_size = 10
    current_page = 0

    while True:
        all_ids = ctx.monitor.get_all_snapshot_ids()
        if not all_ids:
            UI.info("No hay historial disponible.")
            break

        total_snapshots = len(all_ids)
        total_pages = (total_snapshots + page_size - 1) // page_size

        if current_page >= total_pages:
            current_page = max(0, total_pages - 1)

        typer.clear()
        start_idx = current_page * page_size
        end_idx = min(start_idx + page_size, total_snapshots)

        table = Table(
            title=f"Historial de Snapshots (Pág. {current_page + 1}/{total_pages} | {start_idx + 1}-{end_idx} de {total_snapshots})",
            show_header=True,
            header_style="bold magenta",
        )
        table.add_column("Timestamp (ID)", style="dim", no_wrap=True)
        table.add_column("Fecha/Hora", no_wrap=True)
        table.add_column("Mensaje", style="cyan")

        page_chunk = all_ids[start_idx:end_idx]
        for tid in page_chunk:
            info = ctx.monitor.get_snapshot_info(tid)
            if info:
                table.add_row(
                    info["timestamp"],
                    info["human_time"],
                    info.get("message") or "-",
                )

        console.print(table)

        options = []
        if current_page < total_pages - 1:
            options.append("[bold cyan][S][/]iguiente")
        if current_page > 0:
            options.append("[bold cyan][A][/]nterior")
        options.append("[bold cyan][D][/]elete")
        options.append("[bold cyan][R][/]ename")
        options.append("[bold cyan][Q][/]uit")

        prompt_msg = f"\nNavegación ({', '.join(options)}): "
        choice = console.input(prompt_msg).strip().lower()

        if choice == "q":
            break
        elif choice == "s" and current_page < total_pages - 1:
            current_page += 1
        elif choice == "a" and current_page > 0:
            current_page -= 1
        elif choice == "d":
            tid = console.input(
                "\n[bold yellow]Ingresa el ID del snapshot a eliminar: [/]"
            ).strip()
            if tid in all_ids:
                confirm = (
                    console.input(
                        f"[bold red]¿Confirmas la eliminación definitiva del snapshot {tid}? (s/n): [/]"
                    )
                    .strip()
                    .lower()
                )
                if confirm == "s":
                    if ctx.monitor.delete_snapshot(tid):
                        UI.success("Snapshot eliminado de forma definitiva.")
                    else:
                        UI.error("No se pudo realizar la eliminación física.")
                else:
                    UI.info("Operación cancelada.")
            else:
                UI.error("ID no encontrado en el historial.")
            console.input("\nPresiona ENTER para continuar...")
        elif choice == "r":
            tid = console.input(
                "\n[bold yellow]Ingresa el ID del snapshot a renombrar: [/]"
            ).strip()
            if tid in all_ids:
                new_msg = console.input(
                    "[bold yellow]Ingresa el nuevo mensaje o descripción: [/]"
                ).strip()
                if new_msg:
                    confirm = (
                        console.input(f"¿Deseas renombrar a: '{new_msg}'? (s/n): ")
                        .strip()
                        .lower()
                    )
                    if confirm == "s":
                        if ctx.monitor.rename_snapshot(tid, new_msg):
                            UI.success("Snapshot renombrado exitosamente.")
                        else:
                            UI.error("No se pudieron guardar los cambios.")
                    else:
                        UI.info("Operación cancelada.")
                else:
                    UI.warn("El mensaje no puede estar vacío.")
            else:
                UI.error("ID no encontrado en el historial.")
            console.input("\nPresiona ENTER para continuar...")


@registry.register("restore", require_chat=True)
def cmd_restore(ctx: SessionContext, args: list[str]):
    """Restaura un estado de chat y contexto previo a partir de su identificador."""
    if not args:
        UI.warn("Especifica el ID del snapshot.")
        return

    snapshot_id = args[0]
    confirm = console.input(f"[bold red]¿Restaurar snapshot {snapshot_id}? (s/n): [/]")
    if confirm.lower() == "s":
        ctx.stop_monitor()
        if ctx.monitor.restore_snapshot(snapshot_id):
            UI.success(
                "Chat restaurado de forma local y en Drive. Recarga AI Studio (F5)."
            )
        ctx.start_monitor()
