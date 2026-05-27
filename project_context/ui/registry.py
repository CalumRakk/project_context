from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

from project_context.api_drive import AIStudioDriveManager
from project_context.exceptions import (
    InvalidCommandArgumentError,
    MissingStateError,
    VanishModeActiveError,
)
from project_context.history import SnapshotManager
from project_context.utils import save_project_context_state


@dataclass
class SessionContext:
    api: AIStudioDriveManager
    state: dict
    project_path: Path
    monitor: SnapshotManager
    session_media_root: Optional[Path] = None

    def stop_monitor(self):
        self.monitor.stop_monitoring()

    def start_monitor(self):
        if self.state.get("monitor_active", False):
            self.monitor.start_monitoring()

    def update_state(self, new_state: dict):
        self.state = new_state
        self.monitor.state = new_state
        save_project_context_state(self.project_path, new_state)

    @property
    def chat_id(self) -> str:
        """Retorna el ID del chat activo garantizando su existencia."""
        cid = self.state.get("chat_id")
        if not cid:
            raise MissingStateError(
                "No se encontró una sesión de chat activa en este proyecto."
            )
        return cid

    @property
    def file_id(self) -> str:
        """Retorna el ID del archivo de contexto maestro en Drive."""
        fid = self.state.get("file_id")
        if not fid:
            raise MissingStateError(
                "Falta el identificador del archivo de contexto maestro en Drive."
            )
        return fid

    @property
    def context_items(self) -> dict:
        """Inicializa y retorna la estructura de elementos enfocados de forma segura."""
        if "context_items" not in self.state:
            self.state["context_items"] = {}
        items = self.state["context_items"]
        items.setdefault("files", [])
        items.setdefault("folders", [])
        items.setdefault("exclusions", [])
        return items


class CommandMetadata:
    def __init__(
        self,
        handler: Callable[[SessionContext, List[str]], Optional[bool]],
        require_chat: bool,
        allow_in_vanish: bool,
        manage_monitor: bool,
        description: Optional[str] = None,
    ):
        self.handler = handler
        self.require_chat = require_chat
        self.allow_in_vanish = allow_in_vanish
        self.manage_monitor = manage_monitor

        if description:
            self.description = description
        elif handler.__doc__:
            self.description = handler.__doc__.strip().split("\n")[0]
        else:
            self.description = "Sin descripción disponible."


class CommandRegistry:
    def __init__(self):
        self.commands: Dict[str, CommandMetadata] = {}

    def register(
        self,
        *names: str,
        require_chat: bool = False,
        allow_in_vanish: bool = False,
        manage_monitor: bool = True,
        description: Optional[str] = None,
    ):
        def decorator(func: Callable[[SessionContext, List[str]], Optional[bool]]):
            meta = CommandMetadata(
                handler=func,
                require_chat=require_chat,
                allow_in_vanish=allow_in_vanish,
                manage_monitor=manage_monitor,
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

        # Enrutamiento jerárquico dinámico (Option B Namespace Routing)
        if args_list:
            subcommand_candidate = f"{name}:{args_list[0].lower()}"
            if subcommand_candidate in self.commands:
                cmd_meta = self.commands[subcommand_candidate]
                resolved_args = args_list[1:]  # Consumimos el subcomando

        # Fallback al comando base en caso de no haber subcomando
        if not cmd_meta:
            cmd_meta = self.commands.get(name)

        if not cmd_meta:
            raise InvalidCommandArgumentError(f"Comando desconocido: '{name}'")

        if ctx.state.get("vanished") and not cmd_meta.allow_in_vanish:
            raise VanishModeActiveError(
                "La consola está congelada en modo vanish. Usa 'vanish off' para restaurar la sesión."
            )

        if cmd_meta.require_chat and not ctx.state.get("chat_id"):
            raise MissingStateError(
                "No se encontró una sesión de chat activa en este proyecto."
            )

        if cmd_meta.manage_monitor:
            ctx.stop_monitor()

        try:
            return cmd_meta.handler(ctx, resolved_args)
        finally:
            if cmd_meta.manage_monitor:
                ctx.start_monitor()


registry = CommandRegistry()
