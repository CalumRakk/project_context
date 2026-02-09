import signal
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.table import Table

from project_context.api_drive import AIStudioDriveManager
from project_context.history import SnapshotManager
from project_context.ops import (
    COMMIT_TASK_MARKER,
    find_pending_commit_tasks,
    rebuild_project_context,
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
    extract_image_references,
    get_diff_message,
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
    typer.echo(f" n) Escribir ruta manualmente")
    typer.echo(f" s) Saltar estas imágenes")

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
    except:
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
        "  [bold]exit / quit[/]        - Salir de la sesión.\n"
    )
    console.print(help_text)


def interactive_session(api: AIStudioDriveManager, state: dict, project_path: Path):
    UI.info("Sesión interactiva iniciada. Escribe [bold]help[/] para comandos.")
    monitor = SnapshotManager(api, project_path, state)

    def handle_exit(sig, frame):
        UI.info("Cerrando sesión de forma segura...")
        monitor.stop_monitoring()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_exit)  # Ctrl+C
    if sys.platform != "win32":
        signal.signal(signal.SIGTERM, handle_exit)

    if state.get("monitor_active", False):
        UI.info("Reactivando monitor de historial automáticamente...")
        monitor.start_monitoring()

    chat_id = state.get("chat_id")
    consecutive_errors = 0

    UI.info(f"[Chat] Iniciando sesión con chat_id {chat_id}...")

    session_media_root = None  # Memoria temporal de la carpeta de imágenes
    while True:
        consecutive_errors = 0
        try:
            command_line = console.input("[bold green]>> [/]").strip()
            if not command_line:
                continue

            parts = command_line.split(" ", 1)
            command = parts[0].lower()
            args = parts[1] if len(parts) > 1 else ""

            if command in ["exit", "quit"]:
                monitor.stop_monitoring()
                UI.info("Cerrando sesión...")
                break

            elif command == "edit":
                monitor.stop_monitoring()

                run_editor_mode(api, state["chat_id"])
                UI.info("Reactivando monitor de historial automático...")
                command_help()
                if state.get("monitor_active", False):
                    monitor.start_monitoring()

            elif command == "help":
                command_help()

            elif command == "save":
                if not args.strip():
                    UI.warn("Debes proveer un mensaje: `save mi_cambio_importante`")
                else:
                    monitor.create_named_snapshot(args.strip())
            elif command == "monitor":
                if args == "on":
                    monitor.start_monitoring()
                    if not state.get("monitor_active"):
                        state["monitor_active"] = True
                        save_project_context_state(project_path, state)
                elif args == "off":
                    monitor.stop_monitoring()
                    if state.get("monitor_active"):
                        state["monitor_active"] = False
                        save_project_context_state(project_path, state)
                else:
                    UI.warn("Uso: monitor on | off")

            elif command == "history":
                all_ids = monitor.get_all_snapshot_ids()
                if not all_ids:
                    UI.info("No hay historial disponible aún.")
                    continue

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

                    # Obtener el subconjunto de IDs para esta página
                    current_chunk = all_ids[i : i + page_size]
                    for tid in current_chunk:
                        info = monitor.get_snapshot_info(tid)
                        if info:
                            table.add_row(
                                info["timestamp"],
                                info["human_time"],
                                info.get("message") or "-",
                            )

                    console.print(table)

                    # Control de paginación
                    if i + page_size < total_snapshots:
                        prompt_msg = f"[bold yellow]-- Presiona ENTER para ver más ({total_snapshots - (i + page_size)} restantes) o 'q' para salir --[/]"
                        choice = console.input(prompt_msg).strip().lower()
                        if choice == "q":
                            break
                    else:
                        UI.info("Fin del historial.")
            elif command == "restore":
                if not args:
                    UI.warn("Especifica el ID del snapshot.")
                else:
                    confirm = console.input(
                        f"[bold red]¿Restaurar snapshot {args}? (s/n): [/]"
                    )
                    if confirm.lower() == "s":
                        monitor.stop_monitoring()
                        if monitor.restore_snapshot(args.strip()):
                            UI.success("Chat restaurado. Recarga AI Studio.")

            elif command == "clear":
                if api.clear_chat_ia_studio(state["chat_id"]):
                    UI.success("Historial de mensajes limpiado en Drive.")

            elif command == "update":
                monitor.stop_monitoring()

                state = update_context(api, project_path, state)
                save_project_context_state(project_path, state)
                monitor.state = state
                print("Puedes reactivar el monitor con 'monitor on'.")

            elif command == "reset":
                confirm = console.input(
                    "[bold red]¿Reconstruir chat y contexto por completo? (s/n): [/]"
                )
                if confirm.lower() == "s":
                    monitor.stop_monitoring()
                    state = rebuild_project_context(api, project_path, state)
                    save_project_context_state(project_path, state)
                    if state.get("monitor_active"):
                        monitor.start_monitoring()

            elif command == "commit":
                # Descargar chat una sola vez para todas las comprobaciones
                chat_data = api.get_chat_ia_studio(state["chat_id"])
                if not chat_data:
                    UI.error("No se pudo obtener el chat de AI Studio.")
                    continue

                subcommand = args.strip().lower()

                # Limpieza
                if subcommand in ["clean", "clear", "rm"]:
                    UI.info("Limpiando bloques de commit...")
                    removed = api.remove_commit_tasks(state["chat_id"])
                    if removed > 0:
                        UI.success(f"Se eliminaron {removed} bloques.")
                    else:
                        UI.info("Nada que limpiar.")
                    continue

                # Validación de duplicados
                pending_tasks = find_pending_commit_tasks(chat_data)
                if pending_tasks and subcommand != "force":
                    UI.warn("Ya hay una sugerencia de commit pendiente en el chat.")
                    UI.info(
                        "Usa 'commit clean' para borrarla o 'commit force' para ignorar."
                    )
                    continue

                # Llegamos aquí si no hay pendientes o es force
                UI.info("Obteniendo cambios de Git...")
                diff_content = get_diff_message(project_path)

                if not diff_content:
                    UI.warn("No hay cambios en stage. Usa `git add` primero.")
                    continue

                prompt_text = (
                    f"{COMMIT_TASK_MARKER}\n"
                    "Actúa como un desarrollador senior con amplia experiencia en la redacción de mensajes de commit siguiendo las mejores prácticas. El archivo context_project.txt contiene el contexto del proyecto:\n\nHe realizado los siguientes cambios:\n\n"
                    "```diff\n"
                    f"{diff_content}\n"
                    "```\n\n"
                    "Con base en esos cambios, sugiéreme un mensaje de commit conciso, en español, que resuma de forma clara y profesional los puntos más relevantes. El mensaje debe ocupar un solo párrafo y reflejar la intención del cambio sin omitir detalles importantes.\n\n"
                    "Formato: <tipo>(<alcance>): <descripción>"
                )

                new_chunk = ChunksText(text=prompt_text, role="user", tokenCount=None)
                chat_data.chunkedPrompt.chunks.append(new_chunk)

                UI.info("Guardando cambios en Drive...")
                if api.update_chat_file(state["chat_id"], chat_data):
                    UI.success("¡Listo! Prompt de commit agregado al final del chat.")
                    UI.info("Ve a AI Studio y presiona RUN.")
                else:
                    UI.error("Error al guardar el archivo en Drive.")

            elif command == "images":
                if not args:
                    UI.warn("Uso: images <archivo.md>")
                    continue

                target_file = project_path / args
                refs = extract_image_references(target_file)

                if not refs:
                    UI.warn(f"No se encontraron imágenes en {args}.")
                    continue

                resolved_paths = []
                for ref_text, is_wiki in refs:
                    path = (target_file.parent / ref_text).resolve()

                    # Si es WikiLink y no se encuentra, usamos resolución asistida
                    if not path.exists() and is_wiki:
                        if not session_media_root:
                            session_media_root = prompt_for_media_folder(project_path)

                        if session_media_root:
                            path = (session_media_root / ref_text).resolve()

                    if path.exists() and path.is_file():
                        resolved_paths.append(path)
                    else:
                        typer.secho(
                            f" [!] No se pudo encontrar: {ref_text}",
                            fg=typer.colors.YELLOW,
                        )

                if not resolved_paths:
                    UI.warn("No se pudo resolver ninguna ruta de imagen.")
                    continue

                monitor.stop_monitoring()

                chat_data = api.get_chat_ia_studio(state["chat_id"])
                if not chat_data:
                    print("Error recuperando el chat desde Drive.")
                    continue

                chat_data.chunkedPrompt.chunks.append(
                    ChunksText(
                        text=IMAGE_INSERTION_PROMPT.format(filename=args), role="user"
                    )
                )

                new_chunks = sync_images(
                    api, project_path, specific_files=resolved_paths
                )
                chat_data.chunkedPrompt.chunks.extend(new_chunks)

                chat_data.chunkedPrompt.chunks.append(
                    ChunksText(
                        text=IMAGE_INSERTION_RESPONSE.format(filename=args),
                        role="model",
                    )
                )

                if api.update_chat_file(state["chat_id"], chat_data):
                    typer.secho(
                        f"¡{len(resolved_paths)} imágenes inyectadas!",
                        fg=typer.colors.GREEN,
                    )

                if state.get("monitor_active"):
                    monitor.start_monitoring()
            elif command in ["fix"]:
                UI.info("Descargando chat para inspección...")
                monitor.stop_monitoring()

                chat_data = api.get_chat_ia_studio(state["chat_id"])

                if not chat_data:
                    UI.error("No se pudo recuperar el chat de Drive.")
                    continue

                fixed_count = 0
                chunks = chat_data.chunkedPrompt.chunks

                for chunk in chunks:
                    if isinstance(chunk, ChunksText) and hasattr(chunk, "finishReason"):
                        if chunk.finishReason != "STOP":
                            chunk.finishReason = "STOP"
                            fixed_count += 1

                if fixed_count > 0:
                    UI.info(
                        f"Se detectaron {fixed_count} bloques con finishReason inconsistente."
                    )
                    if api.update_chat_file(state["chat_id"], chat_data):
                        UI.success(
                            f"¡Sanación completada! {fixed_count} bloques marcados como 'STOP'."
                        )
                    else:
                        UI.error(
                            "Error al intentar guardar el chat corregido en Drive."
                        )
                else:
                    UI.success(
                        "No se encontraron bloques que requieran corrección. El chat está sano."
                    )

                if state.get("monitor_active"):
                    monitor.start_monitoring()
            elif command == "context":
                if not args or args.strip().lower() == "reset":
                    UI.info("Restableciendo contexto a todo el proyecto...")
                    state["context_scope"] = None

                    state = update_context(api, project_path, state)
                    save_project_context_state(project_path, state)
                else:
                    target_rel_path = args.strip()
                    full_target_path = project_path / target_rel_path

                    if not full_target_path.exists():
                        UI.error(
                            f"La ruta '{target_rel_path}' no existe en el proyecto."
                        )
                        continue

                    UI.info(f"Enfocando contexto en: [bold]{target_rel_path}[/]")
                    state["context_scope"] = target_rel_path
                    state = update_context(api, project_path, state)
                    save_project_context_state(project_path, state)
                    UI.success(f"Ahora el modelo solo ve '{target_rel_path}'.")
            else:
                print(f"Comando desconocido: '{command}'")

        except (EOFError, KeyboardInterrupt):
            UI.info("Saliendo...")
            monitor.stop_monitoring()
            break
        except Exception as e:
            UI.error(f"Error de ejecución: {e}")

            consecutive_errors += 1
            if consecutive_errors > 10:
                UI.error("Demasiados errores consecutivos. Saliendo...")
                monitor.stop_monitoring()
                sys.exit(1)

            if state.get("monitor_active"):
                monitor.start_monitoring()
