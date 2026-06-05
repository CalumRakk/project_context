from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

from project_context.services.api_drive import GoogleDriveManager
from project_context.workspace import ProjectContext


@dataclass
class SessionContext:
    """Contenedor explícito de dependencias para la sesión interactiva."""

    api: GoogleDriveManager
    workspace: ProjectContext

    @property
    def project_path(self) -> Path:
        return self.workspace.project_path

    @property
    def chat_id(self) -> str:
        return self.workspace.chat_id

    @property
    def file_id(self) -> str:
        return self.workspace.file_id


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
            from project_context.ui import UI

            UI.error(
                f"Comando desconocido: '{name}'. Escribe 'help' para ver la lista."
            )
            return True

        metadata = self._commands[cmd_name]

        if metadata.require_chat and not ctx.chat_id:
            from project_context.ui import UI

            UI.error(
                "No se encontró una sesión de chat activa para ejecutar este comando."
            )
            return True

        return metadata.handler(ctx, args)
