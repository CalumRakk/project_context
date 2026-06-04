import logging
import os
import sys
import warnings
from datetime import datetime
from typing import Annotated, Optional

import typer

from project_context import __version__
from project_context.commands import run, secrets, update
from project_context.logging_config import setup_logging
from project_context.utils import get_app_root_dir

os.environ["LOG_LEVEL"] = "CRITICAL"

logging.getLogger("googleapiclient").setLevel(logging.CRITICAL)


def setup_terminal_behavior():
    """Configura el manejo de advertencias y comportamiento de la consola."""

    def custom_warning_handler(
        message, category, filename, lineno, file=None, line=None
    ):
        from project_context.utils import UI

        msg_str = str(message)

        if issubclass(category, FutureWarning):
            if "Google" in msg_str and "Python version" in msg_str:
                UI.warn(
                    "Google Cloud dejará de soportar Python 3.10 en Octubre de 2026. Se recomienda actualizar a 3.11+."
                )
            else:
                UI.warn(f"Optimización sugerida: {msg_str}")
        else:
            UI.info(f"[dim]{category.__name__}: {msg_str}[/]")

    warnings.showwarning = custom_warning_handler


setup_terminal_behavior()


# Silenciar loguru/gitingest
try:
    from loguru import logger

    logger.disable("gitingest")
except ImportError:
    pass

if sys.platform.startswith("win"):
    os.system("chcp 65001 > nul")
    if sys.stdout.encoding != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore
    if sys.stderr.encoding != "utf-8":
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore


app = typer.Typer(
    name="project-context",
    help="Herramienta CLI para gestionar contexto de proyecto en Google AI Studio.",
    add_completion=False,
    no_args_is_help=True,
)


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
    cmd_name = ctx.invoked_subcommand or "sys"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    log_dir = get_app_root_dir() / "logs"
    log_path = log_dir / f"{timestamp}_{cmd_name}.log"

    setup_logging(log_path, debug)


# Registro de sub-grupos
# app.add_typer(profile.app, name="profile")
# app.add_typer(dev.app, name="dev")
app.add_typer(secrets.app, name="secrets")

# Registro de comandos principales de primer nivel
app.command(name="run")(run.run_command)
app.command(name="update")(update.update_command)
# app.command(name="shell")(shell.shell_command)


def main():
    import logging
    import sys

    from project_context.exceptions import ProjectContextError
    from project_context.ui.presenters import AuthConsolePresenter
    from project_context.ui.ui import UI

    logger = logging.getLogger("project_context.main")

    try:
        app()
    except ProjectContextError as e:
        # Los errores de dominio conocidos se delegan al presentador de consola
        AuthConsolePresenter.handle_error(e)

    except KeyboardInterrupt:
        # Manejo limpio de Ctrl+C fuera de la sesión interactiva
        UI.print("\n[orange1]![/] Ejecución interrumpida por el usuario.", indent=False)
        # Código de salida estándar para interrupciones por señal (SIGINT)
        sys.exit(130)

    except Exception as e:
        # Registramos el traceback completo en el archivo de log local de forma silenciosa
        logger.exception("Error inesperado en el hilo de ejecución principal:")

        # Presentamos un mensaje simplificado al usuario en consola
        UI.error(f"Ocurrió un error inesperado: {e}", spacing="top")
        UI.info(
            "Se han registrado los detalles técnicos en los archivos de registro (logs) de la aplicación."
        )
        sys.exit(1)
