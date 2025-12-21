import json
import os
import shutil
import sys
from fnmatch import fnmatch
from pathlib import Path
from typing import List, Optional, Tuple, Union, cast

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
        try:
            config = json.loads(self.config_file.read_text())
            return config.get("current_profile", "default")
        except:
            return "default"

    def set_active_profile(self, profile_name: str):
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
            for i in path_file.read_text(encoding="utf-8").splitlines()
            if i.strip() and not i.strip().startswith("#")
        ]
    return []


def generate_context(project_path: Union[str, Path]) -> tuple[str, int]:
    project_path = Path(project_path) if isinstance(project_path, str) else project_path

    custom_ignores = get_ignore_patterns(project_path, ".contextignore")

    summary, tree, content = ingest(
        str(project_path), exclude_patterns=set(custom_ignores)
    )

    estimated_tokens = human_to_int(summary.split()[-1])
    context = tree + "\n\n" + content
    return context, estimated_tokens


def save_context(project_path: Union[str, Path], context: str) -> Path:
    project_path = Path(project_path) if isinstance(project_path, str) else project_path
    inodo = generate_unique_id(project_path)

    # USAMOS EL DIRECTORIO DEL PERFIL
    base_dir = profile_manager.get_working_dir()
    output = base_dir / inodo / f"project_context.txt"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(context, encoding="utf-8")
    return output


def save_project_context_state(
    project_path: Union[str, Path], project_context_state: dict
):
    project_path = Path(project_path) if isinstance(project_path, str) else project_path
    inodo = generate_unique_id(project_path)

    base_dir = profile_manager.get_working_dir()
    output = base_dir / inodo / f"project_context_state.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(project_context_state), encoding="utf-8")


def load_project_context_state(project_path: Union[str, Path]) -> Optional[dict]:
    """Devuelve un diccionario con el estado del proyecto."""
    project_path = Path(project_path) if isinstance(project_path, str) else project_path
    # TODO: Cambiar inodo por otra forma de identificar el proyecto.
    inodo = generate_unique_id(project_path)

    base_dir = profile_manager.get_working_dir()
    input_path = base_dir / inodo / f"project_context_state.json"
    if not input_path.exists():
        return None
    content = input_path.read_text(encoding="utf-8")
    return json.loads(content)


def has_files_modified_since(st_mtime: float, folder: Path, gitignore=True) -> bool:
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
        if gitignore is True:
            if any(fnmatch(ruta_rel, patron) for patron in ignore):  # type: ignore
                continue
        fecha_mod = file.stat().st_mtime
        if fecha_mod > st_mtime:
            return True
    return False
