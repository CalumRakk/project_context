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
    apply_story_update,
)
from project_context.schema import ChunksDocument, ChunksText
from project_context.ui.editor import run_editor_mode
from project_context.utils import (
    IMAGE_INSERTION_PROMPT,
    IMAGE_INSERTION_RESPONSE,
    UI,
    clear_chat_stash,
    console,
    get_context_tree,
    get_potential_media_folders,
    has_unstaged_changes,
    load_chat_stash,
    save_chat_stash,
    save_project_context_state,
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
        "  [bold]images <archivo>[/]   - Sincroniza imágenes referenciadas.\n"
        "  [bold]context <ruta>[/]     - Enfoca el contexto en una ruta específica.\n"
        "  [bold]fix[/]                - Reparar estructura interna del chat.\n"
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
            UI.success("Chat restaurado. Recarga AI Studio.")

@registry.register("clear")
def cmd_clear(ctx: SessionContext, args: str):
    if ctx.api.clear_chat_ia_studio(ctx.state["chat_id"]):
        UI.success("Historial de mensajes limpiado en Drive.")

@registry.register("update")
def cmd_update(ctx: SessionContext, args: str):
    ctx.stop_monitor()
    # INTERCEPCIÓN MODO HISTORIA
    if ctx.state.get("story_mode"):
        UI.info("Modo historia activo. Procesando actualización...")
        try:
            new_state = apply_story_update(ctx.api, ctx.project_path, ctx.state)
            ctx.update_state(new_state)
        except Exception as e:
            UI.error(f"Fallo en la actualización de historia: {e}")
            UI.info("Asegúrate de que la etiqueta <mejora> esté bien escrita.")

    # FLUJO NORMAL (CÓDIGO)
    else:
        new_state = update_context(ctx.api, ctx.project_path, ctx.state)
        ctx.update_state(new_state)

        # Imprimimos el árbol automáticamente SOLO si el usuario pasó 'tree' como argumento (ej: update tree)
        # o si está usando un contexto enfocado
        has_focus = bool(ctx.state.get("context_items", {}).get("files") or ctx.state.get("context_items", {}).get("folders"))

        if args.strip() == "tree" or has_focus:
            UI.info("Árbol de archivos enviado:")
            tree_str = get_context_tree(ctx.project_path, ctx.state.get("context_items"))
            console.print(f"\n[dim cyan]{tree_str}[/dim cyan]\n")
        elif not has_focus and args.strip() != "tree":
            UI.info("Tip: Ejecuta [bold]tree[/] para ver qué archivos se están rastreando.")

    UI.info("Puedes reactivar el monitor con 'monitor on'.")

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

    # Comprobar si ya estamos en "Modo Commit"
    is_commit_mode = ctx.state.get("commit_mode", False)

    if subcommand in ["clear", "done", "restore", "rm"]:
        if not is_commit_mode:
            UI.info("No estás en modo commit rápido. No hay nada que restaurar.")
            return

        UI.info("Restaurando chat original desde copia de seguridad...")
        stashed_json = load_chat_stash(ctx.project_path)

        if not stashed_json:
            UI.error("No se encontró el respaldo del chat. ¿Se borró accidentalmente?")
            ctx.state["commit_mode"] = False
            ctx.update_state(ctx.state)
            return

        ctx.stop_monitor()
        # Subimos el JSON original directamente a Drive, sobreescribiendo el rápido
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
        UI.info("Ve a AI Studio y REFRESCA LA PÁGINA (F5).")
        return

    # Si ya estamos en modo commit y trata de lanzar otro:
    if is_commit_mode:
        UI.warn("Ya estás en modo commit. Ve a AI Studio, presiona RUN para obtener tu commit.")
        UI.info("Cuando termines, usa [bold cyan]commit done[/] para regresar a tu chat original.")
        return

    # ==========================================
    # FLUJO DE ENTRADA: Secuestrar chat y poner modelo rápido
    # ==========================================
    ctx.stop_monitor()

    # ¿Pusieron el flag de añadir todo?
    if subcommand in ["-a", "--all", "all"]:
        UI.info("Añadiendo todos los cambios al stage (git add -A)...")
        stage_all_changes(ctx.project_path)
        subcommand = ""

    UI.info("Obteniendo cambios de Git...")
    prompt_text = generate_commit_prompt_text(ctx.project_path)

    # Inteligencia de UX: Sugerir hacer add si olvidó hacerlo
    if not prompt_text:
        if has_unstaged_changes(ctx.project_path):
            UI.warn("No hay archivos en stage (git add), PERO tienes archivos modificados.")
            confirm = console.input("[bold yellow]¿Quieres añadirlos todos al stage (git add .) ahora? (s/n): [/]")
            if confirm.lower() == "s":
                stage_all_changes(ctx.project_path)
                prompt_text = generate_commit_prompt_text(ctx.project_path)
                if not prompt_text:
                    UI.error("No se pudo generar el diff incluso después de hacer git add.")
                    ctx.start_monitor()
                    return
            else:
                UI.info("Operación cancelada. Haz `git add` manualmente cuando estés listo.")
                ctx.start_monitor()
                return
        else:
            UI.warn("Tu repositorio está completamente limpio. No hay nada que commitear.")
            ctx.start_monitor()
            return

    # 1. Hacer Stash del chat pesado actual
    UI.info("Guardando copia de seguridad del chat actual en tu disco (Stash)...")
    chat_id = ctx.state["chat_id"]
    chat_data = ctx.api.get_chat_ia_studio(chat_id)
    if not chat_data:
        UI.error("No se pudo descargar el chat actual para hacer la copia de seguridad.")
        ctx.start_monitor()
        return

    save_chat_stash(ctx.project_path, chat_data.model_dump_json())

    # 2. Rescatar el archivo project_context.txt para que el modelo tenga el contexto de la app
    context_chunk = None
    for chunk in chat_data.chunkedPrompt.chunks:
        # Buscamos el chunk del documento que coincide con nuestro file_id maestro
        if getattr(chunk, "role", "") == "user" and hasattr(chunk, "driveDocument"):

            assert isinstance(chunk, ChunksDocument)  # Para que mypy entienda el tipo
            if chunk.driveDocument.id == ctx.state.get("file_id"):
                context_chunk = chunk
                break

    # 3. Construir el nuevo historial minimalista
    UI.info("Configurando chat minimalista con modelo rápido (gemini-2.5-flash)...")
    fast_chunks = []
    if context_chunk:
        fast_chunks.append(context_chunk)

    # Añadimos nuestro prompt con el diff
    fast_chunks.append(ChunksText(text=prompt_text, role="user"))

    # Cambiamos el modelo a uno rápido y reemplazamos los mensajes
    chat_data.runSettings.model = "models/gemini-flash-latest"
    chat_data.chunkedPrompt.chunks = fast_chunks
    chat_data.chunkedPrompt.pendingInputs = []

    # 4. Subir a Drive
    if ctx.api.update_chat_file(chat_id, chat_data):
        ctx.state["commit_mode"] = True
        ctx.update_state(ctx.state)
        UI.success("¡Listo! Modo commit activado de forma exitosa.")
        UI.info("Ve a AI Studio, [bold red]REFRESCA LA PÁGINA (F5)[/] y presiona RUN.")
        UI.info("Cuando hayas hecho tu commit, ejecuta [bold cyan]commit done[/] en esta consola.")
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
        ctx.state["context_items"] = {"files": [], "folders": []}

    parts = args.strip().split()
    if not parts:
        UI.warn("Uso: context <add|rm|ls|reset> [rutas...]")
        return

    subcmd = parts[0].lower()
    targets = parts[1:]

    items = ctx.state["context_items"]

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

            # Normalizamos la ruta para evitar duplicados como './src' y 'src'
            rel_path = str(full_path.relative_to(ctx.project_path).as_posix())

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
            UI.warn("Especifica qué quieres eliminar. Ej: context rm src/main.py")
            return

        removed = 0
        for target in targets:
            # Intentar limpiar la ruta para hacer match
            try:
                full_path = ctx.project_path / target
                rel_path = str(full_path.relative_to(ctx.project_path).as_posix())
            except ValueError:
                rel_path = target  # Por si pasan una ruta ya relativa

            if rel_path in items["files"]:
                items["files"].remove(rel_path)
                removed += 1
            if rel_path in items["folders"]:
                items["folders"].remove(rel_path)
                removed += 1

        if removed > 0:
            ctx.update_state(ctx.state)
            UI.success(f"Se eliminaron {removed} elementos del contexto.")
            UI.info("Ejecuta [bold cyan]update[/] para sincronizar los cambios con Drive.")
        else:
            UI.info("No se encontraron esos elementos en el contexto actual.")

    elif subcmd in ["ls", "list"]:
        has_files = len(items["files"]) > 0
        has_folders = len(items["folders"]) > 0

        if not has_files and not has_folders:
            UI.info("Contexto actual: [bold green]Proyecto Completo[/] (No hay filtros específicos).")
            return

        console.print("\n[bold cyan]Contexto Específico (Stage):[/]")
        if has_files:
            console.print("  [bold]Archivos:[/]")
            for f in items["files"]:
                console.print(f"    - {f}")
        if has_folders:
            console.print("  [bold]Carpetas:[/]")
            for d in items["folders"]:
                console.print(f"    - {d}/")
        print("") # Salto de línea

    elif subcmd == "reset":
        ctx.state["context_items"] = {"files": [], "folders": []}
        # Limpiamos el legacy config por si acaso
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

    # Mostrar estado actual si no pasan argumentos
    if not args:
        if ctx.state.get("story_mode"):
            UI.info(f"Modo historia ACTIVO. Ancla actual: [cyan]{ctx.state.get('story_anchor')}[/]")
        else:
            UI.warn("Uso: story <archivo.md> o story exit")
        return

    # Salir del modo historia
    if args.lower() in ["exit", "quit", "off"]:
        ctx.state["story_mode"] = False
        ctx.state["story_anchor"] = None
        ctx.update_state(ctx.state)
        UI.success("Has salido del modo historia. El comando 'update' vuelve a comportarse normalmente.")
        return

    # Iniciar modo historia
    target_file = ctx.project_path / args
    if not target_file.exists():
        UI.error(f"El archivo '{args}' no existe en el proyecto.")
        return

    rel_path = str(target_file.relative_to(ctx.project_path).as_posix())

    # ==========================================
    # LÓGICA INTELIGENTE DE VALIDACIÓN DE CONTEXTO
    # ==========================================
    context_items = ctx.state.get("context_items", {"files": [], "folders": []})
    has_specific_focus = bool(context_items.get("files") or context_items.get("folders"))

    if has_specific_focus:
        # 1. Modo Especifico: Auto-Añadir al contexto
        if rel_path not in context_items["files"]:
            context_items["files"].append(rel_path)
            ctx.state["context_items"] = context_items
            ctx.update_state(ctx.state)
            UI.info(f"El archivo [cyan]{rel_path}[/] fue añadido automáticamente al contexto específico.")
    else:
        # 2. Modo General: Advertencia de ignorados
        import pathspec
        from project_context.utils import get_ignore_patterns

        patterns = get_ignore_patterns(ctx.project_path, ".gitignore")
        patterns += get_ignore_patterns(ctx.project_path, ".contextignore")

        if patterns:
            spec = pathspec.PathSpec.from_lines("gitwildmatch", patterns)
            if spec.match_file(rel_path):
                UI.warn(f"¡Atención! El archivo [cyan]{rel_path}[/] parece estar excluido por .gitignore o .contextignore.")
                UI.info("La IA no podrá leer el contenido de tu historia. Verifica tus reglas de exclusión.")
    # ==========================================


    ctx.stop_monitor()

    UI.info("Iniciando Modo Historia...")
    ctx.state["story_mode"] = True
    ctx.state["story_anchor"] = rel_path
    ctx.update_state(ctx.state)

    try:
        # Llamamos al procesador principal que hará la subida a Drive
        from project_context.ops import apply_story_update
        new_state = apply_story_update(ctx.api, ctx.project_path, ctx.state)
        ctx.update_state(new_state)
    except Exception as e:
        UI.error(f"Error inicializando ancla: {e}")
        UI.info(f"El modo historia se activó para '{rel_path}', pero corrige la etiqueta y usa 'update'.")

    ctx.start_monitor()
