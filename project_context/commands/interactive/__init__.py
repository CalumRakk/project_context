from project_context.commands.interactive.base import cmd_exit, cmd_help
from project_context.commands.interactive.chat import cmd_clear, cmd_update
from project_context.commands.interactive.context import cmd_context_info
from project_context.commands.interactive.register import InteractiveRegistry

# TODO: la nomenclatura de comandos deberia ser comandos y subcomandos para los comandos interactivos.


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

    # --- COMANDOS DE CONTEXTO ---

    registry.register(
        names=["context", "ctx"],
        handler=cmd_context_info,
        description="Muestra los archivos enfocados actualmente y el árbol resultante.",
        require_chat=True,
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
        description="Cierra la sesión interactiva actual de forma segura.",
        require_chat=False,
    )

    return registry
