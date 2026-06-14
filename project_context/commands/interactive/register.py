from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from project_context.core.project_context import ProjectContext
from project_context.core.snapshot_mg import SnapshotManager
from project_context.services.api_drive import GoogleDriveManager
from project_context.services.commit_service import CommitService
from project_context.services.context_service import ContextService
from project_context.services.git_service import GitService
from project_context.ui import UI


@dataclass
class SessionContext:
    """Contenedor explícito de dependencias para la sesión interactiva."""

    api: GoogleDriveManager
    project_context: ProjectContext

    _snapshot_manager: Optional[SnapshotManager] = None
    _git: Optional[GitService] = None
    _commit_service: Optional[CommitService] = None
    _context_service: Optional[ContextService] = None

    @property
    def snapshot_manager(self):
        """Inicializa de forma perezosa y con caché el SnapshotManager."""
        if self._snapshot_manager is None:
            self._snapshot_manager = SnapshotManager(self.api, self.project_context)
            self._snapshot_manager.initialize_schema()

        return self._snapshot_manager

    @property
    def git(self) -> GitService:
        """Inicializa de forma perezosa y con caché el GitService."""
        if self._git is None:
            self._git = GitService(self.project_context.project_path)
        return self._git

    @property
    def commit_service(self) -> CommitService:
        """Inicializa de forma perezosa y con caché el CommitService."""
        if self._commit_service is None:
            self._commit_service = CommitService(self.git)
        return self._commit_service

    @property
    def context_service(self) -> ContextService:
        """Inicializa de forma perezosa y con caché el ContextService."""
        if self._context_service is None:
            self._context_service = ContextService(self.api, self.project_context)
        return self._context_service


class CommandOption:
    """Representa una opción o flag de consola (ej: --force, -f)."""

    def __init__(self, names: List[str], description: str, is_flag: bool = True):
        self.names = names  # Ej: ["--force", "-f"]
        self.description = description
        self.is_flag = is_flag


class CommandArgument:
    """Representa un argumento posicional (ej: snapshot_id, target_file)."""

    def __init__(
        self,
        name: str,
        description: str,
        required: bool = True,
        completer_type: Optional[str] = None,
    ):
        self.name = name
        self.description = description
        self.required = required
        self.completer_type = completer_type  # Ej: 'path', 'profile', 'snapshot'


class ParsedArgs:
    """Contenedor seguro de argumentos procesados por el comando."""

    def __init__(self):
        self.flags: Dict[str, bool] = {}
        self.options: Dict[str, str] = {}
        self.args: List[str] = []

    def has_flag(self, flag_name: str) -> bool:
        """Comprueba si un flag booleano fue ingresado en la llamada."""
        return self.flags.get(flag_name.lower(), False)

    def get_option(self, opt_name: str) -> Optional[str]:
        """Obtiene el valor asociado a una opción con parámetro."""
        return self.options.get(opt_name.lower())

    def get_arg(self, index: int, default: Optional[str] = None) -> Optional[str]:
        """Obtiene un argumento posicional por su índice."""
        if index < len(self.args):
            return self.args[index]
        return default


class CommandMetadata:
    """Metadatos legibles para cada comando registrado."""

    def __init__(
        self,
        handler: Callable[[SessionContext, ParsedArgs], Optional[bool]],
        description: str,
        require_chat: bool = True,
        options: Optional[List[CommandOption]] = None,
        arguments: Optional[List[CommandArgument]] = None,
    ):
        self.handler = handler
        self.description = description
        self.require_chat = require_chat
        self.options = options or []
        self.arguments = arguments or []


class InteractiveRegistry:
    """Registro explícito de comandos para evitar importaciones implícitas."""

    def __init__(self):
        self._commands: Dict[str, CommandMetadata] = {}

    def register(
        self,
        names: List[str],
        handler: Callable[[SessionContext, ParsedArgs], Optional[bool]],
        description: str,
        require_chat: bool = True,
        options: Optional[List[CommandOption]] = None,
        arguments: Optional[List[CommandArgument]] = None,
    ):
        """Asocia explícitamente uno o más alias a un manejador de comandos."""
        metadata = CommandMetadata(
            handler=handler,
            description=description,
            require_chat=require_chat,
            options=options,
            arguments=arguments,
        )
        for name in names:
            self._commands[name.lower()] = metadata

    def get_commands_map(self) -> Dict[str, CommandMetadata]:
        return self._commands

    def execute(
        self, name: str, ctx: SessionContext, args_list: List[str]
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

        # Procesamiento y validación sintáctica centralizada
        try:
            parsed_args = self._parse_arguments(args_list, metadata)
        except ValueError as e:
            UI.error(f"Sintaxis inválida: {e}")
            return True

        return metadata.handler(ctx, parsed_args)

    def _parse_arguments(
        self, args_list: List[str], metadata: CommandMetadata
    ) -> ParsedArgs:
        parsed = ParsedArgs()

        flag_aliases: Dict[str, str] = {}
        option_aliases: Dict[str, str] = {}

        # Mapeamos alias al identificador canónico (primer elemento en names)
        for opt in metadata.options:
            canonical = opt.names[0].lower()
            if opt.is_flag:
                parsed.flags[canonical] = False
                for name in opt.names:
                    flag_aliases[name.lower()] = canonical
            else:
                for name in opt.names:
                    option_aliases[name.lower()] = canonical

        i = 0
        while i < len(args_list):
            item = args_list[i]
            item_lower = item.lower()

            if item_lower in flag_aliases:
                canonical = flag_aliases[item_lower]
                parsed.flags[canonical] = True
            elif item_lower in option_aliases:
                canonical = option_aliases[item_lower]
                if i + 1 < len(args_list):
                    parsed.options[canonical] = args_list[i + 1]
                    i += 1
                else:
                    raise ValueError(f"La opción '{item}' requiere un valor.")
            else:
                parsed.args.append(item)
            i += 1

        # Validar argumentos requeridos
        required_args = [arg for arg in metadata.arguments if arg.required]
        if len(parsed.args) < len(required_args):
            missing = required_args[len(parsed.args) :]
            missing_names = ", ".join([f"<{arg.name}>" for arg in missing])
            raise ValueError(f"Faltan argumentos obligatorios: {missing_names}")

        return parsed
