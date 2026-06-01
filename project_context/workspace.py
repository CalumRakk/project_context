import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Self

import typer
from filelock import FileLock, Timeout

from project_context.exceptions import StateNotFoundError

if TYPE_CHECKING:
    from project_context.schema import ProjectState

logger = logging.getLogger(__name__)


class ProjectContext:
    """
    Workspace Manager encargado de la persistencia local del proyecto,
    control de concurrencia y operaciones sobre archivos de metadatos locales.
    """

    def __init__(self, project_path: Optional[Path] = None):
        self.project_path = project_path or Path.cwd()
        self.local_dir = self.project_path / ".project_context"
        self._state_path = self.local_dir / "state.json"
        self.local_dir.mkdir(parents=True, exist_ok=True)
        self._lock_path = self.local_dir / "app.lock"
        self._lock: Optional[FileLock] = None

    @property
    def is_initialized(self) -> bool:
        """Determina si el proyecto local tiene un estado inicializado."""
        return self._state_path.exists() and self._state_path.is_file()

    def __enter__(self) -> Self:
        if not self.is_initialized:
            confirm = typer.confirm(
                "Este directorio no ha sido inicializado como un proyecto de project_context.\n"
                "¿Deseas inicializar un nuevo contexto de proyecto en la ruta actual?",
                default=True,
            )
            if not confirm:
                from project_context.ui.ui import UI

                UI.info("Operación cancelada.")
                raise typer.Exit()

        self._lock = FileLock(self._lock_path, timeout=0)
        try:
            self._lock.acquire(timeout=0)
            return self
        except Timeout:
            from project_context.ui.ui import UI

            UI.error(
                "Ya existe una instancia de project_context operando activamente en este proyecto.\n"
                "Por favor, cierra la sesión abierta en la otra terminal antes de iniciar una nueva."
            )
            raise typer.Exit(code=1)

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._lock:
            try:
                self._lock.release()
            except Exception as e:
                logger.debug(f"Error liberando el archivo de bloqueo: {e}")

    def save_context(self, context: str) -> Path:
        """Guarda el contexto consolidado en last_context.txt."""
        output = self.local_dir / "last_context.txt"
        output.write_text(context, encoding="utf-8")
        return output

    def save_project_context_state(self, state_data: ProjectState):
        """Guarda el estado del proyecto serializado desde el modelo Pydantic."""
        content = state_data.model_dump_json(indent=2, by_alias=True)
        self._state_path.write_text(content, encoding="utf-8")
        self.ensure_gitignore(state_data.model_dump())

    def load_project_context_state(self):
        """Carga y valida el archivo state.json convirtiéndolo en un modelo Pydantic."""
        from project_context.schema import ProjectState

        if not self._state_path.exists():
            raise StateNotFoundError("El archivo state.json no existe.")
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            return ProjectState(**data)
        except json.JSONDecodeError as e:
            raise StateNotFoundError(
                "El archivo state.json no contiene una estructura JSON legible."
            ) from e

    def _get_stash_path(self, filename: str) -> Path:
        return self.local_dir / filename

    def save_stash(self, filename: str, content: str):
        """Almacena una copia de seguridad en memoria en el subdirectorio local."""
        self._get_stash_path(filename).write_text(content, encoding="utf-8")

    def load_stash(self, filename: str) -> Optional[str]:
        """Recupera el contenido de un respaldo local si existe."""
        path = self._get_stash_path(filename)
        return path.read_text(encoding="utf-8") if path.exists() else None

    def clear_stash(self, filename: str):
        """Elimina físicamente un archivo de respaldo local."""
        path = self._get_stash_path(filename)
        if path.exists():
            path.unlink()

    def ensure_gitignore(self, state_dict: Optional[dict] = None):
        """Verifica y añade la regla de exclusión del directorio local a .gitignore."""
        if state_dict and state_dict.get("auto_gitignore") is False:
            return

        gitignore_path = self.project_path / ".gitignore"
        rule = ".project_context/"
        try:
            content = ""
            if gitignore_path.exists():
                content = gitignore_path.read_text(encoding="utf-8")

            lines = [line.strip() for line in content.splitlines()]
            if any(line == rule or line == ".project_context" for line in lines):
                return

            from project_context.ui.ui import UI

            UI.info("Añadiendo '.project_context/' a .gitignore...")
            suffix = "\n" if content and not content.endswith("\n") else ""
            new_content = (
                content
                + suffix
                + f"\n# Metadatos locales de project-context-cli\n{rule}\n"
            )
            gitignore_path.write_text(new_content, encoding="utf-8")
            UI.success(".gitignore actualizado automáticamente.")
        except Exception as e:
            from project_context.ui.ui import UI

            UI.warn(f"No se pudo escribir en el archivo .gitignore: {e}")
