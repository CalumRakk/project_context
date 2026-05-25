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


class CommandMetadata:
    def __init__(
        self,
        handler: Callable[[SessionContext, List[str]], Optional[bool]],
        require_chat: bool,
        allow_in_vanish: bool,
        manage_monitor: bool,
    ):
        self.handler = handler
        self.require_chat = require_chat
        self.allow_in_vanish = allow_in_vanish
        self.manage_monitor = manage_monitor


class CommandRegistry:
    def __init__(self):
        self.commands: Dict[str, CommandMetadata] = {}

    def register(
        self,
        *names: str,
        require_chat: bool = False,
        allow_in_vanish: bool = False,
        manage_monitor: bool = True,
    ):
        def decorator(func: Callable[[SessionContext, List[str]], Optional[bool]]):
            meta = CommandMetadata(
                handler=func,
                require_chat=require_chat,
                allow_in_vanish=allow_in_vanish,
                manage_monitor=manage_monitor,
            )
            for name in names:
                self.commands[name] = meta
            return func

        return decorator

    def execute(
        self, name: str, ctx: SessionContext, args_list: List[str]
    ) -> Optional[bool]:
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
            return cmd_meta.handler(ctx, args_list)
        finally:
            if cmd_meta.manage_monitor:
                ctx.start_monitor()


registry = CommandRegistry()
