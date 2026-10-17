import logging

from rich.table import Table

from project_context.commands.interactive.register import ParsedArgs, SessionContext
from project_context.core.exceptions import (
    ChatSessionError,
    ProfileNotFoundError,
)
from project_context.ui import UI, console

logger = logging.getLogger("project_context.commands.clone")


def cmd_clone(ctx: SessionContext, args: ParsedArgs):
    """
    Clona la sesión de chat actual y todas sus dependencias de Drive hacia otro perfil/cuenta.
    """
    target_profile = args.get_arg(0)
    if not target_profile:
        available = ctx.replication_service.profile_manager.list_profiles()
        profiles_str = (
            ", ".join([f"[cyan]{p}[/]" for p in available])
            if available
            else "[dim]Ninguno[/]"
        )
        UI.warn("Uso: clone <nombre_perfil> [--switch]")
        UI.info(f"Perfiles configurados disponibles: {profiles_str}")
        return

    logger.info(f"CMD_CLONE_INIT: Solicitando clonación hacia '{target_profile}'")

    try:
        with UI.loading(f"Iniciando clonación hacia perfil '{target_profile}'..."):
            summary = ctx.replication_service.replicate_chat_to_profile(target_profile)

        # Presentación visual del resultado

        UI.separator()
        UI.success(
            f"¡Chat clonado exitosamente hacia el perfil [bold cyan]{summary.target_profile}[/]!"
        )

        table = Table(
            title="[bold cyan]Resumen de Replicación de Chat[/]",
            show_header=True,
            header_style="bold magenta",
            box=None,
        )
        table.add_column("Métrica / Detalle", style="bold green", width=28)
        table.add_column("Valor", style="white")

        table.add_row("Perfil Destino", summary.target_profile)
        table.add_row("Cuenta / Correo", summary.target_email)
        table.add_row("ID de Chat en Destino", summary.cloned_chat_id)
        table.add_row("Total de Dependencias", str(summary.total_dependencies))
        table.add_row(
            "Archivos Reutilizados (MD5)",
            f"[dim green]{summary.reused_dependencies} (sin re-subida)[/]",
        )
        table.add_row(
            "Archivos Nuevos Transferidos",
            f"[yellow]{summary.uploaded_dependencies}[/]",
        )

        console.print("")
        console.print(table)
        console.print("")

        UI.info(
            f"URL del Chat en Destino: [bold underline cyan]{summary.cloned_chat_url}[/]"
        )

        # Conmutación de sesión en caliente (Hot-switch)

        should_switch = args.has_flag("--switch")

        if not should_switch:
            try:
                confirm = (
                    console.input(
                        f"\n[bold yellow]?[/] ¿Deseas alternar la sesión actual al perfil '[bold cyan]{summary.target_profile}[/]' ahora mismo? (S/n): "
                    )
                    .strip()
                    .lower()
                )
                if confirm in ("", "s", "si", "y", "yes"):
                    should_switch = True
            except (EOFError, KeyboardInterrupt):
                should_switch = False

        if should_switch and summary.target_api:
            ctx.switch_session(
                new_api=summary.target_api,
                new_email=summary.target_email,
                profile_name=summary.target_profile,
            )
            UI.separator()
            UI.success(
                f"Sesión conmutada exitosamente al perfil: [bold cyan]{summary.target_profile}[/] ({summary.target_email})"
            )
            UI.info(
                f"Chat activo en esta consola: [bold underline cyan]{summary.cloned_chat_url}[/]"
            )
            UI.tip(
                "Ahora comandos como 'update', 'inline' o 'clear' operarán directamente sobre la nueva cuenta."
            )
            UI.separator()
        else:
            UI.tip(
                "Para conectarte a este chat en otra sesión, ejecuta:",
                commands=[f"project-context run --use {summary.target_profile}"],
                spacing="top",
            )
            UI.separator()

    except ProfileNotFoundError as e:
        logger.warning(f"CMD_CLONE_ERROR: {e}")
        UI.error(str(e))
        available = ctx.replication_service.profile_manager.list_profiles()
        if available:
            UI.info(
                f"Perfiles configurados en este equipo: [cyan]{', '.join(available)}[/]"
            )
    except ChatSessionError as e:
        logger.warning(f"CMD_CLONE_ERROR: {e}")
        UI.error(str(e))
    except ValueError as e:
        logger.warning(f"CMD_CLONE_VALIDATION_ERROR: {e}")
        UI.error(str(e))
    except Exception as e:
        logger.exception(f"CMD_CLONE_UNEXPECTED_ERROR: {e}")  # noqa: TRY401
        UI.error(f"Fallo inesperado al clonar el chat hacia '{target_profile}': {e}")
