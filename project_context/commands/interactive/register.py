from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from project_context.core.project_context import ProjectContext
from project_context.core.snapshot_mg import SnapshotManager
from project_context.services.api_drive import GoogleDriveManager
from project_context.ui import UI


@dataclass
class SessionContext:
    """Contenedor explícito de dependencias para la sesión interactiva."""

    api: GoogleDriveManager
    project_context: ProjectContext

    _snapshot_manager: Optional[SnapshotManager] = None

    @property
    def snapshot_manager(self):
        """Inicializa de forma perezosa y con caché el SnapshotManager."""
        if self._snapshot_manager is None:
            self._snapshot_manager = SnapshotManager(self.api, self.project_context)
            self._snapshot_manager.initialize_schema()

        return self._snapshot_manager


class CommandMetadata:
    """Metadatos legibles para cada comando registrado."""

    def __init__(
        self,
        handler: Callable[[SessionContext, List[str]], Optional[bool]],
        description: str,
        require_chat: bool = True,
    ):
        self.handler = handler
        self.description = description
        self.require_chat = require_chat


class InteractiveRegistry:
    """Registro explícito de comandos para evitar importaciones implícitas."""

    def __init__(self):
        self._commands: Dict[str, CommandMetadata] = {}

    def register(
        self,
        names: List[str],
        handler: Callable[[SessionContext, List[str]], Optional[bool]],
        description: str,
        require_chat: bool = True,
    ):
        """Asocia explícitamente uno o más alias a un manejador de comandos."""
        # TODO: reutilizar los docstring especifica en los subcomandos.
        metadata = CommandMetadata(handler, description, require_chat)
        for name in names:
            self._commands[name.lower()] = metadata

    def get_commands_map(self) -> Dict[str, CommandMetadata]:
        return self._commands

    def execute(
        self, name: str, ctx: SessionContext, args: List[str]
    ) -> Optional[bool]:
        cmd_name = name.lower()
        if cmd_name not in self._commands:
            UI.error(
                f"Comando desconocido: '{name}'. Escribe 'help' para ver la lista."
            )
            return True

        metadata = self._commands[cmd_name]

        state = ctx.project_context.load_state()
        if metadata.require_chat and not state.chat_id:
            UI.error(
                "No se encontró una sesión de chat activa para ejecutar este comando."
            )
            return True

        return metadata.handler(ctx, args)
