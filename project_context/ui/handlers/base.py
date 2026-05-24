from project_context.ui.registry import SessionContext, registry
from project_context.utils import UI, console


def command_help():
    """Muestra la ayuda con un formato más limpio."""
    console.print("\n[bold cyan]Comandos Disponibles:[/]")
    help_text = (
        "  [bold]commit[/]             - Enviar git diff (staged) al chat.\n"
        "  [bold]edit[/]               - Abrir editor visual de historial.\n"
        "  [bold]monitor on/off[/]     - Auto-guardado de historial.\n"
        "  [bold]save <msg>[/]         - Snapshot manual con nombre.\n"
        "  [bold]history[/]            - Ver puntos de restauración.\n"
        "  [bold]restore <id>[/]       - Restaurar chat y contexto.\n"
        "  [bold]transfer <perfil>[/]  - Migrar la sesión actual a otra cuenta.\n"
        "  [bold]tree[/]               - Muestra el árbol de archivos del contexto actual.\n"
        "  [bold]clear[/]              - Limpiar historial del chat en Drive.\n"
        "  [bold]update[/]             - Forzar actualización de contexto.\n"
        "  [bold]reset[/]              - Reconstrucción total del chat.\n"
        "  [bold]tokens <cant>[/]      - Ajustar manualmente los tokens del contexto.\n"
        "  [bold]images <archivo>[/]   - Sincroniza imágenes referenciadas.\n"
        "  [bold]context <ruta>[/]     - Enfoca el contexto en una ruta específica.\n"
        "  [bold]fix[/]                - Reparar estructura interna del chat.\n"
        "  [bold]vanish [on|off][/]    - Ocultar o restaurar el chat de forma temporal.\n"
        "  [bold]exit / quit[/]        - Salir de la sesión.\n"
        "  [bold]story <ruta>[/]      - Entrar al modo co-escritura con un archivo ancla.\n"
        "  [bold]story exit[/]        - Salir del modo co-escritura.\n"
    )
    console.print(help_text)


@registry.register("exit", "quit", manage_monitor=False, allow_in_vanish=True)
def cmd_exit(ctx: SessionContext, args: str):
    ctx.stop_monitor()
    UI.info("Cerrando sesión...")
    return False


@registry.register("help", allow_in_vanish=True)
def cmd_help(ctx: SessionContext, args: str):
    command_help()
