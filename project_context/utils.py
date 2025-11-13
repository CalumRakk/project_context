import json
import os
import sys
from pathlib import Path
from typing import Optional, Union, cast

from gitingest import ingest

from project_context.models import generate_unique_id

PROMPT_TEMPLATE = """Eres un ingeniero de software senior y experto en análisis de código completo.

A continuación te paso **todo el código fuente de mi proyecto** en formato texto plano optimizado para LLMs (generado con Gitingest). 

Formato del resumen:
- Las rutas de archivo aparecen entre ``` (tres acentos graves) seguidas del path completo.
- Luego viene el contenido completo del archivo.
- Los directorios vacíos o archivos ignorados (.gitignore, node_modules, binarios, etc.) están excluidos.
- Todo el proyecto está aquí, no hay archivos externos ni dependencias que no se vean.

INSTRUCCIONES OBLIGATORIAS:
1. Analiza TODA la estructura del proyecto antes de responder.
2. Recuerda el contenido de cada archivo importante (no lo olvides en respuestas siguientes).
3. Si necesitas ver algún archivo de nuevo, puedes pedírmelo por su ruta exacta.
4. Cuando hagas sugerencias de código, respeta la arquitectura actual y el estilo del proyecto.

¿Entendido? Confirma con "Listo, proyecto cargado" y dime brevemente de qué va el proyecto según lo que ves.
"""

RESPONSE_TEMPLATE = """Entendido, proyecto cargado."""


def compute_md5(file_path):
    import hashlib

    hash_md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()


def get_user_config_dir(app_name: str) -> Path:
    if sys.platform.startswith("win"):
        # En Windows se usa APPDATA → Roaming
        return Path(cast(str, os.getenv("APPDATA"))) / app_name
    elif sys.platform == "darwin":
        # En macOS
        return Path.home() / "Library" / "Application Support" / app_name
    else:
        # En Linux / Unix
        return Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config")) / app_name


APP_FOLDER = get_user_config_dir("project_context")
# chat_path_cache = APP_FOLDER / "chat_cache.json"


# def save_chats(chats: list[dict], chat_path_cache=chat_path_cache):
#     chat_path_cache.parent.mkdir(parents=True, exist_ok=True)
#     with open(chat_path_cache, "w", encoding="utf-8") as f:
#         json.dump(chats, f, ensure_ascii=False, indent=4)


# def load_chats(chat_path_cache=chat_path_cache) -> list[dict]:
#     if not chat_path_cache.exists():
#         return []
#     with open(chat_path_cache, "r", encoding="utf-8") as f:
#         chats = json.load(f)
#     return chats


def human_to_int(value):
    value = value.strip().lower()
    multipliers = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}

    if value[-1] in multipliers:
        return int(float(value[:-1]) * multipliers[value[-1]])
    return int(float(value))


def generate_context(proyect_path: Union[str, Path]) -> tuple[str, int]:
    proyect_path = Path(proyect_path) if isinstance(proyect_path, str) else proyect_path
    summary, tree, content = ingest(str(proyect_path))

    estimated_tokens = human_to_int(summary.split()[-1])
    context = tree + "\n\n" + content
    return context, estimated_tokens


def save_context(project_path: Union[str, Path], context: str) -> Path:
    project_path = Path(project_path) if isinstance(project_path, str) else project_path

    inodo = generate_unique_id(project_path)

    output = APP_FOLDER / inodo / f"project_context.txt"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(context, encoding="utf-8")
    return output


def save_project_context_state(
    project_path: Union[str, Path], project_context_state: dict
):
    project_path = Path(project_path) if isinstance(project_path, str) else project_path

    inodo = generate_unique_id(project_path)

    output = APP_FOLDER / inodo / f"project_context_state.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(project_context_state), encoding="utf-8")


def load_project_context_state(
    project_path: Union[str, Path],
) -> Optional[dict]:
    project_path = Path(project_path) if isinstance(project_path, str) else project_path

    inodo = generate_unique_id(project_path)

    input_path = APP_FOLDER / inodo / f"project_context_state.json"
    if not input_path.exists():
        return None
    content = input_path.read_text(encoding="utf-8")
    return json.loads(content)
