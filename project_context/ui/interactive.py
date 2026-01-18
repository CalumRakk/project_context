import sys
from datetime import datetime
from pathlib import Path

from project_context.api_drive import AIStudioDriveManager
from project_context.history import SnapshotManager
from project_context.ops import rebuild_project_context, update_context
from project_context.schema import ChunksText
from project_context.ui.editor import run_editor_mode
from project_context.utils import (
    get_diff_message,
    save_project_context_state,
)


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
    if state.get("monitor_active", False):
        print("[Estado guardado] Reactivando monitor automáticamente...")
        monitor.start_monitoring()

    chat_id = state.get("chat_id")
    consecutive_errors = 0

    print(f"[Chat] Iniciando sesión con chat_id {chat_id}...")

    while True:
        try:
            command_line = input(">> ")
            consecutive_errors = 0
            if not command_line.strip():
                continue

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
                snaps = monitor.list_snapshots()
                if not snaps:
                    print("No hay historial disponible.")
                else:
                    limit = 10
                    if args.strip():
                        if args.strip() == "all":
                            limit = len(snaps)
                        elif args.strip().isdigit():
                            limit = int(args.strip())
                    subset = list(reversed(snaps[:limit]))
                    print(f"\nMostrando últimos {len(subset)} snapshots:")
                    print(f"{'TIMESTAMP (ID)':<16} | {'HORA':<20} | {'MENSAJE'}")
                    print("-" * 70)
                    for snap in subset:
                        msg = snap.get("message") or "-"
                        print(
                            f" {snap['timestamp']:<16} | {snap['human_time']:<20} | {msg}"
                        )
                    print("")

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
            else:
                print(f"Comando desconocido: '{command}'")

        except KeyboardInterrupt:
            monitor.stop_monitoring()
            break
        except Exception as e:
            consecutive_errors += 1
            print(f"Error: {e}")
            if consecutive_errors > 10:
                monitor.stop_monitoring()
                sys.exit(1)
