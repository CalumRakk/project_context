import shutil
import sys
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
    profile_manager,
    save_context,
    save_project_context_state,
)


def initialize_project_context(api: AIStudioDriveManager, project_path: Path) -> Dict:
    print("Primer uso para este proyecto. Creando contexto inicial...")
    content, expected_tokens = generate_context(project_path)
    path_context = save_context(project_path, content)
    content_md5 = compute_md5(path_context)

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

    chat_filename = project_path.name + "_chat.prompt"
    chat_id = api.create_chat_file(file_name=chat_filename, chat_data=chat_data)
    if not chat_id:
        raise ValueError("No se pudo crear el chat en Google Drive.")

    initial_state = {
        "path": str(project_path),
        "last_modified": project_path.stat().st_mtime,
        "md5": content_md5,
        "chat_id": chat_id,
        "file_id": document["id"],
    }
    return initial_state


def update_context(api: AIStudioDriveManager, project_path: Path, state: Dict) -> Dict:
    last_modified_saved = state.get("last_modified", 0)
    chat_id = state.get("chat_id")
    if not chat_id:
        raise ValueError("No se encontró 'chat_id' en el estado del proyecto.")

    print(f"Revisando si el proyecto en '{project_path}' ha cambiado...")

    if not has_files_modified_since(last_modified_saved, project_path):
        print("El proyecto no ha cambiado. No se requiere actualización.")
        return state

    print("El proyecto ha cambiado. Generando nuevo contexto...")
    content, _ = generate_context(project_path)
    path_context = save_context(project_path, content)
    current_md5 = compute_md5(path_context)

    if current_md5 == state.get("md5"):
        print("El contenido es idéntico (cambios irrelevantes).")
        state["last_modified"] = project_path.stat().st_mtime
        return state

    print("El contenido ha cambiado. Actualizando en Google Drive...")
    file_id = state.get("file_id")
    if not file_id:
        raise ValueError("No se encontró 'file_id' para actualizar.")

    api.gdm.update_file_from_memory(file_id, content, "text/plain")

    state["last_modified"] = project_path.stat().st_mtime
    state["md5"] = current_md5
    print("Contexto actualizado con Éxito.")

    return state


def interactive_session(api: AIStudioDriveManager, state: dict, project_path: Path):
    print("\nOk. Contexto cargado. Sesión interactiva iniciada.")
    print("\tEscribe 'help' para ver los comandos disponibles.\n")

    monitor = SnapshotManager(api, project_path, state)
    if state.get("monitor_active", False):
        print("[Estado guardado] Reactivando monitor automáticamente...")
        monitor.start_monitoring()

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
                print("  monitor on/off     - Auto-guardado de historial.")
                print("  history [N|all]    - Ver puntos de restauración.")
                print("  restore <id>       - Restaurar chat y contexto.")
                print("  clear              - Limpiar historial del chat en Drive.")
                print("  update             - Forzar actualización de contexto.")
                print("  exit / quit        - Salir.\n")

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
                    print("Uso: monitor on | monitor off")

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
                    print(f"{'TIMESTAMP (ID)':<20} | {'HORA':<25}")
                    print("-" * 50)
                    for snap in subset:
                        print(f" {snap['timestamp']:<20} | {snap['human_time']:<25}")
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

            else:
                print(f"Comando desconocido: '{command}'")

        except KeyboardInterrupt:
            monitor.stop_monitoring()
            break
        except Exception as e:
            print(f"Error: {e}")


@click.group(invoke_without_command=True)
@click.pass_context
@click.option(
    "-p",
    "--path",
    "project_path",
    type=click.Path(exists=True, file_okay=False),
    default=".",
    help="Ruta del proyecto a analizar.",
)
@click.option(
    "-u", "--update-only", is_flag=True, help="Solo crea/actualiza el contexto y sale."
)
@click.option(
    "-i", "--interactive-only", is_flag=True, help="Entra directo a modo interactivo."
)
def main(ctx, project_path, update_only, interactive_only):
    """
    Herramienta CLI para gestionar contexto de proyecto en Google AI Studio.
    Soporta múltiples perfiles de usuario.
    """
    # Si se invocó un subcomando (ej: 'project_context profile list'), salimos de aquí.
    if ctx.invoked_subcommand is not None:
        return

    project_path = Path(project_path).resolve()
    try:
        api = AIStudioDriveManager()
    except Exception as e:
        click.secho(f"Error inicializando Drive: {e}", fg="red")
        sys.exit(1)

    state = load_project_context_state(project_path)

    if interactive_only:
        if state is None or not state.get("chat_id"):
            click.secho(
                "Error: No hay contexto previo. Ejecuta sin -i primero.", fg="red"
            )
            sys.exit(1)
        print("Modo interactivo rápido.")
    else:
        if state is None:
            state = initialize_project_context(api, project_path)
        else:
            state = update_context(api, project_path, state)
        save_project_context_state(project_path, state)

    if update_only:
        print("Sincronizado. Saliendo.")
    else:
        interactive_session(api, state, project_path)


@main.group()
def profile():
    """Gestión de perfiles de usuario (Multicuentas)."""
    pass


@profile.command(name="list")
def list_profiles():
    """Lista los perfiles disponibles."""
    active = profile_manager.get_active_profile_name()
    profiles = profile_manager.list_profiles()

    click.echo("\nPerfiles disponibles:")
    for p in profiles:
        prefix = "Ok" if p == active else "  "
        click.echo(f"{prefix} {p}")
    click.echo("")


@profile.command(name="add")
@click.argument("name")
def add_profile(name):
    """Crea un nuevo perfil."""
    if name in profile_manager.list_profiles():
        click.secho(f"El perfil '{name}' ya existe.", fg="yellow")
        return

    profile_manager.set_active_profile(name)
    click.secho(f"Perfil '{name}' creado y activado.", fg="green")
    click.echo(
        "La próxima vez que ejecutes 'project_context .', se te pedirá autenticación."
    )


@profile.command(name="use")
@click.argument("name")
def switch_profile(name):
    """Cambia el perfil activo."""
    if name not in profile_manager.list_profiles():
        click.secho(f"Error: El perfil '{name}' no existe.", fg="red")
        return

    profile_manager.set_active_profile(name)
    click.secho(f"Perfil cambiado a: {name}", fg="green")


@profile.command(name="info")
def profile_info():
    """Información del perfil actual y credenciales."""
    name = profile_manager.get_active_profile_name()
    working_dir = profile_manager.get_working_dir()
    secrets_path, secrets_type = profile_manager.resolve_secrets_file()
    token_path = working_dir / "token.json"

    click.echo(f"\n--- 👤 Perfil Actual: {name} ---")
    click.echo(f"Datos:      {working_dir}")
    click.echo(f"Secretos:   {secrets_type}")
    click.echo(f"   └─ Ruta:    {secrets_path}")

    if token_path.exists():
        click.secho("Estado:     Sesión activa (Token existe)", fg="green")
    else:
        click.secho("Estado:     Sesión inactiva (Requiere login)", fg="yellow")
    click.echo("")


@profile.command(name="set-secrets")
@click.argument("secrets_path", type=click.Path(exists=True, dir_okay=False))
def set_secrets(secrets_path):
    """
    Instala un client_secrets.json específico para este perfil.
    Útil si este perfil usa una App de Google Cloud distinta a la global.
    """
    target_path = profile_manager.get_working_dir() / "client_secrets.json"
    shutil.copy(secrets_path, target_path)
    click.secho(
        f"Secretos específicos instalados para '{profile_manager.get_active_profile_name()}'.",
        fg="green",
    )

    token_path = profile_manager.get_working_dir() / "token.json"
    if token_path.exists():
        token_path.unlink()
        click.secho(
            "Token anterior eliminado por seguridad. Re-autenticación requerida.",
            fg="yellow",
        )


if __name__ == "__main__":
    main()
