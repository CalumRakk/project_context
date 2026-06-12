from project_context.commands.interactive.base import cmd_exit, cmd_help
from project_context.commands.interactive.chat import cmd_clear, cmd_history, cmd_update
from project_context.commands.interactive.commit import cmd_commit
from project_context.commands.interactive.register import InteractiveRegistry


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
    )

    registry.register(
        names=["history", "hist"],
        handler=cmd_history,
        description="Muestra el historial de snapshots guardados de forma paginada.",
        require_chat=False,  # No requiere un chat remoto activo para consultar la DB local
    )

    # --- COMANDOS DE COMMIT ---
    registry.register(
        names=["commit", "ci"],
        handler=cmd_commit,
        description="Genera una sugerencia de commit temporal con base en el diff de Git actual.",
        require_chat=True,
    )

    return registry
