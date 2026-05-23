import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional

import typer
from rich.table import Table

from project_context.api_drive import AIStudioDriveManager
from project_context.history import SnapshotManager
from project_context.ops import (
    apply_story_update,
    generate_commit_prompt_text,
    rebuild_project_context,
    resolve_image_paths,
    sync_images,
    update_context,
)
from project_context.schema import ChunksDocument, ChunksText
from project_context.server import BrowserBridgeServer
from project_context.ui.editor import run_editor_mode
from project_context.utils import (
    IMAGE_INSERTION_PROMPT,
    IMAGE_INSERTION_RESPONSE,
    UI,
    clear_chat_stash,
    clear_vanish_stash,
    console,
    get_context_tree,
    get_potential_media_folders,
    has_unstaged_changes,
    human_to_int,
    load_chat_stash,
    load_vanish_stash,
    save_chat_stash,
    save_project_context_state,
    # Nuevas importaciones:
    save_vanish_stash,
    stage_all_changes,
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
        "  [bold]transfer <perfil>[/]  - Migrar la sesión actual a otra cuenta.\n"
        "  [bold]tree[/]               - Muestra el árbol de archivos del contexto actual.\n"
        "  [bold]clear[/]              - Limpiar historial del chat en Drive.\n"
        "  [bold]update[/]             - Forzar actualización de contexto.\n"
        "  [bold]reset[/]              - Reconstrucción total del chat.\n"
        "  [bold]tokens <cant>[/]      - Ajustar manualmente los tokens del contexto.\n"
        "  [bold]images <archivo>[/]   - Sincroniza imágenes referenciadas.\n"
        "  [bold]context <ruta>[/]     - Enfoca el contexto en una ruta específica.\n"
        "  [bold]fix[/]                - Reparar estructura interna del chat.\n"
        "  [bold]vanish [on|off][/]    - Ocultar o restaurar el chat de forma temporal.\n"
        "  [bold]exit / quit[/]        - Salir de la sesión.\n"
        "  [bold]story <ruta>[/]      - Entrar al modo co-escritura con un archivo ancla.\n"
        "  [bold]story exit[/]        - Salir del modo co-escritura.\n"
    )
    console.print(help_text)
@dataclass
class SessionContext:
    api: AIStudioDriveManager
    state: dict
    project_path: Path
    monitor: SnapshotManager
    session_media_root: Optional[Path] = None
    bridge_server: Optional[BrowserBridgeServer] = None

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
    page_size = 10
    current_page = 0

    while True:
        # Cargamos los IDs dinámicamente en cada ciclo por si se eliminan elementos
        all_ids = ctx.monitor.get_all_snapshot_ids()
        if not all_ids:
            UI.info("No hay historial disponible.")
            break

        total_snapshots = len(all_ids)
        total_pages = (total_snapshots + page_size - 1) // page_size

        # Ajustar el puntero de página si se eliminan elementos de la última página
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

        # Construcción de opciones en pantalla
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
            tid = console.input("\n[bold yellow]Ingresa el ID del snapshot a eliminar: [/]").strip()
            if tid in all_ids:
                confirm = console.input(f"[bold red]¿Confirmas la eliminación definitiva del snapshot {tid}? (s/n): [/]").strip().lower()
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
            tid = console.input("\n[bold yellow]Ingresa el ID del snapshot a renombrar: [/]").strip()
            if tid in all_ids:
                new_msg = console.input("[bold yellow]Ingresa el nuevo mensaje o descripción: [/]").strip()
                if new_msg:
                    confirm = console.input(f"¿Deseas renombrar a: '{new_msg}'? (s/n): ").strip().lower()
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

@registry.register("restore")
def cmd_restore(ctx: SessionContext, args: str):
    if not args:
        UI.warn("Especifica el ID del snapshot.")
        return

    confirm = console.input(f"[bold red]¿Restaurar snapshot {args}? (s/n): [/]")
    if confirm.lower() == "s":
        ctx.stop_monitor()
        if ctx.monitor.restore_snapshot(args.strip()):
            chat_id = ctx.state.get("chat_id")
            if ctx.bridge_server and ctx.bridge_server.clients and chat_id:
                UI.success("Chat restaurado en Drive. Recargando pestaña en navegador...")
                ctx.bridge_server.broadcast_reload(chat_id)
            else:
                UI.success("Chat restaurado de forma local y en Drive. Recarga AI Studio (F5).")
        ctx.start_monitor()

@registry.register("clear")
def cmd_clear(ctx: SessionContext, args: str):
    chat_id = ctx.state.get("chat_id")
    if not chat_id:
        UI.error("No hay un chat activo.")
        return

    if ctx.api.clear_chat_ia_studio(chat_id):
        if ctx.bridge_server and ctx.bridge_server.clients:
            UI.success("Historial de mensajes limpiado en Drive. Recargando pestaña...")
            ctx.bridge_server.broadcast_reload(chat_id)
        else:
            UI.success("Historial de mensajes limpiado en Drive.")

@registry.register("update")
def cmd_update(ctx: SessionContext, args: str):
    ctx.stop_monitor()

    chat_id = ctx.state.get("chat_id")
    args_list = args.strip().split()

    # Detectar flag de forzado
    force = False
    if any(f in args_list for f in ["--force", "-f", "force"]):
        force = True
        args_list = [arg for arg in args_list if arg not in ["--force", "-f", "force"]]

    # Detectar flag de ejecución automática posterior
    run_after_update = False
    if any(r in args_list for r in ["--run", "-r"]):
        run_after_update = True
        args_list = [arg for arg in args_list if arg not in ["--run", "-r"]]

    clean_args = " ".join(args_list)
    tab_is_focused = False

    # Validación inteligente de enfoque e input del usuario
    if ctx.bridge_server and chat_id and not force:
        status = ctx.bridge_server.check_if_input_empty(chat_id, timeout=1.5)
        tab_is_focused = status.get("focused", False)

        if tab_is_focused:
            if not status.get("isEmpty", True):
                UI.error("Sincronización abortada: Tienes texto escrito en el input de AI Studio.")
                UI.info("Usa 'update --force' para ignorar este aviso o limpia el input en el navegador.")
                ctx.start_monitor()
                return
        else:
            UI.warn("La pestaña del chat no está activa/enfocada en tu navegador.")
            if run_after_update:
                UI.error("No se puede ejecutar de forma automática si la pestaña no está enfocada.")
                ctx.start_monitor()
                return
            UI.info("El contexto se sincronizará en Google Drive, pero deberás recargar manualmente (F5) al regresar al navegador.")

    # Proceder con la sincronización física del contexto (Google Drive)
    if ctx.state.get("story_mode"):
        UI.info("Modo historia activo. Procesando actualización...")
        try:
            # Modificado para inyectar ctx.session_media_root
            new_state = apply_story_update(
                ctx.api,
                ctx.project_path,
                ctx.state,
                media_root_hint=ctx.session_media_root
            )
            ctx.update_state(new_state)
        except Exception as e:
            UI.error(f"Fallo en la actualización de historia: {e}")
    else:
        new_state = update_context(ctx.api, ctx.project_path, ctx.state)
        ctx.update_state(new_state)

        has_focus = bool(ctx.state.get("context_items", {}).get("files") or ctx.state.get("context_items", {}).get("folders"))
        if clean_args == "tree" or has_focus:
            UI.info("Árbol de archivos enviado:")
            tree_str = get_context_tree(ctx.project_path, ctx.state.get("context_items"))
            console.print(f"\n[dim cyan]{tree_str}[/dim cyan]\n")

    # Si la pestaña estaba enfocada, enviamos la señal de recarga física de forma segura
    if ctx.bridge_server and chat_id and tab_is_focused:
        UI.info("Enviando señal de recarga al navegador...")
        ctx.bridge_server.broadcast_reload(chat_id)

        # Si se solicitó la ejecución automática, esperamos un instante a que inicie la recarga
        # y luego disparamos el trigger que esperará a que el botón RUN se habilite.
        if run_after_update:
            time.sleep(1.0)  # Pausa breve para permitir el inicio de la recarga
            UI.info("Esperando que la página recargue para presionar RUN automáticamente...")
            run_status = ctx.bridge_server.trigger_browser_run(chat_id)
            if run_status.get("success"):
                UI.success(run_status.get("message")) # type: ignore
            else:
                UI.error(f"Fallo en la ejecución: {run_status.get('message')}")

    ctx.start_monitor()


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
    is_commit_mode = ctx.state.get("commit_mode", False)

    if subcommand in ["clear", "done", "restore", "rm"]:
        if not is_commit_mode:
            UI.info("No estás en modo commit rápido. No hay nada que restaurar.")
            return

        UI.info("Restaurando chat original desde copia de seguridad...")
        stashed_json = load_chat_stash(ctx.project_path)

        if not stashed_json:
            UI.error("No se encontró el respaldo del chat.")
            ctx.state["commit_mode"] = False
            ctx.update_state(ctx.state)
            return

        ctx.stop_monitor()
        ctx.api.gdm.update_file_from_memory(
            file_id=ctx.state["chat_id"],
            content=stashed_json,
            mime_type=ctx.api.MIME_PROMPT
        )

        clear_chat_stash(ctx.project_path)
        ctx.state["commit_mode"] = False
        ctx.update_state(ctx.state)
        ctx.start_monitor()

        UI.success("¡Chat original restaurado!")
        if ctx.bridge_server and ctx.bridge_server.clients:
            UI.info("Enviando señal de recarga para restaurar el chat en el navegador...")
            ctx.bridge_server.broadcast_reload(ctx.state["chat_id"])
        else:
            UI.info("Ve a AI Studio y REFRESCA LA PÁGINA (F5).")
        return

    if is_commit_mode:
        UI.warn("Ya estás en modo commit. Ve a AI Studio o usa 'commit done' para restaurar.")
        return

    ctx.stop_monitor()

    if subcommand in ["-a", "--all", "all"]:
        UI.info("Añadiendo todos los cambios al stage (git add -A)...")
        stage_all_changes(ctx.project_path)
        subcommand = ""

    UI.info("Obteniendo cambios de Git...")
    prompt_text = generate_commit_prompt_text(ctx.project_path)

    if not prompt_text:
        if has_unstaged_changes(ctx.project_path):
            UI.warn("No hay archivos en stage (git add), pero hay modificaciones locales.")
            confirm = console.input("[bold yellow]¿Quieres añadirlos todos al stage ahora? (s/n): [/]")
            if confirm.lower() == "s":
                stage_all_changes(ctx.project_path)
                prompt_text = generate_commit_prompt_text(ctx.project_path)
                if not prompt_text:
                    UI.error("No se pudo generar el diff.")
                    ctx.start_monitor()
                    return
            else:
                UI.info("Operación cancelada.")
                ctx.start_monitor()
                return
        else:
            UI.warn("El repositorio está limpio. No hay cambios pendientes.")
            ctx.start_monitor()
            return

    UI.info("Guardando copia de seguridad del chat actual (Stash)...")
    chat_id = ctx.state["chat_id"]
    chat_data = ctx.api.get_chat_ia_studio(chat_id)
    if not chat_data:
        UI.error("No se pudo descargar el chat para respaldo.")
        ctx.start_monitor()
        return

    save_chat_stash(ctx.project_path, chat_data.model_dump_json())

    context_chunk = None
    for chunk in chat_data.chunkedPrompt.chunks:
        if getattr(chunk, "role", "") == "user" and hasattr(chunk, "driveDocument"):
            if chunk.driveDocument.id == ctx.state.get("file_id"): # type: ignore
                context_chunk = chunk
                break

    UI.info("Configurando chat minimalista con modelo rápido...")
    fast_chunks = []
    if context_chunk:
        fast_chunks.append(context_chunk)

    fast_chunks.append(ChunksText(text=prompt_text, role="user"))
    chat_data.runSettings.model = "models/gemini-2.5-flash"  # Actualizado a gemini-2.5-flash
    chat_data.chunkedPrompt.chunks = fast_chunks
    chat_data.chunkedPrompt.pendingInputs = []

    if ctx.api.update_chat_file(chat_id, chat_data):
        ctx.state["commit_mode"] = True
        ctx.update_state(ctx.state)
        UI.success("¡Modo commit activado!")

        if ctx.bridge_server and ctx.bridge_server.clients:
            UI.info("Enviando señal de recarga al navegador...")
            ctx.bridge_server.broadcast_reload(chat_id)
            time.sleep(1.2)
            UI.info("Iniciando ejecución remota del commit...")
            run_status = ctx.bridge_server.trigger_browser_run(chat_id)
            if run_status.get("success"):
                UI.success(run_status.get("message")) # type: ignore
            else:
                UI.error(f"Fallo en ejecución: {run_status.get('message')}")
        else:
            UI.info("Ve a AI Studio, REFRESCA LA PÁGINA (F5) y presiona RUN.")
    else:
        UI.error("Error al subir el chat de commit temporal a Drive.")

    ctx.start_monitor()
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
    if "context_items" not in ctx.state:
        ctx.state["context_items"] = {"files": [], "folders": [], "exclusions": []}

    parts = args.strip().split()
    if not parts:
        UI.warn("Uso: context <add|rm|ls|reset> [rutas...]")
        return

    subcmd = parts[0].lower()
    targets = parts[1:]

    items = ctx.state["context_items"]

    # Asegurar inicialización consistente de todas las colecciones
    if "exclusions" not in items:
        items["exclusions"] = []
    if "files" not in items:
        items["files"] = []
    if "folders" not in items:
        items["folders"] = []

    if subcmd == "add":
        if not targets:
            UI.warn("Especifica al menos una ruta. Ej: context add src/main.py docs/")
            return

        added_count = 0
        for target in targets:
            full_path = ctx.project_path / target
            if not full_path.exists():
                UI.warn(f"Ignorado: '{target}' no existe.")
                continue

            rel_path = str(full_path.relative_to(ctx.project_path).as_posix())

            # Si el elemento estaba previamente en la lista de exclusión, se revierte la exclusión
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

    elif subcmd in ["rm", "remove"]:
        if not targets:
            UI.warn("Especifica qué quieres eliminar o excluir. Ej: context rm viajes/cascada")
            return

        removed = 0
        for target in targets:
            try:
                full_path = ctx.project_path / target
                rel_path = str(full_path.relative_to(ctx.project_path).as_posix())
            except ValueError:
                rel_path = target  # En caso de pasar una ruta que ya es relativa o un patrón de descarte

            # Caso A: El objetivo se encuentra directamente en exclusiones
            if rel_path in items["exclusions"]:
                items["exclusions"].remove(rel_path)
                removed += 1
                UI.success(f"Se eliminó la exclusión sobre: '{rel_path}' (volverá a ser incluido).")
                continue

            # Caso B: El objetivo es un archivo explícito de enfoque
            if rel_path in items["files"]:
                items["files"].remove(rel_path)
                removed += 1
                UI.success(f"Se eliminó '{rel_path}' de los archivos enfocados.")
                continue

            # Caso C: El objetivo es una carpeta explícita de enfoque
            if rel_path in items["folders"]:
                items["folders"].remove(rel_path)
                removed += 1
                UI.success(f"Se eliminó la carpeta '{rel_path}' del enfoque.")

                # Ejecutar limpieza en cascada para remover exclusiones dependientes de esta carpeta
                parent_path = Path(rel_path)
                updated_exclusions = []
                cascade_count = 0

                for exclusion in items["exclusions"]:
                    exc_path = Path(exclusion)
                    try:
                        exc_path.relative_to(parent_path)
                        cascade_count += 1
                    except ValueError:
                        # No pertenece al directorio eliminado, se conserva
                        updated_exclusions.append(exclusion)

                items["exclusions"] = updated_exclusions
                if cascade_count > 0:
                    UI.info(f"Limpieza en cascada: Se eliminaron {cascade_count} exclusiones huérfanas bajo '{rel_path}'.")
                continue

            # Caso D: El objetivo no está en el enfoque actual pero pertenece a una carpeta del enfoque.
            # Se agrega como una exclusión dinámica
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
                    UI.success(f"Se excluyó '{rel_path}' del análisis de su carpeta contenedora.")
            else:
                UI.warn(f"El elemento '{rel_path}' no está en el enfoque ni pertenece a ninguna carpeta activa.")

        if removed > 0:
            ctx.update_state(ctx.state)
            UI.info("Ejecuta [bold cyan]update[/] para sincronizar los cambios con Drive.")

    elif subcmd in ["ls", "list"]:
        has_files = len(items["files"]) > 0
        has_folders = len(items["folders"]) > 0
        has_exclusions = len(items["exclusions"]) > 0

        if not has_files and not has_folders:
            UI.info("Contexto actual: [bold green]Proyecto Completo[/] (No hay filtros específicos).")
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
        print("")  # Salto de línea

    elif subcmd == "reset":
        ctx.state["context_items"] = {"files": [], "folders": [], "exclusions": []}
        if "context_scope" in ctx.state:
            ctx.state["context_scope"] = None

        ctx.update_state(ctx.state)
        UI.success("Contexto restablecido. Ahora el modelo verá todo el proyecto.")
        UI.info("Ejecuta [bold cyan]update[/] para sincronizar los cambios con Drive.")

    else:
        UI.warn("Subcomando desconocido. Usa: add, rm, ls, reset.")

@registry.register("transfer")
def cmd_transfer(ctx: SessionContext, args: str):
    target_profile = args.strip()
    if not target_profile:
        UI.warn("Uso: transfer <perfil_destino>")
        return

    from project_context.utils import profile_manager
    current_profile = profile_manager.get_active_profile_name()

    if target_profile == current_profile:
        UI.warn("El perfil destino no puede ser el mismo que el actual.")
        return

    if target_profile not in profile_manager.list_profiles():
        UI.error(f"El perfil '{target_profile}' no existe.")
        UI.info(f"Perfiles disponibles: {', '.join(profile_manager.list_profiles())}")
        return

    confirm = console.input(f"[bold red]¿Migrar sesión de '{current_profile}' hacia '{target_profile}'? (s/n): [/]")
    if confirm.lower() != "s":
        UI.info("Transferencia cancelada.")
        return

    try:
        from project_context.ops import transfer_chat_to_profile

        # Pausar el rastreo de snapshots localmente en el viejo perfil
        ctx.stop_monitor()

        new_api, new_state = transfer_chat_to_profile(
            ctx.api, ctx.state, ctx.project_path, target_profile
        )

        # Actualización de punteros de memoria "en caliente"
        ctx.api = new_api
        ctx.state = new_state

        # Instanciar un nuevo motor de Snapshots atado a la nueva API
        from project_context.history import SnapshotManager
        ctx.monitor = SnapshotManager(new_api, ctx.project_path, new_state)

        # Sobreescribir o crear el state en la ruta del *nuevo* perfil y arrancar
        ctx.update_state(new_state)
        ctx.start_monitor()

        UI.success(f"¡Migración completada! Ahora estás operando nativamente como [bold]{target_profile}[/].")
        UI.warn("RECUERDA: Ve a Google AI Studio, asegúrate de haber cambiado de cuenta de Google y abre el nuevo chat.")

    except Exception as e:
        UI.error(f"Error crítico durante la transferencia: {e}")
        # Intentar restaurar el perfil de manera segura en caso de fallo parcial
        profile_manager.set_active_profile(current_profile)
        ctx.start_monitor()

@registry.register("tree")
def cmd_tree(ctx: SessionContext, args: str):
    """Muestra el árbol de directorio que la IA está viendo actualmente."""
    UI.info("Generando árbol del contexto actual...")
    tree_str = get_context_tree(ctx.project_path, ctx.state.get("context_items"))
    console.print(f"\n[cyan]{tree_str}[/cyan]\n")

@registry.register("story")
def cmd_story(ctx: SessionContext, args: str):
    args = args.strip()

    if not args:
        if ctx.state.get("story_mode"):
            UI.info(f"Modo historia ACTIVO. Ancla actual: [cyan]{ctx.state.get('story_anchor')}[/]")

        UI.warn("Uso: story <archivo.md> o story exit")
        return

    if args.lower() in ["exit", "quit", "off"]:
        ctx.state["story_mode"] = False
        ctx.state["story_anchor"] = None
        ctx.update_state(ctx.state)
        UI.success("Has salido del modo historia.")
        return

    target_file = ctx.project_path / args
    if not target_file.exists():
        UI.error(f"El archivo '{args}' no existe en el proyecto.")
        return

    rel_path = str(target_file.relative_to(ctx.project_path).as_posix())
    context_items = ctx.state.get("context_items", {"files": [], "folders": []})
    has_specific_focus = bool(context_items.get("files") or context_items.get("folders"))

    if has_specific_focus:
        if rel_path not in context_items["files"]:
            context_items["files"].append(rel_path)
            ctx.state["context_items"] = context_items
            ctx.update_state(ctx.state)
            UI.info(f"El archivo [cyan]{rel_path}[/] fue añadido al contexto específico.")

    ctx.stop_monitor()
    UI.info("Iniciando Modo Historia...")
    ctx.state["story_mode"] = True
    ctx.state["story_anchor"] = rel_path
    ctx.update_state(ctx.state)

    try:
        from project_context.ops import apply_story_update
        new_state = apply_story_update(
            ctx.api,
            ctx.project_path,
            ctx.state,
            media_root_hint=ctx.session_media_root
        )
        ctx.update_state(new_state)

        chat_id = ctx.state.get("chat_id")
        if ctx.bridge_server and ctx.bridge_server.clients and chat_id:
            UI.info("Recargando pestaña en el navegador...")
            ctx.bridge_server.broadcast_reload(chat_id)
            time.sleep(1.2)
            UI.info("Iniciando generación automática...")
            run_status = ctx.bridge_server.trigger_browser_run(chat_id)

            if run_status.get("success"):
                UI.success(run_status.get("message")) # type: ignore
            else:
                UI.error(f"No se pudo ejecutar automáticamente: {run_status.get('message')}")
    except Exception as e:
        UI.error(f"Error inicializando modo historia: {e}")

    ctx.start_monitor()

@registry.register("tokens")
def cmd_tokens(ctx: SessionContext, args: str):
    val = args.strip()
    if not val:
        UI.warn("Uso: tokens <cantidad> (ej: tokens 150000 o tokens 150k)")
        return

    try:
        tokens = human_to_int(val)
    except Exception:
        UI.error("Formato de tokens no válido. Usa números enteros o notaciones como '150k'.")
        return

    chat_id = ctx.state.get("chat_id")
    file_id = ctx.state.get("file_id")

    if not chat_id or not file_id:
        UI.error("Falta información del chat o del archivo de contexto en el estado actual.")
        return

    UI.info(f"Actualizando contador de tokens a [bold]{tokens}[/] en Google Drive...")
    ctx.stop_monitor()

    try:
        with ctx.api.modify_chat(chat_id) as chat_data:
            updated_metadata = False
            for chunk in chat_data.chunkedPrompt.chunks:
                if (
                    isinstance(chunk, ChunksDocument)
                    and chunk.driveDocument.id == file_id
                ):
                    chunk.tokenCount = tokens
                    updated_metadata = True
                    break

            if updated_metadata:
                UI.success(f"Contador de tokens actualizado en Drive a: [bold]{tokens}[/]")
            else:
                UI.warn("No se encontró el bloque de contexto del archivo en el chat actual.")
    except Exception as e:
        UI.error(f"Error al intentar modificar los tokens en el chat: {e}")
    finally:
        ctx.start_monitor()

@registry.register("insert", "msg")
def cmd_insert(ctx: SessionContext, args: str):
    args = args.strip()
    if not args:
        UI.warn("Uso: insert [user|ia|model] <mensaje>")
        return

    parts = args.split(" ", 1)
    role_input = parts[0].lower()

    user_aliases = {"user", "usuario", "u"}
    model_aliases = {"model", "ia", "assistant", "modelo", "i"}

    # Determinar rol y mensaje de forma inteligente
    if role_input in user_aliases:
        role = "user"
        message = parts[1].strip() if len(parts) > 1 else ""
    elif role_input in model_aliases:
        role = "model"
        message = parts[1].strip() if len(parts) > 1 else ""
    else:
        # Si no coincide con ningún alias, asumimos que todo es el mensaje del usuario
        role = "user"
        message = args

    if not message:
        UI.warn("El cuerpo del mensaje no puede estar vacío.")
        return

    ctx.stop_monitor()

    chat_id = ctx.state.get("chat_id")
    if not chat_id:
        UI.error("No se encontró un chat_id válido en el estado actual.")
        ctx.start_monitor()
        return

    UI.info("Verificando la estructura del chat en Google Drive...")
    chat_data = ctx.api.get_chat_ia_studio(chat_id)
    if not chat_data:
        UI.error("No se pudo obtener el historial del chat desde Drive.")
        ctx.start_monitor()
        return

    chunks = chat_data.chunkedPrompt.chunks
    if chunks:
        last_chunk = chunks[-1]
        last_role = getattr(last_chunk, "role", None)

        # Regla de validación de alternancia
        if role == "user" and last_role == "user":
            UI.error("Validación fallida: El último bloque del chat ya es de tipo [bold]Usuario[/].")
            UI.info("Ejecuta 'RUN' en AI Studio para obtener la respuesta o inserta un mensaje de tipo 'ia' primero.")
            ctx.start_monitor()
            return
        elif role == "model" and last_role == "model":
            UI.error("Validación fallida: El último bloque del chat ya es de tipo [bold]IA (Model)[/].")
            UI.info("Inserta un mensaje de tipo 'user' primero para mantener la alternancia.")
            ctx.start_monitor()
            return

    UI.info(f"Insertando bloque con rol '{role}'...")
    success = ctx.api.append_message(chat_id, message, role=role)

    if success:
        UI.success(f"Mensaje insertado con rol '{role}'.")
        # Integración con el puente del navegador
        if ctx.bridge_server and ctx.bridge_server.clients:
            UI.info("Puente activo detectado. Recargando pestaña...")
            ctx.bridge_server.broadcast_reload(chat_id)

            if role == "user":
                time.sleep(1.2)  # Pausa para permitir que la pestaña recargue e inicialice el DOM
                UI.info("Iniciando ejecución remota en Google AI Studio...")
                run_status = ctx.bridge_server.trigger_browser_run(chat_id)
                if run_status.get("success"):
                    UI.success(run_status.get("message")) # type: ignore
                else:
                    UI.error(f"Fallo en ejecución automática: {run_status.get('message')}")
        else:
            UI.info("Recuerda recargar la pestaña en Google AI Studio (F5) para aplicar los cambios.")
    else:
        UI.error("Error al escribir el mensaje en Google Drive.")

    ctx.start_monitor()

@registry.register("run", "r")
def cmd_run(ctx: SessionContext, args: str):
    chat_id = ctx.state.get("chat_id")
    if not chat_id:
        UI.error("No se encontró el chat_id en el estado actual.")
        return

    if not ctx.bridge_server:
        UI.error("El servidor de puente no está activo o inicializado.")
        return

    UI.info("Iniciando ejecución remota en AI Studio (esperando activación del botón)...")
    status = ctx.bridge_server.trigger_browser_run(chat_id)

    if status.get("success"):
        UI.success(status.get("message", "Ejecución iniciada con éxito."))
    else:
        UI.error(f"No se pudo completar la ejecución: {status.get('message')}")


@registry.register("vanish")
def cmd_vanish(ctx: SessionContext, args: str):
    subcommand = args.strip().lower()

    # Si no se proporciona argumento, se comporta como un interruptor alterno (toggle)
    if not subcommand:
        is_vanished = ctx.state.get("vanished", False)
        subcommand = "off" if is_vanished else "on"

    if subcommand == "on":
        if ctx.state.get("vanished", False):
            UI.warn("El modo vanish ya se encuentra activo.")
            return

        ctx.stop_monitor()
        chat_id = ctx.state.get("chat_id")
        if not chat_id:
            UI.error("No se detectó un chat activo en el estado del proyecto.")
            ctx.start_monitor()
            return

        UI.info("Guardando copia de seguridad del chat (Vanish Stash)...")
        chat_data = ctx.api.get_chat_ia_studio(chat_id)
        if not chat_data:
            UI.error("No se pudo descargar el chat desde Drive para realizar el respaldo.")
            ctx.start_monitor()
            return

        # Respaldar localmente
        save_vanish_stash(ctx.project_path, chat_data.model_dump_json())

        # Crear estructura limpia
        UI.info("Estableciendo pantalla limpia en Google Drive...")
        vanish_chunks = [
            ChunksText(
                text="✨ vanish off ✨",
                role="user"
            )
        ]
        chat_data.chunkedPrompt.chunks = vanish_chunks
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
            UI.error("Ocurrió un problema al actualizar el chat en Drive.")
            clear_vanish_stash(ctx.project_path)

        ctx.start_monitor()

    elif subcommand == "off":
        if not ctx.state.get("vanished", False):
            UI.warn("El modo vanish no está activo en este momento.")
            return

        ctx.stop_monitor()
        chat_id = ctx.state.get("chat_id")
        stashed_json = load_vanish_stash(ctx.project_path)

        if not stashed_json:
            UI.error("No se encontró el archivo de respaldo de Vanish para restaurar.")
            ctx.state["vanished"] = False
            ctx.update_state(ctx.state)
            ctx.start_monitor()
            return

        UI.info("Restaurando conversación y contexto original...")
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
            UI.error("No se pudo escribir el archivo original en Drive.")

        ctx.start_monitor()
    else:
        UI.warn("Subcomando inválido. Uso sugerido: 'vanish on' o 'vanish off'")
