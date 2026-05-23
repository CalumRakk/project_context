import json
import logging
import os
import re
import shutil
import sys
from fnmatch import fnmatch
from pathlib import Path
from typing import List, Optional, Tuple, Union, cast

import gitingest
import pathspec
from git import Repo, exc
from rich.console import Console
from rich.theme import Theme

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


class UI:
    @staticmethod
    def info(message: str):
        console.print(f"[info]i[/] {message}")

    @staticmethod
    def success(message: str):
        console.print(f"[success]>[/] {message}")

    @staticmethod
    def warn(message: str):
        console.print(f"[warning]![/] {message}")

    @staticmethod
    def error(message: str):
        console.print(f"[error]X[/] {message}")


def get_app_root_dir() -> Path:
    """Devuelve la raíz de configuración global (~/.config/project_context)."""
    if sys.platform.startswith("win"):
        base = Path(cast(str, os.getenv("APPDATA")))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "project_context"


class ProfileManager:
    def __init__(self):
        self.root_dir = get_app_root_dir()
        self.profiles_dir = self.root_dir / "profiles"
        self.config_file = self.root_dir / "global_config.json"
        self._temp_profile: Optional[str] = None
        self._ensure_structure()

    def _ensure_structure(self):
        """Crea la estructura base y migra datos antiguos si existen."""
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.profiles_dir.mkdir(exist_ok=True)

        # Lógica de Migración Automática
        old_token = self.root_dir / "token.json"
        # Buscamos carpetas que parezcan contextos (tienen guiones, ej: "2050-343")
        # y que no sean la carpeta 'profiles'.
        old_items = [
            x
            for x in self.root_dir.iterdir()
            if x.is_dir() and "-" in x.name and x.name != "profiles"
        ]

        # Si encontramos datos viejos en la raíz, los movemos a 'default'
        if (old_token.exists() or old_items) and not (
            self.profiles_dir / "default"
        ).exists():
            print("Detectada estructura antigua. Migrando al perfil 'default'...")
            default_dir = self.profiles_dir / "default"
            default_dir.mkdir(parents=True, exist_ok=True)

            if old_token.exists():
                shutil.move(str(old_token), str(default_dir / "token.json"))

            for item in old_items:
                shutil.move(str(item), str(default_dir / item.name))

            self.set_active_profile("default")

    def set_temporary_profile(self, profile_name: str):
        """Establece un perfil activo solo para la ejecución actual (en memoria)."""
        self._temp_profile = profile_name

    def get_active_profile_name(self) -> str:
        if self._temp_profile:
            return self._temp_profile

        if not self.config_file.exists():
            return "default"

        config = json.loads(self.config_file.read_text())
        return config.get("current_profile", "default")


    def set_active_profile(self, profile_name: str):
        self._temp_profile = None

        config = {"current_profile": profile_name}
        self.config_file.write_text(json.dumps(config))
        (self.profiles_dir / profile_name).mkdir(parents=True, exist_ok=True)

    def get_working_dir(self) -> Path:
        """Devuelve la ruta donde se guardan los datos del perfil actual."""
        profile = self.get_active_profile_name()
        path = self.profiles_dir / profile
        path.mkdir(parents=True, exist_ok=True)
        return path

    def list_profiles(self) -> list[str]:
        return [d.name for d in self.profiles_dir.iterdir() if d.is_dir()]

    def resolve_secrets_file(self) -> Tuple[Path, str]:
        """
        Estrategia de Cascada:
        1. Busca client_secrets.json en la carpeta del perfil.
        2. Si no está, busca en la carpeta global.
        Retorna (Path, Tipo_Origen)
        """
        profile_dir = self.get_working_dir()
        specific_secrets = profile_dir / "client_secrets.json"

        if specific_secrets.exists():
            return specific_secrets, "Perfil (Específico)"

        global_secrets = self.root_dir / "client_secrets.json"
        return global_secrets, "Global (Compartido)"


profile_manager = ProfileManager()


def compute_md5(file_path):
    import hashlib

    hash_md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()


def generate_unique_id(path: Union[str, Path]) -> str:
    p = Path(path) if isinstance(path, str) else path
    st = p.stat()
    return f"{st.st_dev}-{st.st_ino}"


def human_to_int(value):
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


def generate_context(
    project_path: Union[str, Path], context_items: Optional[dict] = None
) -> tuple[str, int]:
    project_path = Path(project_path) if isinstance(project_path, str) else project_path

    if not context_items or (not context_items.get("files") and not context_items.get("folders")):
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

    # -- Procesa Archivos Explícitos --
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
                    total_tokens += len(text) // 4  # Estimación rápida de tokens
                except Exception as e:
                    final_content += f"================================================\nFILE: {f_path}\n================================================\n[Error leyendo archivo: {e}]\n\n"

    folders = context_items.get("folders", [])
    if folders:
        final_tree += "└── [Carpetas Específicas Añadidas]\n"
        for folder in folders:
            real_folder = project_path / folder
            if real_folder.exists() and real_folder.is_dir():
                summary, tree, content = gitingest.ingest(
                    str(real_folder), exclude_patterns=set(custom_ignores)
                )

                # Ajustamos la indentación del árbol para que encaje visualmente
                indented_tree = "\n".join(f"    {line}" for line in tree.splitlines())
                final_tree += f"{indented_tree}\n"

                final_content += f"{content}\n"
                total_tokens += human_to_int(summary.split()[-1])

    full_context = final_tree + "\n" + final_content
    return full_context, total_tokens


def save_context(project_path: Union[str, Path], context: str) -> Path:
    project_path = Path(project_path) if isinstance(project_path, str) else project_path
    inodo = generate_unique_id(project_path)

    # USAMOS EL DIRECTORIO DEL PERFIL
    base_dir = profile_manager.get_working_dir()
    output = base_dir / inodo / "project_context.txt"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(context, encoding="utf-8")
    return output


def save_project_context_state(
    project_path: Union[str, Path], project_context_state: dict
):
    project_path = Path(project_path) if isinstance(project_path, str) else project_path
    inodo = generate_unique_id(project_path)

    base_dir = profile_manager.get_working_dir()
    output = base_dir / inodo / "project_context_state.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(project_context_state), encoding="utf-8")


def load_project_context_state(project_path: Union[str, Path]) -> Optional[dict]:
    """Devuelve un diccionario con el estado del proyecto."""
    project_path = Path(project_path) if isinstance(project_path, str) else project_path
    # TODO: Cambiar inodo por otra forma de identificar el proyecto.
    inodo = generate_unique_id(project_path)

    base_dir = profile_manager.get_working_dir()
    input_path = base_dir / inodo / "project_context_state.json"
    if not input_path.exists():
        return None
    content = input_path.read_text(encoding="utf-8")
    return json.loads(content)


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
    """
    Busca un archivo '.contextprompt' en la raíz del proyecto.
    Si existe, usa su contenido. Si no, usa el template por defecto.
    """
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


def get_diff_message(project_path: Path) -> Optional[str]:
    """
    Obtiene el diff de los archivos en STAGE (listos para commit).
    Si no hay archivos en stage, retorna None.
    """
    try:
        # search_parent_directories=True permite ejecutarlo en subcarpetas
        repo = Repo(project_path, search_parent_directories=True)

        # Obtiene el diff de lo que está en 'stage' (cached) vs HEAD
        diff_text = repo.git.diff("--cached")

        if not diff_text.strip():
            # Si es un repo nuevo sin commits previos, 'diff --cached' a veces retorna vacío
            # aunque haya archivos nuevos añadidos.
            if not repo.head.is_valid():
                status = repo.git.status("--short")
                if status:
                    return f"Initial commit. Files added:\n{status}"
            return None

        return diff_text

    except exc.InvalidGitRepositoryError:
        print("Error: El directorio actual no es un repositorio Git válido.")
        return None
    except Exception as e:
        print(f"Error obteniendo git diff: {e}")
        return None


def get_filtered_files(project_path: Path, extensions: set[str]) -> list[Path]:
    """
    Escanea el proyecto buscando archivos con ciertas extensiones,
    respetando .gitignore y .contextignore.
    """

    patterns = get_ignore_patterns(project_path, ".gitignore")
    patterns += get_ignore_patterns(project_path, ".contextignore")

    patterns += [".git/", "node_modules/", "__pycache__/", ".venv/", "venv/"]

    # Crear el objeto de especificación (formato gitignore)
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
    """Busca carpetas que probablemente contengan imágenes (assets, attachments, etc)."""
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
            # Evitar carpetas ignoradas (node_modules, .git, etc)
            if not any(
                part.startswith(".") or part == "node_modules" for part in p.parts
            ):
                found.append(p)
    return found


def extract_image_references(file_path: Path) -> list[tuple[str, bool]]:
    """
    Extrae referencias de imágenes.
    Retorna una lista de tuplas: (nombre_o_ruta, es_wikilink)
    """
    if not file_path.exists():
        return []
    content = file_path.read_text(encoding="utf-8")

    results = []
    # Markdown estándar e HTML
    std_patterns = [
        r"!\[.*?\]\((.*?\.(?:png|jpg|jpeg|webp|gif))\)",
        r'<img\s+[^>]*src=["\'](.*?\.(?:png|jpg|jpeg|webp|gif))["\']',
    ]
    for pat in std_patterns:
        matches = re.findall(pat, content, re.IGNORECASE)
        results.extend(
            [(m.strip(), False) for m in matches if not m.startswith(("http", "data:"))]
        )

    # WikiLinks (estilo Obsidian): ![[imagen.png|237]]
    wiki_matches = re.findall(r"!\[\[(.*?)(?:\|.*?)?\]\]", content)
    results.extend(
        [(m.strip(), True) for m in wiki_matches if not m.startswith(("http", "data:"))]
    )

    return list(dict.fromkeys(results))


# ==========================================
# UTILIDADES DE GIT
# ==========================================

def has_unstaged_changes(project_path: Path) -> bool:
    """Verifica si hay archivos modificados o untracked que no están en stage."""
    try:
        repo = Repo(project_path, search_parent_directories=True)
        # is_dirty(untracked_files=True) devuelve True si hay cambios sin commit/stage
        # Pero queremos saber si hay algo fuera del stage específicamente.
        # repo.untracked_files nos da los nuevos.
        # repo.index.diff(None) nos da los modificados sin stage.
        if repo.untracked_files or repo.index.diff(None):
            return True
        return False
    except exc.InvalidGitRepositoryError:
        return False

def stage_all_changes(project_path: Path):
    """Ejecuta git add . en el repositorio."""
    try:
        repo = Repo(project_path, search_parent_directories=True)
        repo.git.add(A=True)
    except Exception as e:
        UI.error(f"Error al hacer git add: {e}")



def save_chat_stash(project_path: Union[str, Path], chat_json: str):
    """Guarda una copia de seguridad del chat actual en disco."""
    project_path = Path(project_path) if isinstance(project_path, str) else project_path
    inodo = generate_unique_id(project_path)
    base_dir = profile_manager.get_working_dir()
    output = base_dir / inodo / "chat_stash.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(chat_json, encoding="utf-8")

def load_chat_stash(project_path: Union[str, Path]) -> Optional[str]:
    """Recupera la copia de seguridad del chat desde el disco."""
    project_path = Path(project_path) if isinstance(project_path, str) else project_path
    inodo = generate_unique_id(project_path)
    base_dir = profile_manager.get_working_dir()
    input_path = base_dir / inodo / "chat_stash.json"
    if not input_path.exists():
        return None
    return input_path.read_text(encoding="utf-8")

def clear_chat_stash(project_path: Union[str, Path]):
    """Elimina el archivo de copia de seguridad."""
    project_path = Path(project_path) if isinstance(project_path, str) else project_path
    inodo = generate_unique_id(project_path)
    base_dir = profile_manager.get_working_dir()
    input_path = base_dir / inodo / "chat_stash.json"
    if input_path.exists():
        input_path.unlink()

def save_vanish_stash(project_path: Union[str, Path], chat_json: str):
    """Guarda una copia de seguridad del chat actual para el modo vanish."""
    project_path = Path(project_path) if isinstance(project_path, str) else project_path
    inodo = generate_unique_id(project_path)
    base_dir = profile_manager.get_working_dir()
    output = base_dir / inodo / "vanish_stash.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(chat_json, encoding="utf-8")

def load_vanish_stash(project_path: Union[str, Path]) -> Optional[str]:
    """Recupera la copia de seguridad del chat guardada por el modo vanish."""
    project_path = Path(project_path) if isinstance(project_path, str) else project_path
    inodo = generate_unique_id(project_path)
    base_dir = profile_manager.get_working_dir()
    input_path = base_dir / inodo / "vanish_stash.json"
    if not input_path.exists():
        return None
    return input_path.read_text(encoding="utf-8")

def clear_vanish_stash(project_path: Union[str, Path]):
    """Elimina el archivo de copia de seguridad del modo vanish."""
    project_path = Path(project_path) if isinstance(project_path, str) else project_path
    inodo = generate_unique_id(project_path)
    base_dir = profile_manager.get_working_dir()
    input_path = base_dir / inodo / "vanish_stash.json"
    if input_path.exists():
        input_path.unlink()

def get_context_tree(project_path: Union[str, Path], context_items: Optional[dict] = None) -> str:
    """Genera solo la representación visual del árbol (sin el contenido de los archivos)."""
    project_path = Path(project_path) if isinstance(project_path, str) else project_path

    # Si no hay enfoque específico, devolvemos el árbol de todo el proyecto
    if not context_items or (not context_items.get("files") and not context_items.get("folders")):
        custom_ignores = get_ignore_patterns(project_path, ".contextignore")
        summary, tree, content = gitingest.ingest(
            str(project_path), exclude_patterns=set(custom_ignores)
        )
        return tree

    # Si hay un enfoque específico, construimos el árbol manual
    custom_ignores = get_ignore_patterns(project_path, ".contextignore")
    final_tree = "Directory structure (Custom Focus):\n"

    files = context_items.get("files", [])
    if files:
        final_tree += "└── [Archivos Específicos Añadidos]\n"
        for idx, f_path in enumerate(files):
            prefix = "    └── " if idx == len(files) - 1 else "    ├── "
            final_tree += f"{prefix}{f_path}\n"

    folders = context_items.get("folders", [])
    if folders:
        final_tree += "└── [Carpetas Específicas Añadidas]\n"
        for folder in folders:
            real_folder = project_path / folder
            if real_folder.exists() and real_folder.is_dir():
                summary, tree, content = gitingest.ingest(
                    str(real_folder), exclude_patterns=set(custom_ignores)
                )
                # Indentamos para que cuadre visualmente
                indented_tree = "\n".join(f"    {line}" for line in tree.splitlines())
                final_tree += f"{indented_tree}\n"

    return final_tree


def extract_image_references_from_text(content: str) -> List[Tuple[str, bool]]:
    """
    Extrae referencias de imágenes desde una cadena de texto plano.
    Retorna una lista de tuplas: (nombre_o_ruta, es_wikilink)
    """
    results = []
    # Markdown estándar e HTML
    std_patterns = [
        r"!\[.*?\]\((.*?\.(?:png|jpg|jpeg|webp|gif))\)",
        r'<img\s+[^>]*src=["\'](.*?\.(?:png|jpg|jpeg|webp|gif))["\']',
    ]
    for pat in std_patterns:
        matches = re.findall(pat, content, re.IGNORECASE)
        results.extend(
            [(m.strip().lstrip("/"), False) for m in matches if not m.startswith(("http", "data:"))]
        )

    # WikiLinks (estilo Obsidian): ![[imagen.png|237]]
    wiki_matches = re.findall(r"!\[\[(.*?)(?:\|.*?)?\]\]", content)
    results.extend(
        [(m.strip(), True) for m in wiki_matches if not m.startswith(("http", "data:"))]
    )

    return list(dict.fromkeys(results))
