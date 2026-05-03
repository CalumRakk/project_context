from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional

import typer
from rich.table import Table

from project_context.api_drive import AIStudioDriveManager
from project_context.history import SnapshotManager
from project_context.ops import (
    generate_commit_prompt_text,
    rebuild_project_context,
    resolve_image_paths,
    sync_images,
    update_context,
)
from project_context.schema import ChunksText
from project_context.ui.editor import run_editor_mode
from project_context.utils import (
    IMAGE_INSERTION_PROMPT,
    IMAGE_INSERTION_RESPONSE,
    UI,
    console,
    get_potential_media_folders,
    save_project_context_state,
)


def prompt_for_media_folder(project_path: Path) -> Optional[Path]:
    """Interfaz de usuario para resolver el vacío de la carpeta de imágenes."""
    typer.secho(
        "\n[?] Se detectaron referencias tipo WikiLink (Obsidian).",
        fg=typer.colors.CYAN,
    )
    candidates = get_potential_media_folders(project_path)

    typer.echo("¿En qué carpeta debería buscar los archivos adjuntos?")
    for i, folder in enumerate(candidates, 1):
        typer.echo(f" {i}) {folder.relative_to(project_path)}")
    typer.echo(" n) Escribir ruta manualmente")
    typer.echo(" s) Saltar estas imágenes")

    choice = input("Selección: ").strip().lower()

    if choice == "s":
        return None
    if choice == "n":
        manual = input("Ruta desde la raíz del proyecto: ").strip()
        return project_path / manual

    try:
        idx = int(choice) - 1
        if 0 <= idx < len(candidates):
            return candidates[idx]
    except ValueError:
        pass
    return None

def command_help():
    """Muestra la ayuda con un formato más limpio."""
    console.print("\n[bold cyan]Comandos Disponibles:[/]")
    help_text = (
        "  [bold]commit[/]             - Enviar git diff (staged) al chat.\n"
        "  [bold]edit[/]               - Abrir editor visual de historial.\n"
        "  [bold]monitor on/off[/]     - Auto-guardado de historial.\n"
        "  [bold]save <msg>[/]         - Snapshot manual con nombre.\n"
        "  [bold]history[/]            - Ver puntos de restauración.\n"
        "  [bold]restore <id>[/]       - Restaurar chat y contexto.\n"
        "  [bold]clear[/]              - Limpiar historial del chat en Drive.\n"
        "  [bold]update[/]             - Forzar actualización de contexto.\n"
        "  [bold]reset[/]              - Reconstrucción total del chat.\n"
        "  [bold]images <archivo>[/]   - Sincroniza imágenes referenciadas.\n"
        "  [bold]context <ruta>[/]     - Enfoca el contexto en una ruta específica.\n"
        "  [bold]fix[/]                - Reparar estructura interna del chat.\n"
        "  [bold]exit / quit[/]        - Salir de la sesión.\n"
    )
    console.print(help_text)

@dataclass
class SessionContext:
    api: AIStudioDriveManager
    state: dict
    project_path: Path
    monitor: SnapshotManager
    session_media_root: Optional[Path] = None

    def stop_monitor(self):
        self.monitor.stop_monitoring()

    def start_monitor(self):
        if self.state.get("monitor_active", False):
            self.monitor.start_monitoring()

    def update_state(self, new_state: dict):
        """Actualiza el estado en memoria, en el monitor y guarda en disco."""
        self.state = new_state
        self.monitor.state = new_state
        save_project_context_state(self.project_path, new_state)


class CommandRegistry:
    def __init__(self):
        # La función devuelve False si se debe detener el bucle principal
        self.commands: Dict[str, Callable[[SessionContext, str], Optional[bool]]] = {}

    def register(self, *names: str):
        def decorator(func: Callable[[SessionContext, str], Optional[bool]]):
            for name in names:
                self.commands[name] = func
            return func
        return decorator

registry = CommandRegistry()


@registry.register("exit", "quit")
def cmd_exit(ctx: SessionContext, args: str):
    ctx.stop_monitor()
    UI.info("Cerrando sesión...")
    return False

@registry.register("help")
def cmd_help(ctx: SessionContext, args: str):
    command_help()

@registry.register("edit")
def cmd_edit(ctx: SessionContext, args: str):
    ctx.stop_monitor()
    run_editor_mode(ctx.api, ctx.state["chat_id"])
    UI.info("Reactivando monitor de historial automático...")
    command_help()
    ctx.start_monitor()

@registry.register("save")
def cmd_save(ctx: SessionContext, args: str):
    if not args.strip():
        UI.warn("Debes proveer un mensaje: `save mi_cambio_importante`")
    else:
        ctx.monitor.create_named_snapshot(args.strip())

@registry.register("monitor")
def cmd_monitor(ctx: SessionContext, args: str):
    if args == "on":
        ctx.monitor.start_monitoring()
        if not ctx.state.get("monitor_active"):
            ctx.state["monitor_active"] = True
            ctx.update_state(ctx.state)
    elif args == "off":
        ctx.stop_monitor()
        if ctx.state.get("monitor_active"):
            ctx.state["monitor_active"] = False
            ctx.update_state(ctx.state)
    else:
        UI.warn("Uso: monitor on | off")

@registry.register("history")
def cmd_history(ctx: SessionContext, args: str):
    all_ids = ctx.monitor.get_all_snapshot_ids()
    if not all_ids:
        UI.info("No hay historial disponible aún.")
        return

    page_size = 10
    total_snapshots = len(all_ids)

    for i in range(0, total_snapshots, page_size):
        table = Table(
            title=f"Historial de Snapshots ({i+1}-{min(i+page_size, total_snapshots)} de {total_snapshots})",
            show_header=True,
            header_style="bold magenta",
        )
        table.add_column("Timestamp (ID)", style="dim", no_wrap=True)
        table.add_column("Fecha/Hora", no_wrap=True)
        table.add_column("Mensaje", style="cyan")

        current_chunk = all_ids[i : i + page_size]
        for tid in current_chunk:
            info = ctx.monitor.get_snapshot_info(tid)
            if info:
                table.add_row(
                    info["timestamp"],
                    info["human_time"],
                    info.get("message") or "-",
                )

        console.print(table)

        if i + page_size < total_snapshots:
            prompt_msg = f"[bold yellow]-- Presiona ENTER para ver más ({total_snapshots - (i + page_size)} restantes) o 'q' para salir --[/]"
            choice = console.input(prompt_msg).strip().lower()
            if choice == "q":
                break
        else:
            UI.info("Fin del historial.")

@registry.register("restore")
def cmd_restore(ctx: SessionContext, args: str):
    if not args:
        UI.warn("Especifica el ID del snapshot.")
        return

    confirm = console.input(f"[bold red]¿Restaurar snapshot {args}? (s/n): [/]")
    if confirm.lower() == "s":
        ctx.stop_monitor()
        if ctx.monitor.restore_snapshot(args.strip()):
            UI.success("Chat restaurado. Recarga AI Studio.")

@registry.register("clear")
def cmd_clear(ctx: SessionContext, args: str):
    if ctx.api.clear_chat_ia_studio(ctx.state["chat_id"]):
        UI.success("Historial de mensajes limpiado en Drive.")

@registry.register("update")
def cmd_update(ctx: SessionContext, args: str):
    ctx.stop_monitor()
    new_state = update_context(ctx.api, ctx.project_path, ctx.state)
    ctx.update_state(new_state)
    print("Puedes reactivar el monitor con 'monitor on'.")

@registry.register("reset")
def cmd_reset(ctx: SessionContext, args: str):
    confirm = console.input("[bold red]¿Reconstruir chat y contexto por completo? (s/n): [/]")
    if confirm.lower() == "s":
        ctx.stop_monitor()
        new_state = rebuild_project_context(ctx.api, ctx.project_path, ctx.state)
        ctx.update_state(new_state)
        ctx.start_monitor()

@registry.register("commit")
def cmd_commit(ctx: SessionContext, args: str):
    subcommand = args.strip().lower()

    if subcommand in ["clean", "clear", "rm"]:
        UI.info("Limpiando bloques de commit...")
        removed = ctx.api.remove_commit_tasks(ctx.state["chat_id"])
        if removed > 0:
            UI.success(f"Se eliminaron {removed} bloques.")
        else:
            UI.info("Nada que limpiar.")
        return

    has_pending = ctx.api.has_pending_commit_suggestion(ctx.state["chat_id"])
    if has_pending and subcommand != "force":
        UI.warn("Ya hay una sugerencia de commit pendiente en el chat.")
        UI.info("Usa 'commit clean' para borrarla o 'commit force' para ignorar.")
        return

    UI.info("Obteniendo cambios de Git...")
    prompt_text = generate_commit_prompt_text(ctx.project_path)

    if not prompt_text:
        UI.warn("No hay cambios en stage. Usa `git add` primero.")
        return

    UI.info("Enviando prompt a Drive...")
    if ctx.api.append_message(ctx.state["chat_id"], prompt_text, role="user"):
        UI.success("¡Listo! Prompt de commit agregado. Ve a AI Studio y presiona RUN.")
    else:
        UI.error("Error al guardar en Drive.")

@registry.register("images")
def cmd_images(ctx: SessionContext, args: str):
    if not args:
        UI.warn("Uso: images <archivo.md>")
        return

    try:
        found_paths, missing = resolve_image_paths(
            ctx.project_path, args, ctx.session_media_root
        )
        if missing and not ctx.session_media_root:
            UI.warn(f"No se encontraron: {', '.join(missing[:3])}...")
            ctx.session_media_root = prompt_for_media_folder(ctx.project_path)

            if ctx.session_media_root:
                found_paths_2, missing_2 = resolve_image_paths(
                    ctx.project_path, args, ctx.session_media_root
                )
                found_paths = list(set(found_paths + found_paths_2))
                missing = missing_2

        if not found_paths:
            UI.warn("No se pudo resolver ninguna ruta de imagen válida.")
            if missing:
                UI.info(f"Faltantes: {missing}")
            return

        ctx.stop_monitor()

        chunks_to_add = []
        chunks_to_add.append(
            ChunksText(
                text=IMAGE_INSERTION_PROMPT.format(filename=args),
                role="user",
            )
        )

        image_chunks = sync_images(
            ctx.api, ctx.project_path, specific_files=found_paths
        )
        chunks_to_add.extend(image_chunks)

        chunks_to_add.append(
            ChunksText(
                text=IMAGE_INSERTION_RESPONSE.format(filename=args),
                role="model",
            )
        )

        if ctx.api.append_chunks(ctx.state["chat_id"], chunks_to_add):
            typer.secho(f"¡{len(found_paths)} imágenes inyectadas!", fg=typer.colors.GREEN)
        else:
            UI.error("Error al subir imágenes al chat.")

        ctx.start_monitor()

    except FileNotFoundError:
        UI.error(f"El archivo '{args}' no existe en el proyecto.")
    except Exception as e:
        UI.error(f"Error procesando imágenes: {e}")

@registry.register("fix")
def cmd_fix(ctx: SessionContext, args: str):
    UI.info("Analizando chat...")
    ctx.stop_monitor()

    fixed_count = ctx.api.repair_chat_structure(ctx.state["chat_id"])

    if fixed_count > 0:
        UI.success(f"¡Sanación completada! {fixed_count} bloques corregidos.")
    else:
        UI.success("El chat está sano o no se pudo acceder.")

    ctx.start_monitor()

@registry.register("context")
def cmd_context(ctx: SessionContext, args: str):
    if not args or args.strip().lower() == "reset":
        UI.info("Restableciendo contexto a todo el proyecto...")
        ctx.state["context_scope"] = None
        new_state = update_context(ctx.api, ctx.project_path, ctx.state)
        ctx.update_state(new_state)
    else:
        target_rel_path = args.strip()
        full_target_path = ctx.project_path / target_rel_path

        if not full_target_path.exists():
            UI.error(f"La ruta '{target_rel_path}' no existe en el proyecto.")
            return

        UI.info(f"Enfocando contexto en: [bold]{target_rel_path}[/]")
        ctx.state["context_scope"] = target_rel_path
        new_state = update_context(ctx.api, ctx.project_path, ctx.state)
        ctx.update_state(new_state)
        UI.success(f"Ahora el modelo solo ve '{target_rel_path}'.")
