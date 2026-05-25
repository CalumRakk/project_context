from pathlib import Path

from project_context.ui.registry import SessionContext, registry
from project_context.utils import UI, console, get_context_tree


def _ensure_context_items(ctx: SessionContext) -> dict:
    if "context_items" not in ctx.state:
        ctx.state["context_items"] = {"files": [], "folders": [], "exclusions": []}
    items = ctx.state["context_items"]
    if "exclusions" not in items:
        items["exclusions"] = []
    if "files" not in items:
        items["files"] = []
    if "folders" not in items:
        items["folders"] = []
    return items


@registry.register("tree", require_chat=True)
def cmd_tree(ctx: SessionContext, args: list[str]):
    """Muestra el árbol de directorios que el modelo puede visualizar actualmente."""
    UI.info("Generando árbol del contexto actual...")
    tree_str = get_context_tree(ctx.project_path, ctx.state.get("context_items"))
    console.print(f"\n[cyan]{tree_str}[/cyan]\n")


@registry.register("context", require_chat=True)
def cmd_context(ctx: SessionContext, args: list[str]):
    """Muestra instrucciones de uso para la gestión del enfoque de contexto."""
    UI.warn("Uso: context <add|rm|ls|reset> [rutas...]")


@registry.register("context:add", require_chat=True)
def cmd_context_add(ctx: SessionContext, args: list[str]):
    """Añade archivos o carpetas al enfoque del contexto activo."""
    items = _ensure_context_items(ctx)
    if not args:
        UI.warn("Especifica al menos una ruta. Ej: context add src/main.py docs/")
        return

    added_count = 0
    for target in args:
        full_path = ctx.project_path / target
        if not full_path.exists():
            UI.warn(f"Ignorado: '{target}' no existe.")
            continue

        rel_path = str(full_path.relative_to(ctx.project_path).as_posix())

        if rel_path in items["exclusions"]:
            items["exclusions"].remove(rel_path)
            UI.info(f"Se revirtió el descarte previo de '{rel_path}'.")

        if full_path.is_file():
            if rel_path not in items["files"]:
                items["files"].append(rel_path)
                added_count += 1
        elif full_path.is_dir():
            if rel_path not in items["folders"]:
                items["folders"].append(rel_path)
                added_count += 1

    if added_count > 0:
        ctx.update_state(ctx.state)
        UI.success(f"Se añadieron {added_count} elementos al contexto.")
        UI.info("Ejecuta [bold cyan]update[/] para sincronizar los cambios con Drive.")
    else:
        UI.info("No se añadieron elementos nuevos.")


@registry.register("context:rm", "context:remove", require_chat=True)
def cmd_context_rm(ctx: SessionContext, args: list[str]):
    """Elimina elementos del enfoque de contexto o los excluye explícitamente."""
    items = _ensure_context_items(ctx)
    if not args:
        UI.warn(
            "Especifica qué quieres eliminar o excluir. Ej: context rm viajes/cascada"
        )
        return

    removed = 0
    for target in args:
        try:
            full_path = ctx.project_path / target
            rel_path = str(full_path.relative_to(ctx.project_path).as_posix())
        except ValueError:
            rel_path = target

        if rel_path in items["exclusions"]:
            items["exclusions"].remove(rel_path)
            removed += 1
            UI.success(
                f"Se eliminó la exclusión sobre: '{rel_path}' (volverá a ser incluido)."
            )
            continue

        if rel_path in items["files"]:
            items["files"].remove(rel_path)
            removed += 1
            UI.success(f"Se eliminó '{rel_path}' de los archivos enfocados.")
            continue

        if rel_path in items["folders"]:
            items["folders"].remove(rel_path)
            removed += 1
            UI.success(f"Se eliminó la carpeta '{rel_path}' del enfoque.")

            parent_path = Path(rel_path)
            updated_exclusions = []
            cascade_count = 0

            for exclusion in items["exclusions"]:
                exc_path = Path(exclusion)
                try:
                    exc_path.relative_to(parent_path)
                    cascade_count += 1
                except ValueError:
                    updated_exclusions.append(exclusion)

            items["exclusions"] = updated_exclusions
            if cascade_count > 0:
                UI.info(
                    f"Limpieza en cascada: Se eliminaron {cascade_count} exclusiones huérfanas bajo '{rel_path}'."
                )
            continue

        target_path = Path(rel_path)
        is_sub_element = False
        for folder in items["folders"]:
            folder_path = Path(folder)
            try:
                target_path.relative_to(folder_path)
                is_sub_element = True
                break
            except ValueError:
                pass

        if is_sub_element:
            if rel_path not in items["exclusions"]:
                items["exclusions"].append(rel_path)
                removed += 1
                UI.success(
                    f"Se excluyó '{rel_path}' del análisis de su carpeta contenedora."
                )
        else:
            UI.warn(
                f"El elemento '{rel_path}' no está en el enfoque ni pertenece a ninguna carpeta activa."
            )

    if removed > 0:
        ctx.update_state(ctx.state)
        UI.info("Ejecuta [bold cyan]update[/] para sincronizar los cambios con Drive.")


@registry.register("context:ls", "context:list", require_chat=True)
def cmd_context_ls(ctx: SessionContext, args: list[str]):
    """Lista las exclusiones, carpetas y archivos específicos del enfoque actual."""
    items = _ensure_context_items(ctx)
    has_files = len(items["files"]) > 0
    has_folders = len(items["folders"]) > 0
    has_exclusions = len(items["exclusions"]) > 0

    if not has_files and not has_folders:
        UI.info(
            "Contexto actual: [bold green]Proyecto Completo[/] (No hay filtros específicos)."
        )
        return

    console.print("\n[bold cyan]Contexto Específico (Stage):[/]")
    if has_files:
        console.print("  [bold]Archivos enfocados:[/]")
        for f in items["files"]:
            console.print(f"    - {f}")
    if has_folders:
        console.print("  [bold]Carpetas enfocadas:[/]")
        for d in items["folders"]:
            console.print(f"    - {d}/")
    if has_exclusions:
        console.print("  [bold red]Exclusiones aplicadas (Descartes):[/]")
        for exc in items["exclusions"]:
            console.print(f"    - {exc}")
    print("")


@registry.register("context:reset", require_chat=True)
def cmd_context_reset(ctx: SessionContext, args: list[str]):
    """Restablece los filtros aplicados para volver a evaluar el proyecto completo."""
    ctx.state["context_items"] = {"files": [], "folders": [], "exclusions": []}
    ctx.update_state(ctx.state)
    UI.success("Contexto restablecido. Ahora el modelo verá todo el proyecto.")
    UI.info("Ejecuta [bold cyan]update[/] para sincronizar los cambios con Drive.")
