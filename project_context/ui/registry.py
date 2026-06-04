from pathlib import Path
from typing import Callable, Dict, List, Optional

from project_context.api_drive import GoogleDriveManager
from project_context.exceptions import (
    InvalidCommandArgumentError,
    MissingStateError,
)
from project_context.schema import LocalContextItems
from project_context.workspace import ProjectContext


class SessionContext:
    def __init__(
        self,
        api: GoogleDriveManager,
        workspace: ProjectContext,
    ):
        self.api = api
        self.workspace = workspace

    @property
    def project_path(self) -> Path:
        return self.workspace.project_path

    @property
    def chat_id(self) -> str:
        chat_id = self.workspace.chat_id
        if not chat_id:
            raise MissingStateError("No se encontró una sesión de chat activa.")
        return chat_id

    @property
    def file_id(self) -> str:
        file_id = self.workspace.file_id
        if not file_id:
            raise MissingStateError("Falta el identificador del archivo de contexto.")
        return file_id

    @property
    def context_items(self) -> LocalContextItems:
        return self.workspace.context_items


class CommandMetadata:
    """Metadatos de configuración para comandos de la CLI."""

    def __init__(
        self,
        handler: Callable[[SessionContext, List[str]], Optional[bool]],
        require_chat: bool,
        description: Optional[str] = None,
    ):
        self.handler = handler
        self.require_chat = require_chat

        if description:
            self.description = description
        elif handler.__doc__:
            self.description = handler.__doc__.strip().split("\n")[0]
        else:
            self.description = "Sin descripción disponible."


class CommandRegistry:
    """Enrutador de comandos interactivos limpio y sin efectos colaterales de monitoreo o vanish."""

    def __init__(self):
        self.commands: Dict[str, CommandMetadata] = {}

    def register(
        self,
        *names: str,
        require_chat: bool = False,
        description: Optional[str] = None,
        **kwargs,
    ):
        def decorator(func: Callable[[SessionContext, List[str]], Optional[bool]]):
            meta = CommandMetadata(
                handler=func,
                require_chat=require_chat,
                description=description,
            )
            for name in names:
                self.commands[name] = meta
            return func

        return decorator

    def execute(
        self, name: str, ctx: SessionContext, args_list: List[str]
    ) -> Optional[bool]:
        cmd_meta = None
        resolved_args = args_list

        if args_list:
            subcommand_candidate = f"{name}:{args_list[0].lower()}"
            if subcommand_candidate in self.commands:
                cmd_meta = self.commands[subcommand_candidate]
                resolved_args = args_list[1:]

        if not cmd_meta:
            cmd_meta = self.commands.get(name)

        if not cmd_meta:
            raise InvalidCommandArgumentError(f"Comando desconocido: '{name}'")

        if cmd_meta.require_chat and not ctx.workspace.chat_id:
            raise MissingStateError(
                "No se encontró una sesión de chat activa en este proyecto."
            )

        return cmd_meta.handler(ctx, resolved_args)


registry = CommandRegistry()
