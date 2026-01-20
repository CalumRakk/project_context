import signal
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.table import Table

from project_context.api_drive import AIStudioDriveManager
from project_context.history import SnapshotManager
from project_context.ops import rebuild_project_context, sync_images, update_context
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

                table = Table(
                    title="Historial de Snapshots",
                    show_header=True,
                    header_style="bold magenta",
                )
                table.add_column("Timestamp (ID)", style="dim")
                table.add_column("Fecha/Hora")
                table.add_column("Mensaje", style="cyan")

                for tid in all_ids:
                    info = monitor.get_snapshot_info(tid)
                    if info:
                        table.add_row(
                            info["timestamp"],
                            info["human_time"],
                            info.get("message") or "-",
                        )

                console.print(table)

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
                UI.info("Obteniendo cambios de Git...")
                diff_content = get_diff_message(project_path)
                if not diff_content:
                    UI.warn("No hay cambios en stage. Usa `git add` primero.")
                    continue

                UI.success("Sugerencia de commit enviada a AI Studio.")

                chat_data = api.get_chat_ia_studio(state["chat_id"])
                if not chat_data:
                    UI.warn("No se pudo obtener el chat de AI Studio.")
                    continue

                prompt_text = (
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
