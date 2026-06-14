from project_context.commands.interactive.base import cmd_exit, cmd_help
from project_context.commands.interactive.chat import (
    cmd_clear,
    cmd_history,
    cmd_restore,
    cmd_save,
    cmd_update,
)
from project_context.commands.interactive.commit import cmd_commit
from project_context.commands.interactive.register import (
    CommandArgument,
    CommandOption,
    InteractiveRegistry,
)
from project_context.commands.interactive.story import cmd_story


def bootstrap_interactive_registry() -> InteractiveRegistry:
    """Construye y configura el registro de comandos de forma explícita."""
    registry = InteractiveRegistry()

    # --- COMANDOS BASE ---
    registry.register(
        names=["exit", "quit"],
        handler=cmd_exit,
        description="Cierra la sesión interactiva actual de forma segura.",
        require_chat=False,
    )

    registry.register(
        names=["help", "h"],
        handler=lambda ctx, args: cmd_help(ctx, args, registry),
        description="Muestra la tabla de ayuda con los comandos configurados.",
        require_chat=False,
    )

    # --- COMANDOS DE CHAT ---
    registry.register(
        names=["clear"],
        handler=cmd_clear,
        description="Limpia el historial de la conversación manteniendo el contexto inicial.",
        require_chat=True,
    )

    registry.register(
        names=["update"],
        handler=cmd_update,
        description="Actualiza el contenido del archivo de contexto en Drive.",
        require_chat=False,
        options=[
            CommandOption(
                ["--force", "-f"],
                "Fuerza la actualización completa sin comprobar cambios.",
                is_flag=True,
            ),
            CommandOption(
                ["--tree", "-t"],
                "Muestra la estructura jerárquica de archivos enviados.",
                is_flag=True,
            ),
        ],
    )

    registry.register(
        names=["save"],
        handler=cmd_save,
        description="Crea de forma manual un snapshot de respaldo etiquetado con un mensaje.",
        require_chat=True,
        arguments=[
            CommandArgument(
                "mensaje", "Descripción o motivo del snapshot.", required=True
            )
        ],
    )

    registry.register(
        names=["restore"],
        handler=cmd_restore,
        description="Restaura el entorno de Drive y la sesión de chat usando un ID de snapshot.",
        require_chat=True,
        arguments=[
            CommandArgument(
                "snapshot_id",
                "ID del snapshot a restaurar.",
                required=True,
                completer_type="snapshot",
            )
        ],
    )

    registry.register(
        names=["history", "hist"],
        handler=cmd_history,
        description="Muestra el historial de snapshots guardados de forma paginada.",
        require_chat=False,
        options=[
            CommandOption(
                ["--all", "-a"],
                "Muestra todos los snapshots omitiendo la paginación.",
                is_flag=True,
            )
        ],
        arguments=[
            CommandArgument("pagina", "Número de página a visualizar.", required=False)
        ],
    )

    # --- COMANDOS DE COMMIT ---
    registry.register(
        names=["commit", "ci"],
        handler=cmd_commit,
        description="Genera una sugerencia de commit temporal con base en el diff de Git actual.",
        require_chat=True,
        options=[
            CommandOption(
                ["--all", "-a"],
                "Realiza un stage (git add) automático de todas las modificaciones antes del diff.",
                is_flag=True,
            ),
            CommandOption(
                ["--restore", "-r"],
                "Restaura la sesión de chat original removiendo el prompt de commit.",
                is_flag=True,
            ),
        ],
    )

    # --- COMANDO DE HISTORIA ---
    registry.register(
        names=["story"],
        handler=cmd_story,
        description="Configura o procesa las intenciones del modo historia interactivo.",
        require_chat=True,
        arguments=[
            CommandArgument(
                "target",
                "Archivo Markdown a procesar o 'exit' para apagar.",
                required=False,
                completer_type="path",
            )
        ],
    )

    return registry
