import copy
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict

import click

from project_context.api_drive import AIStudioDriveManager
from project_context.history import SnapshotManager
from project_context.schema import (
    ChatIAStudio,
    ChunkedPrompt,
    ChunksDocument,
    ChunksImage,
    ChunksText,
    DriveDocument,
    RunSettings,
    SystemInstruction,
)
from project_context.utils import (
    RESPONSE_TEMPLATE,
    compute_md5,
    generate_context,
    get_custom_prompt,
    get_diff_message,
    has_files_modified_since,
    load_project_context_state,
    profile_manager,
    save_context,
    save_project_context_state,
)

if sys.platform.startswith("win"):
    os.system("chcp 65001 > nul")
    # Reconfigurar la salida estándar de Python a UTF-8
    if sys.stdout.encoding != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore
    if sys.stderr.encoding != "utf-8":
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore


def format_chunk_row(index: int, chunk) -> str:
    """Formatea una fila para la tabla de resumen del editor."""
    role = getattr(chunk, "role", "unknown")

    if isinstance(chunk, ChunksDocument) or hasattr(chunk, "driveDocument"):
        ctype = "FILE"
        tokens = f"{getattr(chunk, 'tokenCount', 0)}t"
        snippet = f"[ID: {chunk.driveDocument.id}] (Contexto/Archivo)"
    elif isinstance(chunk, ChunksImage) or hasattr(chunk, "driveImage"):
        ctype = "IMG "
        tokens = f"{getattr(chunk, 'tokenCount', 0)}t"
        snippet = "[Imagen adjunta]"
    elif isinstance(chunk, ChunksText) or hasattr(chunk, "text"):
        ctype = "TEXT"
        t_count = getattr(chunk, "tokenCount", None)
        tokens = f"{t_count}t" if t_count is not None else "? t"
        raw_text = chunk.text.replace("\n", " ").replace("\r", "")
        snippet = (raw_text[:60] + "...") if len(raw_text) > 60 else raw_text
    else:
        ctype = "??? "
        tokens = "-"
        snippet = str(chunk)

    return f" {index:<3} | {role:<6} | {ctype:<5} | {tokens:<8} | {snippet}"


def get_full_content_for_pager(chunk) -> str:
    """Prepara el contenido completo para el paginador (view)."""
    output = []
    output.append("=" * 80)
    output.append(f" ROL: {getattr(chunk, 'role', 'N/A').upper()}")
    output.append("-" * 80)

    if isinstance(chunk, ChunksDocument) or hasattr(chunk, "driveDocument"):
        output.append(f"TIPO: DOCUMENTO DRIVE")
        output.append(f"ID: {chunk.driveDocument.id}")
        output.append(f"TOKENS: {getattr(chunk, 'tokenCount', 'N/A')}")
        output.append(
            "\n(El contenido es un archivo vinculado en Drive, no texto plano editable aquí)"
        )

    elif isinstance(chunk, ChunksText) or hasattr(chunk, "text"):
        output.append(f"TIPO: TEXTO")
        output.append("-" * 80)
        output.append(chunk.text)

    else:
        output.append("Contenido no reconocible o imagen.")

    output.append("\n" + "=" * 80)
    output.append("(Presiona 'q' para salir de esta vista)")
    return "\n".join(output)


def run_editor_mode(api: AIStudioDriveManager, chat_id: str):
    """
    Lógica encapsulada del editor visual.
    Funciona como una 'ventana modal' sobre la consola.
    """
    click.echo(f"Cargando chat {chat_id} para edición...")
    chat_data = api.get_chat_ia_studio(chat_id)
    if not chat_data:
        click.secho("Error descargando chat.", fg="red")
        return

    chunks = copy.deepcopy(chat_data.chunkedPrompt.chunks)
    unsaved_changes = False
    while True:
        click.clear()
        click.secho(
            "\n--- MODO EDICIÓN (Borrador en Memoria) ---", fg="green", bold=True
        )
        click.echo(f"Chat ID: {chat_id}")
        if unsaved_changes:
            click.secho(
                "(!) HAY CAMBIOS SIN GUARDAR. Usa 'save' para aplicar.",
                fg="magenta",
                bold=True,
            )

        # Renderizar Tabla
        click.echo("\n" + "-" * 100)
        click.echo(
            f" {'ID':<3} | {'ROL':<6} | {'TIPO':<5} | {'TOKENS':<8} | {'PREVISUALIZACIÓN'}"
        )
        click.echo("-" * 100)

        for i, chunk in enumerate(chunks):
            row_str = format_chunk_row(i, chunk)
            color = None
            if i == 0:
                color = "blue"  # Contexto protegido
            if i == len(chunks) - 1:
                color = "cyan"  # Último mensaje

            if color:
                click.secho(row_str, fg=color)
            else:
                click.echo(row_str)
        click.echo("-" * 100)

        try:
            cmd_input = input("edit >> ").strip()
        except (KeyboardInterrupt, EOFError):
            break

        if not cmd_input:
            continue

        parts = cmd_input.split()
        cmd = parts[0].lower()
        args = parts[1:]

        if cmd in ["exit", "back", "q"]:
            if unsaved_changes:
                confirm = input(
                    "Tienes cambios sin guardar. ¿Salir y descartar? (s/n): "
                )
                if confirm.lower() != "s":
                    continue
            break

        elif cmd == "help":
            input(
                "\nComandos:\n  view <id> : Ver contenido completo.\n  rm <id>   : Borrar mensaje.\n  pop [n]   : Borrar últimos n.\n  save      : Guardar en Drive.\n  exit      : Salir.\n\n[Enter] para continuar..."
            )

        elif cmd == "view":
            if args and args[0].isdigit():
                idx = int(args[0])
                if 0 <= idx < len(chunks):
                    click.echo_via_pager(get_full_content_for_pager(chunks[idx]))
                else:
                    input("ID fuera de rango. [Enter]...")

        elif cmd == "rm":
            if not args:
                click.secho("Uso: rm <id> o rm <inicio>-<fin>", fg="red")
                time.sleep(1)
                continue

            arg = args[0]
            indices_to_remove = set()

            try:
                # Caso Rango (ej: 3-5)
                if "-" in arg:
                    start_str, end_str = arg.split("-", 1)
                    start, end = int(start_str), int(end_str)
                    if start > end:
                        start, end = end, start
                    indices_to_remove.update(range(start, end + 1))

                # Caso Índice único (ej: 3)
                elif arg.isdigit():
                    indices_to_remove.add(int(arg))
                else:
                    click.secho(
                        "Formato inválido. Use número (N) o rango (N-M).", fg="red"
                    )
                    time.sleep(1.5)
                    continue

            except ValueError:
                click.secho("Error al interpretar los índices.", fg="red")
                time.sleep(1)
                continue

            # Proteger el Contexto (índice 0)
            if 0 in indices_to_remove:
                click.secho(
                    "(!) El índice 0 (Contexto) está protegido y no se borrará.",
                    fg="yellow",
                )
                indices_to_remove.discard(0)
                time.sleep(1.5)

            # Filtrar índices fuera de rango real
            max_idx = len(chunks) - 1
            valid_indices = {i for i in indices_to_remove if 0 < i <= max_idx}
            if not valid_indices:
                click.secho(
                    "No se seleccionaron índices válidos para eliminar.", fg="yellow"
                )
                time.sleep(1)
                continue

            # Reconstruimos la lista excluyendo los índices marcados
            new_chunks = [
                chunk for i, chunk in enumerate(chunks) if i not in valid_indices
            ]
            chunks = new_chunks
            unsaved_changes = True

            count = len(valid_indices)
            click.secho(
                f"Marcsdos {count} mensaje(s) para eliminar. Usa 'save' para confirmar.",
                fg="green",
            )
            time.sleep(1)

        elif cmd == "pop":
            count = 1
            if args and args[0].isdigit():
                count = int(args[0])

            popped = 0
            for _ in range(count):
                if len(chunks) > 1:  # Protege índice 0
                    chunks.pop()
                    popped += 1
                    unsaved_changes = True
            if popped > 0:
                print(f"Eliminados {popped} mensajes.")
                time.sleep(0.5)

        elif cmd == "save":
            if not unsaved_changes:
                print("No hay cambios.")
                time.sleep(1)
                continue

            print("Subiendo cambios a Google Drive...")
            chat_data.chunkedPrompt.chunks = chunks
            if api.update_chat_file(chat_id, chat_data):
                click.secho("¡Guardado exitoso!", fg="green")
                unsaved_changes = False
                time.sleep(1.5)
            else:
                click.secho("Error al guardar.", fg="red")
        else:
            click.secho("Comando no reconocido.", fg="red")
            input("[Enter]...")


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
    prompt_text = get_custom_prompt(project_path)
    drive_document = DriveDocument(id=document["id"])
    chat_file = ChunksDocument(
        driveDocument=drive_document, role="user", tokenCount=expected_tokens
    )
    chunks_text_prompt = ChunksText(text=prompt_text, role="user", tokenCount=None)
    chunks_text_response = ChunksText(
        text=RESPONSE_TEMPLATE, role="model", tokenCount=None
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
                monitor.stop_monitoring()
                print("Iniciando reinicio...")
                date_session_reset = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                monitor.create_named_snapshot(f"ANTES DEL RESET {date_session_reset}")
                try:
                    state = update_context(api, project_path, state)
                    save_project_context_state(project_path, state)
                    monitor.state = state
                except Exception as e:
                    print(f"Error: {e}")
                    continue
                if api.clear_chat_ia_studio(state["chat_id"]):
                    print("Chat limpiado.")
                if state.get("monitor_active", False):
                    monitor.start_monitoring()
                print("¡Sesión reiniciada!")
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


@click.group()
def main():
    """
    Herramienta CLI para gestionar contexto de proyecto en Google AI Studio.
    """
    pass


@main.command(name="run")
@click.argument(
    "project_path", type=click.Path(exists=True, file_okay=False), required=True
)
@click.option(
    "-u", "--update-only", is_flag=True, help="Solo crea/actualiza el contexto y sale."
)
@click.option(
    "-i", "--interactive-only", is_flag=True, help="Entra directo a modo interactivo."
)
@click.option(
    "--use",
    help="Usa un perfil específico temporalmente para esta ejecución.",
    default=None,
)
def run_command(project_path, update_only, interactive_only, use):
    """
    Analiza y sincroniza el proyecto en la ruta indicada.

    Uso: project_context run .
    """
    project_path = Path(project_path).resolve()

    if use:
        available_profiles = profile_manager.list_profiles()
        if use not in available_profiles:
            click.secho(f"Error: El perfil de usuario '{use}' no existe.", fg="red")
            click.echo(f"Perfiles disponibles: {', '.join(available_profiles)}")
            sys.exit(1)

        profile_manager.set_temporary_profile(use)
        click.secho(f"Usando perfil temporal: {use}", fg="yellow")

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
        "La próxima vez que ejecutes 'project_context run .', se te pedirá autenticación."
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
    click.echo(f"   └── Ruta:    {secrets_path}")

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
    Util si este perfil usa una App de Google Cloud distinta a la global.
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
