from typing import List

from project_context.commands.interactive.register import SessionContext
from project_context.ui import UI
from project_context.utils import get_context_tree


def cmd_context_info(ctx: SessionContext, args: List[str]):
    """Muestra el estado actual del enfoque de contexto y la estructura del árbol."""
    items = ctx.workspace.context_items

    UI.print("[bold cyan]Estado del Enfoque del Contexto:[/]")

    if not items.files and not items.folders:
        UI.info("Ámbito actual: [bold green]Todo el proyecto (Raíz)[/]")
    else:
        UI.info("Ámbito actual: [bold yellow]Enfoque Específico (Stage)[/]")
        if items.files:
            UI.print("  [bold]Archivos en enfoque:[/]")
            for f in items.files:
                UI.print(f"    - {f}")
        if items.folders:
            UI.print("  [bold]Carpetas en enfoque:[/]")
            for d in items.folders:
                UI.print(f"    - {d}")
        if items.exclusions:
            UI.print("  [bold]Exclusiones explícitas:[/]")
            for e in items.exclusions:
                UI.print(f"    - {e}")

    UI.print("\n[bold]Estructura que se enviará a Drive:[/]")
    try:
        tree_structure = get_context_tree(ctx.project_path, context_items=items)
        UI.print(f"[dim]{tree_structure}[/]")
    except Exception as e:
        UI.error(f"No se pudo generar la vista del árbol: {e}")

    return True
