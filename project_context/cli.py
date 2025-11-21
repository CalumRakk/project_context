from pathlib import Path
from typing import Dict

import click

from project_context.api_drive import AIStudioDriveManager
from project_context.history import SnapshotManager
from project_context.schema import (
    ChatIAStudio,
    ChunkedPrompt,
    ChunksDocument,
    ChunksText,
    DriveDocument,
    RunSettings,
    SystemInstruction,
)
from project_context.utils import (
    PROMPT_TEMPLATE,
    RESPONSE_TEMPLATE,
    compute_md5,
    generate_context,
    has_files_modified_since,
    load_project_context_state,
    save_context,
    save_project_context_state,
)


def initialize_project_context(api: AIStudioDriveManager, project_path: Path) -> Dict:
    """
    Gestiona la creación inicial del contexto, los archivos en Drive y el estado local.
    """
    print("Primer uso para este proyecto. Creando contexto inicial...")
    content, expected_tokens = generate_context(project_path)
    path_context = save_context(project_path, content)
    content_md5 = compute_md5(path_context)

    # 1. Crear el archivo de contexto en Google Drive
    mimetype = "text/plain"
    filename = project_path.name + "_context.txt"
    document = api.gdm.create_file_from_memory(
        folder_id=api.ai_studio_folder,
        file_name=filename,
        content=content,
        mime_type=mimetype,
    )
    if not document or "id" not in document:
        raise ValueError("No se pudo crear el archivo de contexto en Google Drive.")

    # 2. Preparar la estructura del chat para AI Studio
    drive_document = DriveDocument(id=document["id"])
    chat_file = ChunksDocument(
        driveDocument=drive_document, role="user", tokenCount=expected_tokens
    )
    chunks_text_prompt = ChunksText(text=PROMPT_TEMPLATE, role="user", tokenCount=248)
    chunks_text_response = ChunksText(
        text=RESPONSE_TEMPLATE, role="model", tokenCount=4
    )

    chat_data = ChatIAStudio(
        runSettings=RunSettings(),
        systemInstruction=SystemInstruction(),
        chunkedPrompt=ChunkedPrompt(
            chunks=[chat_file, chunks_text_prompt, chunks_text_response],
            pendingInputs=[],
        ),
    )

    # 3. Crear el archivo de chat en Google Drive
    chat_filename = project_path.name + "_chat.prompt"
    chat_id = api.create_chat_file(file_name=chat_filename, chat_data=chat_data)
    if not chat_id:
        raise ValueError("No se pudo crear el chat en Google Drive.")

    # 4. Crear y devolver el estado inicial
    initial_state = {
        "path": str(project_path),
        "last_modified": project_path.stat().st_mtime,
        "md5": content_md5,
        "chat_id": chat_id,
        "file_id": document["id"],
    }
    return initial_state


def update_context(api: AIStudioDriveManager, project_path: Path, state: Dict) -> Dict:
    """
    Verifica si el contexto del proyecto necesita ser actualizado y lo hace si es necesario.
    """
    last_modified_saved = state.get("last_modified", 0)
    chat_id = state.get("chat_id")
    if not chat_id:
        raise ValueError("No se encontró 'chat_id' en el estado del proyecto.")

    print(f"Revisando si el proyecto en '{project_path}' ha cambiado...")
    print(f"{chat_id=}")

    # Comprobación rapida: si la fecha de modificación de la carpeta es más reciente.
    if not has_files_modified_since(last_modified_saved, project_path):
        print(
            "El proyecto no ha cambiado desde la última vez. No se requiere actualización."
        )
        return state

    print("El proyecto ha cambiado. Generando nuevo contexto para comparación...")
    content, _ = generate_context(project_path)
    path_context = save_context(project_path, content)
    current_md5 = compute_md5(path_context)

    if current_md5 == state.get("md5"):
        print(
            "Aunque los archivos cambiaron, el contenido del contexto es el mismo. No se requiere actualización."
        )
        # Actualizamos la fecha para no volver a comprobar innecesariamente
        state["last_modified"] = project_path.stat().st_mtime
        return state

    print("El contenido del contexto ha cambiado. Actualizando en Google Drive...")
    file_id = state.get("file_id")
    if not file_id:
        raise ValueError(
            "No se encontró 'file_id' en el estado para poder actualizar el archivo."
        )

    api.gdm.update_file_from_memory(file_id, content, "text/plain")

    # Actualizar el estado con la nueva información
    state["last_modified"] = project_path.stat().st_mtime
    state["md5"] = current_md5
    print("Contexto actualizado con Exito.")

    return state


def interactive_session(api: AIStudioDriveManager, state: dict, project_path: Path):
    """Inicia un bucle interactivo para recibir comandos del usuario."""
    print("\nOk. Contexto cargado. Sesión interactiva iniciada.")
    print("\tEscribe 'help' para ver los comandos disponibles.\n")

    monitor = SnapshotManager(api, project_path, state)
    while True:
        try:
            command_line = input(">> ")
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
                print("\nComandos disponibles:")
                print(
                    "  monitor on/off     - Activa/Desactiva el guardado automático de historial."
                )
                print(
                    "  history            - Muestra los puntos de restauración disponibles."
                )
                print(
                    "  restore <id>       - Restaura el chat y contexto a un punto anterior."
                )
                print(
                    "  clear              - Limpia el historial del chat en Google Drive."
                )
                print(
                    "  update             - Revisa y actualiza el contexto si el proyecto cambió."
                )
                print("  exit / quit        - Cierra la sesión.\n")

            elif command == "monitor":
                if args == "on":
                    monitor.start_monitoring()
                elif args == "off":
                    monitor.stop_monitoring()
                else:
                    print("Uso correcto: monitor on | monitor off")

            elif command == "history":
                snaps = monitor.list_snapshots()
                if not snaps:
                    print("No hay historial disponible.")
                else:
                    print(f"\n{'TIMESTAMP (ID)':<20} | {'HORA':<25} | {'CONTEXTO REF'}")
                    print("-" * 65)
                    for snap in snaps:
                        ctx_ref = snap.get("context_md5", "")[:8] + "..."
                        print(
                            f"{snap['timestamp']:<20} | {snap['human_time']:<25} | {ctx_ref}"
                        )
                    print("")

            elif command == "restore":
                if not args:
                    print(
                        "Debes especificar el TIMESTAMP (cópialo del comando 'history')."
                    )
                else:
                    confirm = input(
                        f"ESTO SOBREESCRIBIRA EL CHAT ACTUAL EN DRIVE. ¿Seguro? (s/n): "
                    )
                    if confirm.lower() == "s":
                        monitor.stop_monitoring()
                        success = monitor.restore_snapshot(args.strip())
                        if success:
                            print(
                                "Recarga la página de AI Studio para ver los cambios."
                            )

            elif command == "clear":
                print("Limpiando historial del chat...")
                success = api.clear_chat_ia_studio(state["chat_id"])
                if success:
                    print("Historial limpiado.")
                else:
                    print("Error al limpiar el historial.")

            elif command == "update":
                # Pausamos monitor para evitar detectar el cambio de contexto como un cambio de chat
                monitor.stop_monitoring()
                state = update_context(api, project_path, state)
                save_project_context_state(project_path, state)
                monitor.state = state
                print("Puedes reactivar el monitor con 'monitor on'.")

            else:
                print(f"Comando desconocido: '{command}'")

        except KeyboardInterrupt:
            monitor.stop_monitoring()
            print("\nCerrando sesión por interrupción.")
            break
        except Exception as e:
            print(f"Ocurrió un error: {e}")


@click.command()
@click.argument(
    "project_path",
    type=click.Path(exists=True, file_okay=False, resolve_path=True),
)
@click.option(
    "-u",
    "--update-only",
    is_flag=True,
    help="Solo crea o actualiza el contexto y sale sin iniciar la sesión interactiva.",
)
def main(project_path, update_only: bool):
    """
    Inicia o actualiza el contexto de un proyecto para Google AI Studio
    y entra en una sesión interactiva.
    """
    api = AIStudioDriveManager()
    project_path = Path(project_path)

    state = load_project_context_state(project_path)

    if state is None:
        state = initialize_project_context(api, project_path)
    else:
        state = update_context(api, project_path, state)

    save_project_context_state(project_path, state)

    if update_only:
        print("Contexto sincronizado correctamente. Saliendo.")
    else:
        interactive_session(api, state, project_path)