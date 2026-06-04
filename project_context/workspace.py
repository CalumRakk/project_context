import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Self

import typer
from filelock import FileLock, Timeout

from project_context.exceptions import StateNotFoundError

if TYPE_CHECKING:
    from project_context.schema import LocalContextItems, ProjectState

logger = logging.getLogger(__name__)


class ProjectContext:
    def __init__(self, project_path: Optional[Path] = None):
        self.project_path = project_path or Path.cwd()
        self.local_dir = self.project_path / ".project_context"
        self._state_path = self.local_dir / "state.json"
        self._lock_path = self.local_dir / "app.lock"

        self.snapshots_dir = self.local_dir / "snapshots"
        self.objects_dir = self.snapshots_dir / "objects"
        self.context_store_dir = self.snapshots_dir / "context_store"

        self._lock: Optional[FileLock] = None
        self._state: Optional["ProjectState"] = None

        self.is_multiple_instances = bool(
            os.getenv("PROJECT_CONTEXT_MULTIPLE_INSTANCES")
        )

    @property
    def is_initialized(self) -> bool:
        return self.local_dir.exists()

    def _initialize(self):
        confirm = typer.confirm(
            "Este directorio no ha sido inicializado como un proyecto de project_context.\n"
            "¿Deseas inicializar un nuevo contexto de proyecto en la ruta actual?",
            default=True,
        )
        if not confirm:
            from project_context.ui.ui import UI

            UI.info("Operación cancelada.")
            raise typer.Exit()

        self.local_dir.mkdir(parents=True, exist_ok=True)
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self.objects_dir.mkdir(parents=True, exist_ok=True)

    def __enter__(self) -> Self:
        if not self.is_initialized:
            self._initialize()

        if self.is_multiple_instances is False:
            self._lock = FileLock(self._lock_path, timeout=0)
            try:
                self._lock.acquire(timeout=0)
            except Timeout:
                from project_context.ui.ui import UI

                UI.error(
                    "Ya existe una instancia de project_context operando activamente en este proyecto.\n"
                    "Por favor, cierra la sesión abierta en la otra terminal antes de iniciar una nueva."
                )
                raise typer.Exit(code=1)

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._lock and self.is_multiple_instances is False:
            try:
                self._lock.release()
            except Exception as e:
                logger.debug(f"Error liberando el archivo de bloqueo: {e}")

    # --- GESTIÓN DE ESTADO TRANSPARENTE ---

    def _ensure_state_loaded(self):
        if self._state is None:
            try:
                self._state = self.load_project_context_state()
            except StateNotFoundError:
                from project_context.schema import LocalContextItems, ProjectState

                self._state = ProjectState(
                    chat_id="",
                    file_id="",
                    md5="",
                    last_modified=0.0,
                    context_items=LocalContextItems(),
                )

    def save(self):
        """Guarda explícitamente el estado actual en el disco."""
        if self._state is not None:
            self.save_project_context_state(self._state)

    @property
    def chat_id(self) -> str:
        self._ensure_state_loaded()
        return self._state.chat_id if self._state else ""

    @chat_id.setter
    def chat_id(self, value: str):
        self._ensure_state_loaded()
        if self._state:
            self._state.chat_id = value
            self.save()

    @property
    def file_id(self) -> str:
        self._ensure_state_loaded()
        return self._state.file_id if self._state else ""

    @file_id.setter
    def file_id(self, value: str):
        self._ensure_state_loaded()
        if self._state:
            self._state.file_id = value
            self.save()

    @property
    def md5(self) -> str:
        self._ensure_state_loaded()
        if self._state:
            return getattr(self._state, "file_md5", getattr(self._state, "md5", ""))
        return ""

    @md5.setter
    def md5(self, value: str):
        self._ensure_state_loaded()
        if self._state:
            self._state.file_md5 = value
            if hasattr(self._state, "md5"):
                setattr(self._state, "md5", value)
            self.save()

    @property
    def last_modified(self) -> float:
        self._ensure_state_loaded()
        return self._state.last_modified if self._state else 0.0

    @last_modified.setter
    def last_modified(self, value: float):
        self._ensure_state_loaded()
        if self._state:
            self._state.last_modified = value
            self.save()

    @property
    def context_items(self) -> "LocalContextItems":
        self._ensure_state_loaded()
        from project_context.schema import LocalContextItems

        if self._state and hasattr(self._state, "context_items"):
            return self._state.context_items
        return LocalContextItems()

    @context_items.setter
    def context_items(self, value: "LocalContextItems"):
        self._ensure_state_loaded()
        if self._state:
            self._state.context_items = value
            self.save()

    @property
    def story_mode(self) -> bool:
        self._ensure_state_loaded()
        return getattr(self._state, "story_mode", False) if self._state else False

    @story_mode.setter
    def story_mode(self, value: bool):
        self._ensure_state_loaded()
        if self._state:
            setattr(self._state, "story_mode", value)
            self.save()

    @property
    def story_anchor(self) -> Optional[str]:
        self._ensure_state_loaded()
        return getattr(self._state, "story_anchor", None) if self._state else None

    @story_anchor.setter
    def story_anchor(self, value: Optional[str]):
        self._ensure_state_loaded()
        if self._state:
            setattr(self._state, "story_anchor", value)
            self.save()

    @property
    def vanished(self) -> bool:
        self._ensure_state_loaded()
        return getattr(self._state, "vanished", False) if self._state else False

    @vanished.setter
    def vanished(self, value: bool):
        self._ensure_state_loaded()
        if self._state:
            setattr(self._state, "vanished", value)
            self.save()

    @property
    def commit_mode(self) -> bool:
        self._ensure_state_loaded()
        return getattr(self._state, "commit_mode", False) if self._state else False

    @commit_mode.setter
    def commit_mode(self, value: bool):
        self._ensure_state_loaded()
        if self._state:
            setattr(self._state, "commit_mode", value)
            self.save()

    # --- OPERACIONES DE PERSISTENCIA ---

    def save_context(self, context: str) -> Path:
        """Guarda el contexto consolidado en last_context.txt."""
        output = self.local_dir / "last_context.txt"
        output.write_text(context, encoding="utf-8")
        return output

    def save_project_context_state(self, state_data: "ProjectState"):
        """Guarda el estado del proyecto serializado desde el modelo Pydantic."""
        content = state_data.model_dump_json(indent=2, by_alias=True)
        self._state_path.write_text(content, encoding="utf-8")
        self.ensure_gitignore(state_data.model_dump())

    def load_project_context_state(self) -> "ProjectState":
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
