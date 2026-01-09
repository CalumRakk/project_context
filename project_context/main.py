import os
import sys

import typer

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
