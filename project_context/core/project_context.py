import json
import logging
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Self

import gitingest
import typer
from filelock import FileLock, Timeout

from project_context.core.schemas import Context, ContextConfig, ProjectState
from project_context.ui import UI
from project_context.utils import compute_md5, human_to_int

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class ProjectContext:
    def __init__(self, email: str, project_path: Optional[Path] = None):
        self.email = email
        self.project_path = project_path or Path.cwd()

        self.local_dir = self.project_path / ".project_context"
        self.snapshots_dir = self.local_dir / "snapshots"
        self.objects_dir = self.snapshots_dir / "objects"
        self.context_store_dir = self.snapshots_dir / "context_store"

        self.identity_hash = compute_md5(email.strip().lower())
        self.state_path = self.local_dir / f"state_{self.identity_hash}.json"

        self._lock_path = self.local_dir / "app.lock"
        self._lock: Optional[FileLock] = None
        self.is_multiple_instances = bool(
            os.getenv("PROJECT_CONTEXT_MULTIPLE_INSTANCES")
        )

    @property
    def exists_folder_project(self) -> bool:
        return self.local_dir.exists()

    @property
    def context_config_path(self) -> Path:
        return self.local_dir / "context.json"

    def load_context_config(self) -> ContextConfig:
        """Carga de manera robusta la configuración unificada de contexto del proyecto."""
        if not self.context_config_path.exists():
            config = ContextConfig(folders=["."])
            self.save_context_config(config)
            return config
        try:
            data = json.loads(self.context_config_path.read_text(encoding="utf-8"))
            return ContextConfig(**data)
        except Exception as e:
            logger.debug(
                f"Error cargando context.json, usando valores por defecto: {e}"
            )
            config = ContextConfig(folders=["."])
            self.save_context_config(config)
            return config

    def save_context_config(self, config: ContextConfig):
        """Persiste la configuración unificada de contexto en disco."""
        self.context_config_path.parent.mkdir(parents=True, exist_ok=True)
        self.context_config_path.write_text(
            config.model_dump_json(indent=2), encoding="utf-8"
        )

    def _build_folders(self):
        confirm = typer.confirm(
            "Este directorio no ha sido inicializado como un proyecto de project_context.\n"
            "¿Deseas inicializar un nuevo contexto de proyecto en la ruta actual?",
            default=True,
        )
        if not confirm:
            UI.info("Operación cancelada.")
            raise typer.Exit()

        self.local_dir.mkdir(parents=True, exist_ok=True)
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self.objects_dir.mkdir(parents=True, exist_ok=True)

    def __enter__(self) -> Self:
        if not self.exists_folder_project:
            self._build_folders()

        if self.is_multiple_instances is False:
            self._lock = FileLock(self._lock_path, timeout=0)
            try:
                self._lock.acquire(timeout=0)
            except Timeout:
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

    def load_state(self) -> ProjectState:
        """Carga y valida el archivo state_<hash>.json del perfil activo."""
        if not self.state_path.exists():
            return ProjectState(state_path=self.state_path)

        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            return ProjectState(**data, state_path=self.state_path)
        except json.JSONDecodeError:
            return ProjectState(state_path=self.state_path)

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
            UI.warn(f"No se pudo escribir en el archivo .gitignore: {e}")

    def generate_context(self) -> Context:
        """
        Genera el prompt de contexto unificado.

        "El contexto siempre es el resultado de procesar una lista de elementos incluidos,
        aplicando sobre ellos la lista de exclusiones."
        """
        config = self.load_context_config()

        from project_context.utils import get_ignore_patterns

        custom_ignores = list(config.exclusions)
        gitignore_ignores = get_ignore_patterns(self.project_path, ".gitignore")
        all_ignores = set(custom_ignores + gitignore_ignores)

        # Caso 1: Enfoque Global (Análisis del proyecto completo)
        if config.folders == ["."]:
            summary, tree, content = gitingest.ingest(
                self.project_path.as_posix(), exclude_patterns=all_ignores
            )
            estimated_tokens = human_to_int(summary.split()[-1])
            text = tree + "\n\n" + content
            return Context(text=text, token_count=estimated_tokens)

        # Caso 2: Enfoque Específico (Archivos y subcarpetas enfocados)
        final_tree = "Directory structure (Focus):\n"
        final_content = ""
        total_tokens = 0

        # Procesamiento de archivos específicos
        files = config.files
        if files:
            final_tree += "└── [Archivos Específicos Añadidos]\n"
            for idx, f_path in enumerate(files):
                real_path = self.project_path / f_path

                # Omitir si coincide con alguna regla de exclusión
                from fnmatch import fnmatch

                if any(
                    fnmatch(str(f_path), pattern) or fnmatch(real_path.name, pattern)
                    for pattern in all_ignores
                ):
                    continue

                prefix = "    └── " if idx == len(files) - 1 else "    ├── "
                final_tree += f"{prefix}{f_path}\n"

                if real_path.exists() and real_path.is_file():
                    try:
                        text = real_path.read_text(encoding="utf-8")
                        final_content += (
                            f"{'=' * 48}\nFILE: {f_path}\n{'=' * 48}\n{text}\n\n"
                        )
                        total_tokens += len(text) // 4
                    except Exception as e:
                        final_content += f"{'=' * 48}\nFILE: {f_path}\n{'=' * 48}\n[Error leyendo archivo: {e}]\n\n"

        # Procesamiento de carpetas específicas
        folders = config.folders
        if folders:
            final_tree += "└── [Carpetas Específicas Añadidas]\n"
            for folder in folders:
                real_folder = self.project_path / folder
                if real_folder.exists() and real_folder.is_dir():
                    summary, tree, content = gitingest.ingest(
                        str(real_folder), exclude_patterns=all_ignores
                    )

                    indented_tree = "\n".join(
                        f"    {line}" for line in tree.splitlines()
                    )
                    final_tree += f"{indented_tree}\n"

                    # Post-procesado para asegurar que los marcadores de ruta de archivo
                    # devueltos por Gitingest sigan siendo relativos a la raíz del repositorio.
                    pattern = r"================================================\s*FILE:\s*([^\n]+)\s*================================================"

                    def replace_path(match):
                        rel_file_path = match.group(1)
                        full_rel_path = (Path(folder) / rel_file_path).as_posix()
                        return f"================================================\nFILE: {full_rel_path}\n================================================"

                    fixed_content = re.sub(pattern, replace_path, content)
                    final_content += f"{fixed_content}\n"
                    total_tokens += human_to_int(summary.split()[-1])

        full_context = final_tree + "\n" + final_content
        return Context(text=full_context, token_count=total_tokens)
