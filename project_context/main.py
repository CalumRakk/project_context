import os
import sys
import warnings

import typer

os.environ["LOG_LEVEL"] = "CRITICAL"


def setup_terminal_behavior():
    """Configura el manejo de advertencias y comportamiento de la consola."""

    def custom_warning_handler(
        message, category, filename, lineno, file=None, line=None
    ):
        from project_context.utils import UI

        msg_str = str(message)

        if issubclass(category, FutureWarning):
            # Limpiar el mensaje de Google para que sea más legible
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

from project_context.commands import profile, run

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

app.add_typer(profile.app, name="profile")
app.command(name="run")(run.run_command)


def main():
    app()


if __name__ == "__main__":
    main()
