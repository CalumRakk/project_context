from rich.table import Table

from project_context.ui.registry import SessionContext, registry
from project_context.utils import UI, console


@registry.register("exit", "quit", manage_monitor=False, allow_in_vanish=True)
def cmd_exit(ctx: SessionContext, args: list[str]):
    """Cierra la sesión interactiva actual de forma segura."""
    ctx.stop_monitor()
    UI.info("Cerrando sesión...")
    return False


@registry.register("help", allow_in_vanish=True)
def cmd_help(ctx: SessionContext, args: list[str]):
    """Muestra la tabla de ayuda con todos los comandos registrados de forma dinámica."""
    table = Table(
        title="[bold cyan]Comandos Disponibles[/]",
        show_header=True,
        header_style="bold magenta",
        box=None,
    )
    table.add_column("Comando(s)", style="bold green", width=25)
    table.add_column("Descripción", style="white")

    # Agrupamos los nombres que comparten la misma función de manejo (alias)
    handler_to_names = {}
    for name, meta in registry.commands.items():
        handler_to_names.setdefault(meta.handler, []).append(name)

    # Ordenamos los grupos alfabéticamente por su alias principal
    sorted_groups = sorted(
        handler_to_names.items(), key=lambda item: sorted(item[1])[0]
    )

    for handler, names in sorted_groups:
        meta = registry.commands[names[0]]
        # Formato de alias: "exit, quit"
        aliases_str = ", ".join(sorted(names))
        table.add_row(aliases_str, meta.description)

    console.print("")
    console.print(table)
    console.print("")
