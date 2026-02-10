import signal
import sys
from pathlib import Path
from typing import Optional

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
                subcommand = args.strip().lower()

                if subcommand in ["clean", "clear", "rm"]:
                    UI.info("Limpiando bloques de commit...")
                    removed = api.remove_commit_tasks(state["chat_id"])
                    if removed > 0:
                        UI.success(f"Se eliminaron {removed} bloques.")
                    else:
                        UI.info("Nada que limpiar.")
                    continue

                has_pending = api.has_pending_commit_suggestion(state["chat_id"])
                if has_pending and subcommand != "force":
                    UI.warn("Ya hay una sugerencia de commit pendiente en el chat.")
                    UI.info(
                        "Usa 'commit clean' para borrarla o 'commit force' para ignorar."
                    )
                    continue

                UI.info("Obteniendo cambios de Git...")
                prompt_text = generate_commit_prompt_text(project_path)

                if not prompt_text:
                    UI.warn("No hay cambios en stage. Usa `git add` primero.")
                    continue

                UI.info("Enviando prompt a Drive...")
                if api.append_message(state["chat_id"], prompt_text, role="user"):
                    UI.success(
                        "¡Listo! Prompt de commit agregado. Ve a AI Studio y presiona RUN."
                    )
                else:
                    UI.error("Error al guardar en Drive.")
            elif command == "images":
                if not args:
                    UI.warn("Uso: images <archivo.md>")
                    continue

                try:
                    found_paths, missing = resolve_image_paths(
                        project_path, args, session_media_root
                    )
                    if missing and not session_media_root:
                        UI.warn(f"No se encontraron: {', '.join(missing[:3])}...")
                        session_media_root = prompt_for_media_folder(project_path)

                        if session_media_root:
                            found_paths_2, missing_2 = resolve_image_paths(
                                project_path, args, session_media_root
                            )
                            found_paths = list(set(found_paths + found_paths_2))
                            missing = missing_2

                    if not found_paths:
                        UI.warn("No se pudo resolver ninguna ruta de imagen válida.")
                        if missing:
                            UI.info(f"Faltantes: {missing}")
                        continue

                    monitor.stop_monitoring()

                    chunks_to_add = []
                    chunks_to_add.append(
                        ChunksText(
                            text=IMAGE_INSERTION_PROMPT.format(filename=args),
                            role="user",
                        )
                    )

                    image_chunks = sync_images(
                        api, project_path, specific_files=found_paths
                    )
                    chunks_to_add.extend(image_chunks)

                    chunks_to_add.append(
                        ChunksText(
                            text=IMAGE_INSERTION_RESPONSE.format(filename=args),
                            role="model",
                        )
                    )

                    if api.append_chunks(state["chat_id"], chunks_to_add):
                        typer.secho(
                            f"¡{len(found_paths)} imágenes inyectadas!",
                            fg=typer.colors.GREEN,
                        )
                    else:
                        UI.error("Error al subir imágenes al chat.")

                    if state.get("monitor_active"):
                        monitor.start_monitoring()

                except FileNotFoundError:
                    UI.error(f"El archivo '{args}' no existe en el proyecto.")
                except Exception as e:
                    UI.error(f"Error procesando imágenes: {e}")

            elif command in ["fix"]:
                UI.info("Analizando chat...")
                monitor.stop_monitoring()

                fixed_count = api.repair_chat_structure(state["chat_id"])

                if fixed_count > 0:
                    UI.success(
                        f"¡Sanación completada! {fixed_count} bloques corregidos."
                    )
                else:
                    UI.success("El chat está sano o no se pudo acceder.")

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
