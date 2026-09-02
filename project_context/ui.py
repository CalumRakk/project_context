import logging
import time
from contextlib import contextmanager
from typing import List, Literal, Optional, Union

from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text
from rich.theme import Theme

Spacing = Optional[Literal["top", "bottom", "block"]]
custom_theme = Theme(
    {
        "info": "dim cyan",
        "warning": "orange1",
        "error": "bold red",
        "success": "bold green",
        "progress": "italic blue",
    }
)

console = Console(theme=custom_theme)
ui_logger = logging.getLogger("project_context.ui")


def strip_markup(text: str) -> str:
    """Elimina las etiquetas de color [bold], [red], etc. para el archivo de log."""
    return Text.from_markup(text).plain


class UI:
    @classmethod
    def separator(cls):
        console.print(Columns([Rule(style="bright_black")], width=45))

    @staticmethod
    def sleep_progress(seconds: int):
        """Muestra una cuenta regresiva visual sin inundar el log."""
        if seconds <= 0:
            return

        UI.info(f"Iniciando pausa ({seconds // 60} min)...")

        with console.status("[bold blue]Pausa activa...", spinner="line") as status:
            for i in range(seconds, 0, -1):
                time.sleep(1)

                if i % 60 == 0 or i <= 10:
                    mins = i // 60
                    secs = i % 60
                    status.update(
                        f"[bold blue]Pausa activa: Siguiente parte en {mins}m {secs}s..."
                    )

        UI.success("Pausa finalizada. Reanudando subida.")

    @staticmethod
    def _print(
        message: str,
        *,
        indent: bool = False,
        spacing: Spacing = None,
        log_level: int = logging.INFO,
        **kwargs,
    ):
        """Imprime un mensaje con formato y opcionalmente agrega espacio antes o después."""
        if spacing in ("top", "block"):
            console.print()

        prefix = "  " if indent else ""
        console.print(f"{prefix}{message}", **kwargs)

        clean_msg = strip_markup(message).strip()
        if clean_msg:
            ui_logger.log(log_level, clean_msg)

        if spacing in ("bottom", "block"):
            console.print()

    @staticmethod
    def print(message: str, *, indent: bool = True, spacing: Spacing = None, **kwargs):
        UI._print(
            f"{message}",
            indent=indent,
            spacing=spacing,
            log_level=logging.DEBUG,
            **kwargs,
        )

    @staticmethod
    def info(message: str, *, spacing: Spacing = None, **kwargs):
        UI._print(
            f"[info]i[/] {message}", spacing=spacing, log_level=logging.INFO, **kwargs
        )

    @staticmethod
    def success(message: str, *, spacing: Spacing = None, **kwargs):
        UI._print(
            f"[success]>[/] {message}",
            spacing=spacing,
            log_level=logging.INFO,
            **kwargs,
        )

    @staticmethod
    def warn(message: str, *, spacing: Spacing = None, **kwargs):
        UI._print(
            f"[warning]![/] {message}",
            spacing=spacing,
            log_level=logging.WARNING,
            **kwargs,
        )

    @staticmethod
    def error(message: str, *, spacing: Spacing = None, **kwargs):
        UI._print(
            f"[error]X[/] {message}", spacing=spacing, log_level=logging.ERROR, **kwargs
        )

    @staticmethod
    def tip(
        message: str,
        commands: Optional[Union[List[str], str]] = None,
        spacing: Spacing = None,
        **kwarg,
    ):
        """Muestra una sugerencia al usuario. Opcionalmente formatea un comando."""
        UI._print(
            f"[dim cyan]  Tip:[/] {message}",
            spacing=spacing,
            log_level=logging.INFO,
            **kwarg,
        )

        if commands:
            if isinstance(commands, str):
                commands = [commands]

            for command in commands:
                console.print(f"    [bold yellow]> {command}[/]")

    @staticmethod
    def educational_tip(
        message: str,
        title: Optional[str] = None,
        commands: Optional[Union[List[str], str]] = None,
        spacing: Spacing = None,
        border_style: str = "cyan",
    ):
        """Muestra una sugerencia educativa destacada en un recuadro (Panel)."""
        if spacing in ("top", "block"):
            console.print()

        content_lines = [message]

        if commands:
            content_lines.append("")
            if isinstance(commands, str):
                commands = [commands]

            for command in commands:
                content_lines.append(f"   [bold yellow]> {command}[/]")

        panel_content = "\n".join(content_lines)

        panel = Panel(
            panel_content,
            title=f"[bold]{title}[/]" if title else None,
            border_style=border_style,
            padding=(1, 1),
        )

        console.print(panel)
        clean_content = strip_markup(panel_content).strip()
        if clean_content:
            ui_logger.info(clean_content)

        if spacing in ("bottom", "block"):
            console.print()

    @classmethod
    @contextmanager
    def loading(cls, message: str):
        with console.status(f"[bold green]{message}"):
            yield
