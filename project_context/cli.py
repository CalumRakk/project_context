from pathlib import Path

import click

from project_context.api_drive import GoogleDriveManager
from project_context.browser import Browser
from project_context.utils import (
    PROMPT_TEMPLATE,
    compute_md5,
    generate_context,
    load_project_context_state,
    save_context,
    save_project_context_state,
)


def interactive_session(browser: Browser, api: GoogleDriveManager, state: dict):
    """Inicia un bucle interactivo para recibir comandos del usuario."""
    print("\nOk. Contexto cargado. Sesión interactiva iniciada.")
    print("\tEscribe 'help' para ver los comandos disponibles.\n")

    while True:
        try:
            command_line = input(">> ")
            if not command_line.strip():
                continue

            parts = command_line.split(" ", 1)
            command = parts[0].lower()
            args = parts[1] if len(parts) > 1 else ""

            if command in ["exit", "quit"]:
                print("Cerrando navegador y terminando sesión...")
                browser.close()
                break
            elif command == "ask":
                if not args:
                    print("Error: El comando 'ask' requiere una pregunta.")
                    continue
                print("Enviando prompt...")
                response, _ = browser.chat.write_prompt(args)
                print("\nRespuesta de la IA:\n--------------------")
                print(response)
                print("--------------------")
            elif command == "help":
                print("\nComandos disponibles:")
                print('  ask "<pregunta>" - Envía una pregunta a la IA.')
                print("  clear              - Limpia el historial del chat.")
                print(
                    "  update             - Revisa y actualiza el contexto si el proyecto cambió."
                )
                print("  status             - Muestra el estado de la sesión.")
                print("  exit / quit        - Cierra la sesión.\n")
            elif command == "clear":
                print("Limpiando historial del chat...")
                success = api.clear_chat_ia_studio(state["chat_id"])
                if success:
                    print("Historial limpiado.")
                    browser.driver.refresh()
                else:
                    print("Error al limpiar el historial.")
            else:
                print(f"Comando desconocido: '{command}'")

        except KeyboardInterrupt:
            print("\nCerrando sesión por interrupción.")
            browser.close()
            break
        except Exception as e:
            print(f"Ocurrió un error: {e}")


@click.command()
@click.argument(
    "project_path", type=click.Path(exists=True, file_okay=False, resolve_path=True)
)
def main(project_path):
    """
    Inicia o actualiza el contexto de un proyecto para Google AI Studio
    y entra en una sesión interactiva.
    """
    cookies_path = r"aistudio.google.com_cookies.txt"
    browser = Browser(cookies_path=cookies_path)
    api = GoogleDriveManager()

    project_path = Path(project_path) if isinstance(project_path, str) else project_path
    last_modified = project_path.stat().st_mtime

    project_context_state = load_project_context_state(project_path)
    if project_context_state is None:
        content = generate_context(project_path)
        path_context = save_context(project_path, content)
        content_md5 = compute_md5(path_context)

        browser.chat.select_model("Gemini 2.5 Flash")

        browser.chat.attach_file(path_context)
        response, chat_id = browser.chat.write_prompt(
            PROMPT_TEMPLATE, thinking_mode=False
        )

        chat_ia_studio = api.get_chat_ia_studio(chat_id)
        if not chat_ia_studio:
            raise ValueError("No se pudo obtener el contenido del archivo subido.")
        file_ids = [
            getattr(i, "driveDocument").id
            for i in chat_ia_studio.chunkedPrompt.chunks
            if hasattr(i, "driveDocument")
        ]
        if not file_ids:
            raise ValueError(
                "No se encontraron IDs de archivos en el contenido obtenido."
            )

        project_context_state = {
            "path": str(project_path),
            "last_modified": last_modified,
            "md5": content_md5,
            "chat_id": chat_id,
            "file_id": file_ids[0],
        }
        save_project_context_state(project_path, project_context_state)
    else:
        last_modified_saved = project_context_state.get("last_modified", 0)
        context_md5_saved = project_context_state.get("md5", "")
        file_id_saved = project_context_state.get("file_id", "")
        chat_id_saved = project_context_state.get("chat_id", "")

        current_mtime = project_path.stat().st_mtime
        if current_mtime <= last_modified_saved:
            print(
                "El proyecto no ha cambiado desde la última vez. No se requiere actualización."
            )
        else:
            api = GoogleDriveManager()
            print("El proyecto ha cambiado. Generando nuevo contexto...")
            content = generate_context(project_path)
            path_context = save_context(project_path, content)
            content_md5 = compute_md5(path_context)
            if content_md5 == context_md5_saved:
                print(
                    "El contenido del proyecto no ha cambiado. No se requiere actualización."
                )
            else:
                api.update_file_content(file_id_saved, path_context)

                project_context_state["last_modified"] = current_mtime
                project_context_state["md5"] = content_md5
                save_project_context_state(project_path, project_context_state)

        browser.chat.go_to_chat(chat_id_saved)

    interactive_session(browser, api, project_context_state)
