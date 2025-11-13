import json
import os
import sys
from fnmatch import fnmatch
from pathlib import Path
from typing import Optional, Union, cast

from gitingest import ingest

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


def generate_unique_id(path: Union[str, Path]) -> str:
    """Genera un identificador único a partir del stat del archivo.

    Formato: "<st_dev>-<st_ino>" — robusto contra renombres pero no contra
    copiar el archivo a otro FS.
    """
    p = Path(path) if isinstance(path, str) else path
    st = p.stat()
    return f"{st.st_dev}-{st.st_ino}"


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


def has_files_modified_since(st_mtime: float, folder: Path, gitignore=True) -> bool:
    """
    Devuelve True si algún archivo dentro de `folder` ha sido modificado después de `st_mtime`.
    Si `gitignore` es True, se respetan las reglas del archivo .gitignore en la raíz de `folder`.
    """
    if gitignore:
        path_gitignore = folder / ".gitignore"
        if path_gitignore.exists():
            ignore = [
                i.strip()
                for i in path_gitignore.read_text(encoding="utf-8").splitlines()
                if i.strip() and not i.strip().startswith("#")
            ]
        else:
            ignore = []

    folder = Path(folder)
    for file in folder.rglob("*"):
        if not file.is_file():
            continue

        ruta_rel = str(file.relative_to(folder))

        # Verificamos si coincide con alguno de los patrones a ignorar
        if gitignore is True:
            if any(fnmatch(ruta_rel, patron) for patron in ignore):  # type: ignore
                continue

        fecha_mod = file.stat().st_mtime
        if fecha_mod > st_mtime:
            return True
    return False
