import json
import logging
import os
import re
import sys
from fnmatch import fnmatch
from pathlib import Path
from typing import List, Optional, Tuple, Union, cast

import gitingest
import pathspec
from rich.console import Console
from rich.theme import Theme

from project_context.ui.ui import UI

logger = logging.getLogger(__name__)

COMMIT_TASK_MARKER = "<!-- TASK:COMMIT_SUGGESTION -->"

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
IMAGE_INSERTION_PROMPT = """He detectado y adjuntado las siguientes imágenes relacionadas con el archivo `{filename}`.
Utilízalas como referencia visual para complementar tu comprensión del proyecto."""

IMAGE_INSERTION_RESPONSE = """Entendido. He recibido y procesado las imágenes vinculadas a `{filename}`.
Ya tengo la referencia visual necesaria para ayudarte con esta parte del proyecto. ¿En qué puedo ayudarte ahora?"""


custom_theme = Theme(
    {
        "info": "dim cyan",
        "warning": "magenta",
        "error": "bold red",
        "success": "bold green",
        "progress": "italic blue",
    }
)

console = Console(theme=custom_theme)


def get_app_root_dir() -> Path:
    """Devuelve la raíz de configuración global (~/.config/project_context)."""
    env_override = os.getenv("PROJECT_CONTEXT_HOME")
    if env_override:
        return Path(env_override)

    if sys.platform.startswith("win"):
        base = Path(cast(str, os.getenv("APPDATA")))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "project_context"


def compute_md5(source: Union[bytes, str, Path]) -> str:
    """Calcula el hash MD5 de un bloque de bytes o de un archivo físico."""
    import hashlib

    hash_md5 = hashlib.md5()

    if isinstance(source, bytes):
        hash_md5.update(source)
    else:
        file_path = Path(source)  # type: ignore
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)

    return hash_md5.hexdigest()


def generate_unique_id(path: Union[str, Path]) -> str:
    p = Path(path) if isinstance(path, str) else path
    st = p.stat()
    return f"{st.st_dev}-{st.st_ino}"


def human_to_int(value) -> int:
    value = value.strip().lower()
    multipliers = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}
    if value[-1] in multipliers:
        return int(float(value[:-1]) * multipliers[value[-1]])
    return int(float(value))


def get_ignore_patterns(folder: Path, filename: str) -> List[str]:
    """Lee patrones de un archivo de ignore (gitignore o contextignore)."""
    path_file = folder / filename
    if path_file.exists():
        return [
            i.strip()
            for i in path_file.read_text(encoding="utf-8").splitlines() + [filename]
            if i.strip() and not i.strip().startswith("#")
        ]
    return []


def get_local_context_dir(project_path: Union[str, Path]) -> Path:
    """Obtiene y garantiza la existencia del directorio de metadatos local."""
    path = Path(project_path)
    local_dir = path / ".project_context"
    local_dir.mkdir(parents=True, exist_ok=True)
    return local_dir


def ensure_gitignore(project_path: Union[str, Path], state_data: Optional[dict] = None):
    """Verifica y añade la regla de exclusión del directorio local a .gitignore."""
    project_path = Path(project_path)

    if state_data and state_data.get("auto_gitignore") is False:
        return

    gitignore_path = project_path / ".gitignore"
    rule = ".project_context/"

    try:
        content = ""
        if gitignore_path.exists():
            content = gitignore_path.read_text(encoding="utf-8")

        lines = [line.strip() for line in content.splitlines()]
        if any(line == rule or line == ".project_context" for line in lines):
            return

        UI.info("Añadiendo '.project_context/' a .gitignore...")
        suffix = "\n" if content and not content.endswith("\n") else ""
        new_content = (
            content + suffix + f"\n# Metadatos locales de project-context-cli\n{rule}\n"
        )
        gitignore_path.write_text(new_content, encoding="utf-8")
        UI.success(".gitignore actualizado automáticamente.")
    except Exception as e:
        UI.warn(f"No se pudo escribir en el archivo .gitignore: {e}")


def generate_context(
    project_path: Union[str, Path], context_items: Optional[dict] = None
) -> tuple[str, int]:
    project_path = Path(project_path) if isinstance(project_path, str) else project_path

    if not context_items or (
        not context_items.get("files") and not context_items.get("folders")
    ):
        custom_ignores = get_ignore_patterns(project_path, ".contextignore")
        summary, tree, content = gitingest.ingest(
            str(project_path), exclude_patterns=set(custom_ignores)
        )
        estimated_tokens = human_to_int(summary.split()[-1])
        return tree + "\n\n" + content, estimated_tokens

    custom_ignores = get_ignore_patterns(project_path, ".contextignore")

    final_tree = "Directory structure (Custom Focus):\n"
    final_content = ""
    total_tokens = 0

    files = context_items.get("files", [])
    if files:
        final_tree += "└── [Archivos Específicos Añadidos]\n"
        for idx, f_path in enumerate(files):
            real_path = project_path / f_path
            prefix = "    └── " if idx == len(files) - 1 else "    ├── "
            final_tree += f"{prefix}{f_path}\n"

            if real_path.exists() and real_path.is_file():
                try:
                    text = real_path.read_text(encoding="utf-8")
                    final_content += f"================================================\nFILE: {f_path}\n================================================\n{text}\n\n"
                    total_tokens += len(text) // 4
                except Exception as e:
                    final_content += f"================================================\nFILE: {f_path}\n================================================\n[Error leyendo archivo: {e}]\n\n"

    folders = context_items.get("folders", [])
    exclusions = context_items.get("exclusions", [])
    if folders:
        final_tree += "└── [Carpetas Específicas Añadidas]\n"
        for folder in folders:
            real_folder = project_path / folder
            if real_folder.exists() and real_folder.is_dir():
                folder_path_obj = Path(folder)
                folder_specific_ignores = list(custom_ignores)

                for exc in exclusions:
                    exc_path = Path(exc)
                    try:
                        rel_exc = exc_path.relative_to(folder_path_obj)
                        folder_specific_ignores.append(str(rel_exc.as_posix()))
                    except ValueError:
                        pass

                summary, tree, content = gitingest.ingest(
                    str(real_folder), exclude_patterns=set(folder_specific_ignores)
                )

                indented_tree = "\n".join(f"    {line}" for line in tree.splitlines())
                final_tree += f"{indented_tree}\n"

                final_content += f"{content}\n"
                total_tokens += human_to_int(summary.split()[-1])

    full_context = final_tree + "\n" + final_content
    return full_context, total_tokens


def save_context(project_path: Union[str, Path], context: str) -> Path:
    """Guarda el contexto consolidado en last_context.txt."""
    project_path = Path(project_path)
    local_dir = get_local_context_dir(project_path)
    output = local_dir / "last_context.txt"
    output.write_text(context, encoding="utf-8")
    return output


def save_project_context_state(
    project_path: Union[str, Path], project_context_state: dict
):
    """Guarda el estado del proyecto en el archivo state.json local."""
    project_path = Path(project_path)
    local_dir = get_local_context_dir(project_path)
    output_path = local_dir / "state.json"

    output_path.write_text(
        json.dumps(project_context_state, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    ensure_gitignore(project_path, project_context_state)


def load_project_context_state(project_path: Union[str, Path]) -> Optional[dict]:
    """Carga el archivo state.json local del proyecto."""
    project_path = Path(project_path)
    local_dir = get_local_context_dir(project_path)
    state_path = local_dir / "state.json"

    if state_path.exists():
        try:
            return json.loads(state_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error(f"Error cargando state.json: {e}")
            return None
    return None


def has_files_modified_since(
    st_mtime: float, target_path: Path | str, gitignore=True
) -> bool:
    ignore = []
    target_path = Path(target_path)
    if gitignore:
        path_gitignore = target_path / ".gitignore"
        if path_gitignore.exists():
            ignore = [
                i.strip()
                for i in path_gitignore.read_text(encoding="utf-8").splitlines()
                if i.strip() and not i.strip().startswith("#")
            ]

    if target_path.is_file():
        fecha_mod = target_path.stat().st_mtime
        if fecha_mod > st_mtime:
            return True
        return False
    elif target_path.is_dir():
        for file in target_path.rglob("*"):
            if not file.is_file():
                continue
            ruta_rel = str(file.relative_to(target_path))
            if gitignore is True:
                if any(fnmatch(ruta_rel, patron) for patron in ignore):
                    continue
            fecha_mod = file.stat().st_mtime
            if fecha_mod > st_mtime:
                return True
        return False
    raise Exception("No se encontraron archivos modificados")


def resolve_prompt(project_path: Union[str, Path]) -> str:
    """Busca un archivo '.contextprompt' o retorna el template por defecto."""
    project_path = Path(project_path) if isinstance(project_path, str) else project_path
    prompt_file = project_path / ".contextprompt"

    if prompt_file.exists() and prompt_file.is_file():
        try:
            content = prompt_file.read_text(encoding="utf-8").strip()
            if content:
                print(f"Usando prompt personalizado desde: {prompt_file.name}")
                return content
        except Exception as e:
            print(f"Advertencia: No se pudo leer {prompt_file.name}: {e}")

    return PROMPT_TEMPLATE


def get_filtered_files(project_path: Path, extensions: set[str]) -> list[Path]:
    """Busca archivos con ciertas extensiones respetando ignores."""
    patterns = get_ignore_patterns(project_path, ".gitignore")
    patterns += get_ignore_patterns(project_path, ".contextignore")
    patterns += [".git/", "node_modules/", "__pycache__/", ".venv/", "venv/"]

    spec = pathspec.PathSpec.from_lines("gitwildmatch", patterns)
    valid_files = []

    for file in project_path.rglob("*"):
        if not file.is_file():
            continue

        if file.suffix.lower() not in extensions:
            continue

        rel_path = file.relative_to(project_path)
        if not spec.match_file(str(rel_path)):
            valid_files.append(file)

    return valid_files


def get_potential_media_folders(project_path: Path) -> list[Path]:
    """Busca directorios susceptibles de almacenar assets visuales."""
    common_names = {
        "assets",
        "attachments",
        "img",
        "images",
        "media",
        "static",
        "public",
    }
    found = []
    for p in project_path.rglob("*"):
        if p.is_dir() and p.name.lower() in common_names:
            if not any(
                part.startswith(".") or part == "node_modules" for part in p.parts
            ):
                found.append(p)
    return found


def extract_image_references_from_text(content: str) -> List[Tuple[str, bool]]:
    """Extrae referencias Markdown, HTML o WikiLink de imágenes."""
    results = []
    std_patterns = [
        r"!\[.*?\]\((.*?\.(?:png|jpg|jpeg|webp|gif))\)",
        r'<img\s+[^>]*src=["\'](.*?\.(?:png|jpg|jpeg|webp|gif))["\']',
    ]
    for pat in std_patterns:
        matches = re.findall(pat, content, re.IGNORECASE)
        results.extend(
            [
                (m.strip().lstrip("/"), False)
                for m in matches
                if not m.startswith(("http", "data:"))
            ]
        )

    wiki_matches = re.findall(r"!\[\[(.*?)(?:\|.*?)?\]\]", content)
    results.extend(
        [(m.strip(), True) for m in wiki_matches if not m.startswith(("http", "data:"))]
    )

    return list(dict.fromkeys(results))


def extract_image_references(file_path: Path) -> List[Tuple[str, bool]]:
    if not file_path.exists():
        return []
    return extract_image_references_from_text(file_path.read_text(encoding="utf-8"))


def _get_stash_path(project_path: Union[str, Path], filename: str) -> Path:
    return get_local_context_dir(project_path) / filename


def save_stash(project_path: Union[str, Path], filename: str, content: str):
    _get_stash_path(project_path, filename).write_text(content, encoding="utf-8")


def load_stash(project_path: Union[str, Path], filename: str) -> Optional[str]:
    path = _get_stash_path(project_path, filename)
    return path.read_text(encoding="utf-8") if path.exists() else None


def clear_stash(project_path: Union[str, Path], filename: str):
    path = _get_stash_path(project_path, filename)
    if path.exists():
        path.unlink()


def get_context_tree(
    project_path: Union[str, Path], context_items: Optional[dict] = None
) -> str:
    project_path = Path(project_path) if isinstance(project_path, str) else project_path

    if not context_items or (
        not context_items.get("files") and not context_items.get("folders")
    ):
        custom_ignores = get_ignore_patterns(project_path, ".contextignore")
        summary, tree, content = gitingest.ingest(
            str(project_path), exclude_patterns=set(custom_ignores)
        )
        return tree

    custom_ignores = get_ignore_patterns(project_path, ".contextignore")
    final_tree = "Directory structure (Custom Focus):\n"

    files = context_items.get("files", [])
    if files:
        final_tree += "└── [Archivos Específicos Añadidos]\n"
        for idx, f_path in enumerate(files):
            prefix = "    └── " if idx == len(files) - 1 else "    ├── "
            final_tree += f"{prefix}{f_path}\n"

    folders = context_items.get("folders", [])
    exclusions = context_items.get("exclusions", [])
    if folders:
        final_tree += "└── [Carpetas Específicas Añadidas]\n"
        for folder in folders:
            real_folder = project_path / folder
            if real_folder.exists() and real_folder.is_dir():
                folder_path_obj = Path(folder)
                folder_specific_ignores = list(custom_ignores)

                for exc in exclusions:
                    exc_path = Path(exc)
                    try:
                        rel_exc = exc_path.relative_to(folder_path_obj)
                        folder_specific_ignores.append(str(rel_exc.as_posix()))
                    except ValueError:
                        pass

                summary, tree, content = gitingest.ingest(
                    str(real_folder), exclude_patterns=set(folder_specific_ignores)
                )
                indented_tree = "\n".join(f"    {line}" for line in tree.splitlines())
                final_tree += f"{indented_tree}\n"

    return final_tree


def validate_google_secrets_file(path: Path) -> bool:
    """Verifica si el archivo JSON cumple la estructura de credenciales OAuth de Google."""
    if not path.exists() or not path.is_file():
        return False
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
        for key in ["installed", "web"]:
            if key in data and isinstance(data[key], dict):
                sub_data = data[key]
                if "client_id" in sub_data and "client_secret" in sub_data:
                    return True
        return False
    except Exception:
        return False
