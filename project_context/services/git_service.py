from pathlib import Path
from typing import Optional

from git import Repo, exc

from project_context.ui import UI


class GitService:
    """Servicio encargado de interactuar con el repositorio Git local."""

    def __init__(self, project_path: Path):
        self.project_path = project_path
        self._repo: Optional[Repo] = None

    @property
    def repo(self) -> Repo:
        """Inicializa y retorna el repositorio de forma perezosa (lazy)."""
        if self._repo is None:
            try:
                self._repo = Repo(self.project_path, search_parent_directories=True)
            except exc.InvalidGitRepositoryError:
                UI.error("El directorio actual no es un repositorio Git válido.")
                raise
        return self._repo

    def get_diff_message(self) -> Optional[str]:
        """Obtiene el diff de los archivos que están en stage (staged/cached)."""
        try:
            diff_text = self.repo.git.diff("--cached")

            if not diff_text.strip():
                if not self.repo.head.is_valid():
                    status = self.repo.git.status("--short")
                    if status:
                        return f"Initial commit. Files added:\n{status}"
                return None

            return diff_text

        except Exception as e:
            UI.error(f"Error obteniendo git diff: {e}")
            return None

    def get_diff_cached(self) -> Optional[str]:
        """Retorna el diff en stage o None si está vacío."""
        try:
            diff = self.repo.git.diff("--cached")
            return diff.strip() or None
        except Exception as e:
            UI.error(f"Error al obtener cambios en stage: {e}")
            return None

    def has_unstaged_changes(self) -> bool:
        """Verifica si hay archivos modificados o untracked que no están en stage."""
        try:
            if self.repo.untracked_files or self.repo.index.diff(None):
                return True
            return False
        except exc.InvalidGitRepositoryError:
            return False

    def stage_all_changes(self):
        """Agrega todas las modificaciones locales al stage (git add .)."""
        try:
            self.repo.git.add(A=True)
        except Exception as e:
            UI.error(f"Error al hacer git add: {e}")
