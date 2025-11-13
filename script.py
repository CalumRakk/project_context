from pathlib import Path

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

if __name__ == "__main__":
    project_path = Path(r"D:\github Leo\project_context")
    last_modified = project_path.stat().st_mtime

    project_context_state = load_project_context_state(project_path)
    if project_context_state is None:
        content = generate_context(project_path)
        path_context = save_context(project_path, content)
        content_md5 = compute_md5(path_context)

        cookies_path = r"aistudio.google.com_cookies.txt"
        browser = Browser(cookies_path=cookies_path)
        api = GoogleDriveManager()
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
