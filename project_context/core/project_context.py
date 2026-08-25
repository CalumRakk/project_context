import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Self

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

    def check_file_override(self, rel_path: str) -> Optional[str]:
        """Comprueba si una ruta de archivo relativa anula un exclude o un .gitignore."""
        config = self.load_context_config()
        from project_context.utils import diagnose_file_override

        return diagnose_file_override(self.project_path, rel_path, config.exclusions)

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

    def validate_external_folder(self, alias: str, target_path: Path) -> None:
        """Valida que una ruta externa cumpla con las reglas de negocio antes de vincularse (link):

        1. El directorio debe existir físicamente y ser un directorio.
        2. No puede estar dentro de la estructura local del proyecto.
        3. El alias no colisiona con archivos/carpetas en la raíz del proyecto.
        4. El alias no colisiona con otros alias ya registrados.
        5. No puede solaparse ni anidarse con otras rutas externas ya vinculadas.
        """
        abs_target = target_path.resolve()
        abs_project = self.project_path.resolve()

        if not abs_target.exists() or not abs_target.is_dir():
            raise ValueError(
                f"La ruta '{target_path}' no existe o no es un directorio válido en el disco."
            )

        # Regla 2: No puede estar dentro de la estructura local del proyecto
        if abs_target == abs_project or abs_project in abs_target.parents:
            raise ValueError(
                "La ruta especificada ya se encuentra dentro de tu proyecto local.\n"
                "Para enfocar carpetas o archivos locales utiliza 'context set' o 'context add'."
            )

        # Regla 3: El alias no colisiona con archivos/carpetas en la raíz local
        local_root_items = {
            item.name.lower()
            for item in self.project_path.iterdir()
            if item.name != ".project_context"
        }
        if alias.lower() in local_root_items:
            raise ValueError(
                f"El alias '{alias}' colisiona con un archivo o carpeta existente en la raíz local de tu proyecto.\n"
                "Elige un nombre alternativo para este enlace externo."
            )

        # Regla 4: Colisión con alias ya registrados
        config = self.load_context_config()
        external_folders = getattr(config, "external_folders", {})
        if alias in external_folders:
            raise ValueError(
                f"El alias '{alias}' ya está registrado. Usa 'context unlink {alias}' primero si deseas reasignarlo."
            )

        # Regla 5: No puede solaparse ni anidarse con otras rutas externas
        for ext_alias, ext_path_str in external_folders.items():
            abs_ext = Path(ext_path_str).resolve()
            if abs_target == abs_ext:
                raise ValueError(
                    f"Esta ruta ya está vinculada como paquete externo bajo el alias '{ext_alias}'."
                )
            if abs_ext in abs_target.parents:
                raise ValueError(
                    f"La ruta se solapa por estar contenida dentro del paquete externo '{ext_alias}' ({ext_path_str})."
                )
            if abs_target in abs_ext.parents:
                raise ValueError(
                    f"La ruta se solapa al contener el paquete externo ya registrado '{ext_alias}' ({ext_path_str})."
                )

    def generate_context(self) -> Context:
        """Genera el prompt de contexto unificado aplicando el principio de cortocircuito

        y la precedencia de reglas:
        Archivo Explícito (files) > Exclusiones (exclusions) > Área de Trabajo (folders) > .gitignore.
        """
        config = self.load_context_config()

        from project_context.utils import get_ignore_patterns

        custom_ignores = list(config.exclusions)
        gitignore_ignores = get_ignore_patterns(self.project_path, ".gitignore")
        all_ignores = set(custom_ignores + gitignore_ignores)

        import uuid

        from gitingest.ingestion import ingest_query
        from gitingest.schemas.ingestion import IngestionQuery, VirtualTarget

        targets = []

        # ARCHIVOS FORZADOS / WHITELIST (Máxima prioridad: cortocircuitan .gitignore y exclusions)
        for f_path in config.files:
            real_path = self.project_path / f_path
            if real_path.exists() and real_path.is_file():
                targets.append(
                    VirtualTarget(
                        physical_path=real_path,
                        virtual_alias=".",
                        is_file=True,
                    )
                )

        # ÁREA DE TRABAJO LOCAL (Aplica .gitignore y exclusions)
        if config.folders == ["."]:
            targets.append(
                VirtualTarget(
                    physical_path=self.project_path,
                    virtual_alias=".",
                    is_file=False,
                )
            )
        else:
            for folder in config.folders:
                real_folder = self.project_path / folder
                if real_folder.exists() and real_folder.is_dir():
                    targets.append(
                        VirtualTarget(
                            physical_path=real_folder,
                            virtual_alias=".",
                            is_file=False,
                        )
                    )

        # PAQUETES EXTERNOS (link)
        external_folders = getattr(config, "external_folders", {})
        for alias, ext_path_str in external_folders.items():
            real_folder = Path(ext_path_str)
            if real_folder.exists() and real_folder.is_dir():
                targets.append(
                    VirtualTarget(
                        physical_path=real_folder,
                        virtual_alias=alias,
                        is_file=False,
                    )
                )

        # Construimos la query de ingesta
        query = IngestionQuery(
            local_path=self.project_path,
            slug=self.project_path.name,
            id=uuid.uuid4(),
            max_file_size=10 * 1024 * 1024,
            ignore_patterns=all_ignores,
            targets=targets,
        )

        summary, tree, content = ingest_query(query)
        full_context = tree + "\n" + content
        estimated_tokens = human_to_int(summary.split()[-1])

        return Context(text=full_context, token_count=estimated_tokens)
