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
    """
    Calcula el hash MD5 de forma segura.
    Acepta un bloque de bytes en memoria, una ruta de archivo en formato de cadena o un objeto Path.
    """
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
    """
    Verifica y añade la regla de exclusión del directorio local a .gitignore.
    """
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
    Obtiene el diff de los archivos en STAGE.
    """
    try:
        repo = Repo(project_path, search_parent_directories=True)
        diff_text = repo.git.diff("--cached")

        if not diff_text.strip():
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
    """Busca carpetas que probablemente contengan imágenes."""
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
            [
                (m.strip().lstrip("/"), False)
                for m in matches
                if not m.startswith(("http", "data:"))
            ]
        )

    # WikiLinks (estilo Obsidian): ![[imagen.png|237]]
    wiki_matches = re.findall(r"!\[\[(.*?)(?:\|.*?)?\]\]", content)
    results.extend(
        [(m.strip(), True) for m in wiki_matches if not m.startswith(("http", "data:"))]
    )

    return list(dict.fromkeys(results))


def extract_image_references(file_path: Path) -> List[Tuple[str, bool]]:
    """
    Lee un archivo en disco y extrae sus referencias visuales asociadas.
    """
    if not file_path.exists():
        return []
    return extract_image_references_from_text(file_path.read_text(encoding="utf-8"))


def has_unstaged_changes(project_path: Path) -> bool:
    """Verifica si hay archivos modificados o untracked que no están en stage."""
    try:
        repo = Repo(project_path, search_parent_directories=True)
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


def _get_stash_path(project_path: Union[str, Path], filename: str) -> Path:
    return get_local_context_dir(project_path) / filename


def save_stash(project_path: Union[str, Path], filename: str, content: str):
    """Guarda un estado de respaldo en el directorio de metadatos local."""
    _get_stash_path(project_path, filename).write_text(content, encoding="utf-8")


def load_stash(project_path: Union[str, Path], filename: str) -> Optional[str]:
    """Carga un estado de respaldo si existe en el almacenamiento local."""
    path = _get_stash_path(project_path, filename)
    return path.read_text(encoding="utf-8") if path.exists() else None


def clear_stash(project_path: Union[str, Path], filename: str):
    """Elimina el archivo de respaldo temporal si existe."""
    path = _get_stash_path(project_path, filename)
    if path.exists():
        path.unlink()


def get_context_tree(
    project_path: Union[str, Path], context_items: Optional[dict] = None
) -> str:
    """Genera solo la representación visual del árbol."""
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


class ProfileManager:
    def __init__(self):
        self.root_dir = get_app_root_dir()
        self.profiles_dir = self.root_dir / "profiles"
        self.secrets_dir = self.root_dir / "secrets"
        self.tokens_dir = self.root_dir / "tokens"
        self.active_profile_file = (
            self.root_dir / "active_profile"
        )  # Puntero plano sin extensión
        self.config_file = (
            self.root_dir / "global_config.json"
        )  # Mantenido para migración
        self._temp_profile: Optional[str] = None
        self._ensure_structure()

    def _ensure_structure(self):
        """Crea la estructura base y migra datos antiguos si existen."""
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.profiles_dir.mkdir(exist_ok=True)
        self.secrets_dir.mkdir(exist_ok=True)
        self.tokens_dir.mkdir(exist_ok=True)

        # Migración ligera: Si existía un secreto en la raíz, moverlo al banco de secretos
        legacy_secret = self.root_dir / "client_secrets.json"
        target_secret = self.secrets_dir / "client_secrets.json"
        if legacy_secret.exists() and not target_secret.exists():
            try:
                shutil.copy2(str(legacy_secret), str(target_secret))
            except Exception as e:
                logger.warning(f"No se pudo migrar el secreto legacy: {e}")

        # Migración del config_file (global_config.json) anterior
        if self.config_file.exists():
            try:
                config = json.loads(self.config_file.read_text(encoding="utf-8"))
                old_profile = config.get("current_profile")
                if old_profile and old_profile != "default":
                    profile_file = self.profiles_dir / f"{old_profile}.json"
                    if profile_file.exists():
                        self.active_profile_file.write_text(
                            old_profile, encoding="utf-8"
                        )
                self.config_file.unlink()
            except Exception:
                pass

    def set_temporary_profile(self, profile_name: str):
        """Establece un perfil activo solo para la ejecución actual (en memoria)."""
        self._temp_profile = profile_name

    def get_active_profile_name(self) -> Optional[str]:
        if self._temp_profile:
            return self._temp_profile

        if not self.active_profile_file.exists():
            return None

        try:
            name = self.active_profile_file.read_text(encoding="utf-8").strip()
            if name:
                # Comprobación de existencia del archivo de configuración del perfil
                profile_file = self.profiles_dir / f"{name}.json"
                if profile_file.exists():
                    return name
                else:
                    # Limpieza automática si el perfil apuntado ha sido removido
                    try:
                        self.active_profile_file.unlink()
                    except Exception:
                        pass
        except Exception:
            pass
        return None

    def set_active_profile(self, profile_name: str):
        self._temp_profile = None

        profile_file = self.profiles_dir / f"{profile_name}.json"
        if not profile_file.exists():
            raise FileNotFoundError(
                f"El perfil '{profile_name}' no existe en el sistema."
            )

        self.active_profile_file.write_text(profile_name, encoding="utf-8")

    def get_working_dir(self) -> Path:
        """
        Retorna la raíz del perfil global para almacenamiento temporal heredado.
        """
        return self.root_dir

    def list_profiles(self) -> list[str]:
        """Lista los aliases de perfiles (nombres de archivos .json sin extensión)."""
        return [f.stem for f in self.profiles_dir.glob("*.json")]

    def load_profile_data(self, profile_name: str) -> dict:
        profile_file = self.profiles_dir / f"{profile_name}.json"
        if not profile_file.exists():
            return {}
        try:
            return json.loads(profile_file.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def save_profile_data(self, profile_name: str, data: dict):
        profile_file = self.profiles_dir / f"{profile_name}.json"
        profile_file.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def get_active_profile_data(self) -> dict:
        name = self.get_active_profile_name()
        if not name:
            return {}
        return self.load_profile_data(name)

    def save_active_profile_data(self, data: dict):
        name = self.get_active_profile_name()
        if not name:
            raise ValueError("No hay ningún perfil activo configurado.")
        self.save_profile_data(name, data)

    def resolve_secrets_file(self) -> Tuple[Path, str]:
        """
        Resuelve el secreto asociado al perfil activo aplicando prioridades.
        """
        profile_name = self.get_active_profile_name()
        if not profile_name:
            raise ValueError("No hay ningún perfil activo configurado.")

        profile_data = self.get_active_profile_data()
        secret_name = profile_data.get("associated_secret")

        available_secrets = sorted(
            [f for f in self.secrets_dir.glob("*.json") if f.is_file()]
        )

        if secret_name:
            if not secret_name.endswith(".json"):
                secret_name += ".json"

            specific_path = self.secrets_dir / secret_name
            if specific_path.exists():
                return specific_path, f"Asociado al perfil ({secret_name})"

        if len(available_secrets) == 1:
            auto_secret = available_secrets[0]
            profile_data["associated_secret"] = auto_secret.name
            self.save_profile_data(profile_name, profile_data)

            UI.info(
                f"Auto-asociando el único secreto disponible: [bold]{auto_secret.name}[/]"
            )
            return auto_secret, f"Auto-detectado ({auto_secret.name})"

        elif len(available_secrets) > 1:
            secret_names = [f.name for f in available_secrets]
            raise ValueError(
                f"Conflicto de credenciales: Se detectaron {len(available_secrets)} secretos en el almacén "
                f"({', '.join(secret_names)}) pero el perfil '{profile_name}' no tiene un secreto asociado.\n"
                f"Para solucionarlo, asocia uno explícitamente ejecutando:\n"
                f"  project_context profile set-secrets <ruta_archivo.json> --secret-name <nombre_deseado>\n"
                f"O bien cambia a un perfil que ya esté configurado."
            )

        else:
            fallback_name = secret_name if secret_name else f"{profile_name}.json"
            if not fallback_name.endswith(".json"):
                fallback_name += ".json"
            return self.secrets_dir / fallback_name, "Predeterminado (Faltante)"

    def get_secrets_association_map(self) -> dict:
        association_map = {}

        if self.secrets_dir.exists():
            for file in self.secrets_dir.glob("*.json"):
                association_map[file.name] = {
                    "path": file,
                    "associated_profiles": [],
                    "exists_on_disk": True,
                }

        profiles = self.list_profiles()
        for profile_name in profiles:
            profile_data = self.load_profile_data(profile_name)
            secret_name = profile_data.get("associated_secret")

            if secret_name:
                if not secret_name.endswith(".json"):
                    secret_name += ".json"

                if secret_name not in association_map:
                    association_map[secret_name] = {
                        "path": self.secrets_dir / secret_name,
                        "associated_profiles": [],
                        "exists_on_disk": False,
                    }

                association_map[secret_name]["associated_profiles"].append(profile_name)

        return association_map

    def remove_tokens_for_secret(self, secret_name: str) -> int:
        if not secret_name.endswith(".json"):
            secret_name += ".json"

        removed_count = 0
        if self.tokens_dir.exists():
            for token_file in self.tokens_dir.iterdir():
                if token_file.is_file() and token_file.name.endswith(
                    f"__{secret_name}"
                ):
                    try:
                        token_file.unlink()
                        removed_count += 1
                    except Exception as e:
                        logger.warning(
                            f"No se pudo limpiar el token residual '{token_file.name}': {e}"
                        )
        return removed_count


profile_manager = ProfileManager()


def verify_profile_credentials(profile_name: Optional[str]) -> None:
    """
    Realiza una comprobación estática pre-flight del perfil y sus credenciales.
    Implementa el flujo lógico de validación interactiva y resolución del perfil.
    """
    import json

    from google.oauth2.credentials import Credentials

    from project_context.exceptions import (
        AssociatedSecretMissingError,
        FreshInstallRequiredError,
        ProfileConfigNotFoundError,
        ProfileConfigurationCorruptError,
    )

    available_profiles = profile_manager.list_profiles()
    available_secrets = sorted(
        [f for f in profile_manager.secrets_dir.glob("*.json") if f.is_file()]
    )

    if profile_name:
        if profile_name not in available_profiles:
            raise ProfileConfigNotFoundError(
                f"El perfil de usuario '{profile_name}' no existe en este equipo."
            )
    else:
        active_profile = profile_manager.get_active_profile_name()
        if not active_profile:
            if not available_profiles and not available_secrets:
                raise FreshInstallRequiredError(
                    "No se han detectado perfiles ni credenciales de Google Drive configuradas."
                )
            else:
                raise ProfileConfigNotFoundError(
                    "No hay ningún perfil de usuario activo configurado actualmente."
                )
        profile_name = active_profile

    # --- BLOQUE: Trabajar con perfil resuelto ---
    profile_file = profile_manager.profiles_dir / f"{profile_name}.json"
    try:
        profile_data = json.loads(profile_file.read_text(encoding="utf-8"))
    except Exception as e:
        raise ProfileConfigurationCorruptError(
            f"El archivo del perfil '{profile_name}' está dañado o corrupto: {e}"
        )

    email = profile_data.get("email")
    secret_name = profile_data.get("associated_secret")

    # --- ROMBO: ¿Existe token de session? y ¿Se puede usar o refrescar? ---
    # (Evaluación perezosa estática para evitar requerir el secreto físico si el token sirve)
    token_valid_or_refreshable = False
    if email and secret_name:
        associated_secret_clean = (
            secret_name if secret_name.endswith(".json") else f"{secret_name}.json"
        )
        token_name = f"{email}__{associated_secret_clean}"
        token_path = profile_manager.tokens_dir / token_name

        if token_path.exists():
            try:
                creds = Credentials.from_authorized_user_file(str(token_path))
                if creds.valid or (creds.expired and creds.refresh_token):
                    token_valid_or_refreshable = True
            except Exception:
                token_valid_or_refreshable = False

    # Si el token es utilizable, finaliza la comprobación pre-flight con éxito
    if token_valid_or_refreshable:
        return

    # --- ROMBO: ¿Tiene secreto asociado? ---
    if not secret_name:
        raise AssociatedSecretMissingError(
            f"El perfil '{profile_name}' requiere re-autenticarse, pero no tiene un archivo de secretos asociado."
        )

    # --- ROMBO: ¿Existe el secreto? ---
    associated_secret_clean = (
        secret_name if secret_name.endswith(".json") else f"{secret_name}.json"
    )
    associated_secret_path = profile_manager.secrets_dir / associated_secret_clean
    if not associated_secret_path.exists():
        raise AssociatedSecretMissingError(
            f"El perfil '{profile_name}' requiere re-autenticarse, pero su secreto asociado "
            f"'{associated_secret_clean}' no se encuentra físicamente en el disco."
        )


def safe_verify_profile(profile_name: Optional[str]) -> None:
    """
    Captura los fallos de validación del pre-flight y asiste al usuario
    con sugerencias interactivas y enlistado de opciones.
    """
    import typer

    from project_context.exceptions import (
        AssociatedSecretMissingError,
        FreshInstallRequiredError,
        ProfileConfigNotFoundError,
        ProfileConfigurationCorruptError,
    )

    try:
        verify_profile_credentials(profile_name)
    except FreshInstallRequiredError:
        UI.error(
            "No se ha detectado ninguna credencial de Google Drive en este equipo.",
            spacing="top",
        )
        UI.educational_tip(
            title="Configuración Inicial de Credenciales",
            message=(
                "Para conectar project_context con tu cuenta de Google Drive necesitas un secreto cliente OAuth:\n\n"
                "1. Accede a la Google Cloud Console (https://console.cloud.google.com/).\n"
                "2. Habilita la Google Drive API en tu proyecto.\n"
                "3. Crea credenciales de tipo 'OAuth Client ID' (Desktop App).\n"
                "4. Descarga el archivo JSON resultante."
            ),
            commands=["project_context secrets add /ruta/a/tus_credenciales.json"],
            spacing="bottom",
        )
        raise typer.Exit(code=1)

    except ProfileConfigNotFoundError as e:
        available_profiles = profile_manager.list_profiles()
        if not profile_name:
            UI.error(str(e), spacing="top")
            if available_profiles:
                UI.educational_tip(
                    title="Selecciona un Perfil Activo",
                    message=(
                        f"Perfiles ya configurados en este equipo: {', '.join(available_profiles)}\n\n"
                        "Para activar uno de estos perfiles, ejecuta:"
                    ),
                    commands=["project_context profile use <nombre_perfil>"],
                    spacing="bottom",
                )
            else:
                UI.educational_tip(
                    title="Crea tu primer perfil",
                    message=(
                        "No se han detectado perfiles creados en este sistema.\n"
                        "Si ya has guardado tu archivo de secretos, puedes crear un perfil ejecutando:"
                    ),
                    commands=["project_context profile add <nombre_perfil>"],
                    spacing="bottom",
                )
        else:
            # --- BLOQUE: ERROR: No existe el perfil especificado + Enlistar perfiles ---
            UI.error(
                f"ERROR: El perfil especificado '{profile_name}' no existe.",
                spacing="top",
            )
            if available_profiles:
                UI.educational_tip(
                    title="Perfiles de Usuario Disponibles",
                    message=(
                        f"Perfiles configurados en este equipo: {', '.join(available_profiles)}\n\n"
                        f"Si deseas cambiar a uno de ellos, ejecuta:"
                    ),
                    commands=["project_context profile use <nombre_perfil>"],
                    spacing="bottom",
                )
            else:
                UI.educational_tip(
                    title="Crea el perfil especificado",
                    message=(
                        f"No hay perfiles registrados. Si deseas crear el perfil '{profile_name}' "
                        "y asociarlo a tus credenciales, ejecuta:"
                    ),
                    commands=[f"project_context profile add {profile_name}"],
                    spacing="bottom",
                )
        raise typer.Exit(code=1)

    except AssociatedSecretMissingError as e:
        resolved_profile = (
            profile_name or profile_manager.get_active_profile_name() or "default"
        )
        profile_data = profile_manager.load_profile_data(resolved_profile)
        secret_name = profile_data.get("associated_secret", f"{resolved_profile}.json")
        if not secret_name.endswith(".json"):
            secret_name += ".json"

        UI.error(str(e), spacing="top")
        UI.educational_tip(
            title="Vincular Secreto Requerido",
            message=(
                f"La sesión para el perfil '{resolved_profile}' requiere iniciar el flujo OAuth en el navegador, "
                f"pero se necesita el archivo de secretos físicos '{secret_name}' en el banco global."
            ),
            commands=[
                f"project_context secrets add /ruta/a/tu/archivo.json --name {secret_name.replace('.json', '')}"
            ],
            spacing="bottom",
        )
        raise typer.Exit(code=1)

    except ProfileConfigurationCorruptError as e:
        UI.error(str(e), spacing="top")
        if profile_name:
            UI.educational_tip(
                title="Perfil Corrupto",
                message=(
                    "La estructura del archivo del perfil no es válida. Puedes restablecerlo "
                    "creándolo nuevamente con el comando de adición de perfiles."
                ),
                commands=[f"project_context profile add {profile_name}"],
                spacing="bottom",
            )
        raise typer.Exit(code=1)


def validate_google_secrets_file(path: Path) -> bool:
    """
    Verifica si el archivo en la ruta provista existe, es un JSON válido
    y posee la estructura típica de un archivo de secretos de Google OAuth.
    """
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
