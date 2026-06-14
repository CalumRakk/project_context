from typing import Optional

from rich.table import Table

from project_context.commands.interactive.register import (
    InteractiveRegistry,
    ParsedArgs,
    SessionContext,
)
from project_context.ui import UI, console


def cmd_exit(ctx: SessionContext, args: ParsedArgs) -> Optional[bool]:
    """Termina la sesión interactiva actual de manera ordenada."""
    UI.info("Cerrando sesión de project_context...")
    return False


def cmd_help(
    ctx: SessionContext, args: ParsedArgs, registry: InteractiveRegistry
) -> Optional[bool]:
    """Muestra la tabla con la lista de comandos disponibles en el registro."""
    table = Table(
        title="[bold cyan]Comandos Disponibles[/]",
        show_header=True,
        header_style="bold magenta",
        box=None,
    )
    table.add_column("Comando(s)", style="bold green", width=25)
    table.add_column("Descripción", style="white")

    handler_to_names = {}
    for name, meta in registry.get_commands_map().items():
        handler_to_names.setdefault(meta.handler, []).append(name)

    for handler, names in sorted(
        handler_to_names.items(), key=lambda item: sorted(item[1])[0]
    ):
        meta = registry.get_commands_map()[names[0]]
        aliases_str = ", ".join(sorted(names))
        table.add_row(aliases_str, meta.description)

    console.print("")
    console.print(table)
    console.print("")
    return True
