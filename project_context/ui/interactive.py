import signal
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer

from project_context.api_drive import AIStudioDriveManager
from project_context.history import SnapshotManager
from project_context.ops import rebuild_project_context, sync_images, update_context
from project_context.schema import ChunksText
from project_context.ui.editor import run_editor_mode
from project_context.utils import (
    IMAGE_INSERTION_PROMPT,
    IMAGE_INSERTION_RESPONSE,
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
    print("\nComandos disponibles:")
    print("  commit             - [NUEVO] Enviar git diff (staged) al chat.")
    print("  edit               - Abrir editor visual de historial.")
    print("  monitor on/off     - Auto-guardado de historial.")
    print("  save <mensaje>     - Guardar snapshot manual con nombre.")
    print("  history [N|all]    - Ver puntos de restauración.")
    print("  restore <id>       - Restaurar chat y contexto.")
    print("  clear              - Limpiar historial del chat en Drive.")
    print("  update             - Forzar actualización de contexto.")
    print("  reset              - Actualiza contexto y limpia el chat.")
    print("  exit / quit        - Salir.\n")


def interactive_session(api: AIStudioDriveManager, state: dict, project_path: Path):
    print("\nOk. Contexto cargado. Sesión interactiva iniciada.")
    print("\tEscribe 'help' para ver los comandos disponibles.\n")

    monitor = SnapshotManager(api, project_path, state)

    def handle_exit(sig, frame):
        print("\n[Signal] Cerrando sesión de forma segura...")
        monitor.stop_monitoring()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_exit)  # Ctrl+C
    if sys.platform != "win32":
        signal.signal(signal.SIGTERM, handle_exit)

    if state.get("monitor_active", False):
        print("[Estado guardado] Reactivando monitor automáticamente...")
        monitor.start_monitoring()

    chat_id = state.get("chat_id")
    consecutive_errors = 0

    print(f"[Chat] Iniciando sesión con chat_id {chat_id}...")

    session_media_root = None  # Memoria temporal de la carpeta de imágenes
    while True:
        try:
            command_line = input(">> ")
            consecutive_errors = 0
            if not command_line.strip():
                print("Comando vacío.")
                raise ValueError("Comando vacío.")

            parts = command_line.split(" ", 1)
            command = parts[0].lower()
            args = parts[1] if len(parts) > 1 else ""

            if command in ["exit", "quit"]:
                monitor.stop_monitoring()
                print("Cerrando sesión...")
                break

            elif command == "help":
                command_help()

            elif command == "edit":
                monitor.stop_monitoring()

                run_editor_mode(api, state["chat_id"])
                print("Regresando a la sesión interactiva...")
                command_help()
                if state.get("monitor_active", False):
                    monitor.start_monitoring()

            elif command == "save":
                if not args.strip():
                    print("Por favor, escribe un mensaje para identificar el guardado.")
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

            elif command == "history":
                all_ids = monitor.get_all_snapshot_ids()
                if not all_ids:
                    print("No hay historial disponible.")
                    continue

                page_size = 10
                current_idx = 0
                total = len(all_ids)

                print(f"\nMostrando historial ({total} snapshots):")
                print(f"{'TIMESTAMP (ID)':<18} | {'HORA':<20} | {'MENSAJE'}")
                print("-" * 75)

                while current_idx < total:
                    end_idx = min(current_idx + page_size, total)
                    for i in range(current_idx, end_idx):
                        info = monitor.get_snapshot_info(all_ids[i])
                        if info:
                            msg = info.get("message") or "-"
                            # Truncar mensaje si es muy largo para la tabla
                            if len(msg) > 35:
                                msg = msg[:32] + "..."
                            print(
                                f" {info['timestamp']:<18} | {info['human_time']:<20} | {msg}"
                            )

                    current_idx = end_idx

                    if current_idx < total:
                        prompt = f"-- Más ({current_idx}/{total}). [Enter] para seguir, 'q' para salir --"
                        try:
                            choice = input(prompt).strip().lower()
                            # Borrar la línea del prompt para que el log se vea limpio
                            # \033[F mueve el cursor arriba, \033[K borra la línea
                            sys.stdout.write("\033[F\033[K")
                            if choice == "q":
                                break
                        except (KeyboardInterrupt, EOFError):
                            print("")
                            break
                    else:
                        print("-" * 75)
                        print("Fin del historial.\n")

            elif command == "restore":
                if not args:
                    print("Especifica el TIMESTAMP del comando 'history'.")
                else:
                    if (
                        input(
                            "ESTO SOBREESCRIBIRA EL CHAT ACTUAL. ¿Seguro? (s/n): "
                        ).lower()
                        == "s"
                    ):
                        monitor.stop_monitoring()
                        if monitor.restore_snapshot(args.strip()):
                            print("Recarga AI Studio para ver los cambios.")

            elif command == "clear":
                if api.clear_chat_ia_studio(state["chat_id"]):
                    print("Historial limpiado.")

            elif command == "update":
                monitor.stop_monitoring()

                state = update_context(api, project_path, state)
                save_project_context_state(project_path, state)
                monitor.state = state
                print("Puedes reactivar el monitor con 'monitor on'.")

            elif command == "reset":
                if (
                    input(
                        "Esto eliminará el historial de mensajes y reconstruirá el contexto (incluyendo imágenes). ¿Seguro? (s/n): "
                    ).lower()
                    != "s"
                ):
                    continue

                monitor.stop_monitoring()
                print("Iniciando reinicio completo...")

                # Snapshot de seguridad
                date_session_reset = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                monitor.create_named_snapshot(f"ANTES DEL RESET {date_session_reset}")

                try:
                    state = rebuild_project_context(api, project_path, state)
                    save_project_context_state(project_path, state)

                    monitor.state = state
                    if state.get("monitor_active", False):
                        monitor.start_monitoring()

                    print("¡Sesión e imágenes sincronizadas y chat reiniciado!")
                except Exception as e:
                    print(f"Error durante el reset: {e}")

            elif command == "commit":
                print("Verificando cambios en Git (Stage)...")
                diff_content = get_diff_message(project_path)

                if not diff_content:
                    print(
                        "No hay cambios en stage. Ejecuta 'git add <archivos>' primero."
                    )
                    continue

                print(
                    f"Detectados cambios ({len(diff_content)} caracteres). Obteniendo chat..."
                )

                chat_data = api.get_chat_ia_studio(state["chat_id"])
                if not chat_data:
                    print("Error recuperando el chat desde Drive.")
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

                print("Enviando prompt a AI Studio...")
                if api.update_chat_file(state["chat_id"], chat_data):
                    print("¡Listo! Prompt de commit agregado al final del chat.")
                    print("Ve a AI Studio y presiona RUN.")
                else:
                    print("Error al guardar el archivo en Drive.")

            elif command == "images":
                if not args:
                    print("Uso: images <archivo.md>")
                    continue

                target_file = project_path / args
                refs = extract_image_references(target_file)

                if not refs:
                    print(f"No se encontraron imágenes en {args}.")
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
                    print("No se pudo resolver ninguna ruta de imagen.")
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

        except EOFError:
            break
        except KeyboardInterrupt:
            monitor.stop_monitoring()
            break
        except Exception as e:
            consecutive_errors += 1
            print(f"Error: {e}")
            if consecutive_errors > 10:
                monitor.stop_monitoring()
                sys.exit(1)
