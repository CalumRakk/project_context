import logging
import os
import sys
from datetime import datetime
from typing import Annotated, Optional

import typer

from project_context import __version__
from project_context.commands import run, secrets, update
from project_context.logging_config import setup_logging
from project_context.utils import (
    disable_gitingest_logs,
    get_app_root_dir,
    setup_terminal_behavior,
    setup_windows_terminal,
)

os.environ["LOG_LEVEL"] = "CRITICAL"

logging.getLogger("googleapiclient").setLevel(logging.CRITICAL)


setup_terminal_behavior()
disable_gitingest_logs()
setup_windows_terminal()


app = typer.Typer(
    name="project-context",
    help="Herramienta CLI para gestionar contexto de proyecto en Google AI Studio.",
    add_completion=False,
    no_args_is_help=True,
)
current_log_path = None


def version_callback(value: bool):
    if value:
        typer.echo(f"project-context v{__version__}")
        raise typer.Exit()


@app.callback()
def global_options(
    ctx: typer.Context,
    version: Annotated[
        Optional[bool],
        typer.Option(
            "--version",
            "-v",
            callback=version_callback,
            is_eager=True,
            help="Muestra la versión actual de la herramienta y sale.",
        ),
    ] = None,
    debug: Annotated[
        bool,
        typer.Option(
            "--debug",
            is_flag=True,
            help="Habilita modo debug con logs más detallados en consola.",
        ),
    ] = False,
):
    global current_log_path
    cmd_name = ctx.invoked_subcommand or "sys"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    log_dir = get_app_root_dir() / "logs"
    current_log_path = log_dir / f"{timestamp}_{cmd_name}.log"

    setup_logging(current_log_path, debug)


# Registro de sub-grupos
# app.add_typer(profile.app, name="profile")
# app.add_typer(dev.app, name="dev")
app.add_typer(secrets.app, name="secrets")

# Registro de comandos principales de primer nivel
app.command(name="run")(run.run_command)
app.command(name="update")(update.update_command)


def main():
    import logging

    from project_context.ui import UI

    logger = logging.getLogger("project_context.main")

    try:
        app()
    except KeyboardInterrupt:
        # Manejo limpio de Ctrl+C fuera de la sesión interactiva
        UI.print("\n[orange1]![/] Ejecución interrumpida por el usuario.", indent=False)
        # Código de salida estándar para interrupciones por señal (SIGINT)
        sys.exit(130)

    except Exception as e:
        logger.exception("Error inesperado en el hilo de ejecución principal:")
        UI.error(f"Ocurrió un error inesperado: {e}", spacing="top")
        if current_log_path:
            UI.info(
                f"Revisa el archivo de log para más detalles: [dim]{current_log_path.resolve()}[/]"
            )
        sys.exit(1)
