from pathlib import Path
from typing import Optional
from git import Repo, exc
from project_context.ui.ui import UI


def get_diff_message(project_path: Path) -> Optional[str]:
    """Obtiene el diff de los archivos en STAGE."""
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
        UI.error("El directorio actual no es un repositorio Git válido.")
        return None
    except Exception as e:
        UI.error(f"Error obteniendo git diff: {e}")
        return None


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
