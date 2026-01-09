from pathlib import Path
from typing import Optional

import typer
from typing_extensions import Annotated

from project_context.api_drive import AIStudioDriveManager
from project_context.ops import initialize_project_context, update_context
from project_context.ui.interactive import interactive_session
from project_context.utils import (
    load_project_context_state,
    profile_manager,
    save_project_context_state,
)


def run_command(
    project_path: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=False,
            resolve_path=True,
            help="Ruta del directorio del proyecto a analizar.",
        ),
    ],
    update_only: Annotated[
        bool,
        typer.Option(
            "--update-only",
            "-u",
            help="Solo crea/actualiza el contexto y sale sin entrar al modo interactivo.",
        ),
    ] = False,
    interactive_only: Annotated[
        bool,
        typer.Option(
            "--interactive-only",
            "-i",
            help="Entra directo a modo interactivo (requiere contexto previo).",
        ),
    ] = False,
    use_profile: Annotated[
        Optional[str],
        typer.Option(
            "--use", help="Usa un perfil específico temporalmente para esta ejecución."
        ),
    ] = None,
):
    """
    Analiza y sincroniza el proyecto en la ruta indicada con Google AI Studio.
    """

    if use_profile:
        available_profiles = profile_manager.list_profiles()
        if use_profile not in available_profiles:
            typer.secho(
                f"Error: El perfil de usuario '{use_profile}' no existe.",
                fg=typer.colors.RED,
            )
            typer.echo(f"Perfiles disponibles: {', '.join(available_profiles)}")
            raise typer.Exit(code=1)

        profile_manager.set_temporary_profile(use_profile)
        typer.secho(f"Usando perfil temporal: {use_profile}", fg=typer.colors.YELLOW)

    try:
        api = AIStudioDriveManager()
    except Exception as e:
        typer.secho(f"Error inicializando Drive: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    state = load_project_context_state(project_path)

    if interactive_only:
        if state is None or not state.get("chat_id"):
            typer.secho(
                "Error: No hay contexto previo. Ejecuta sin -i primero.",
                fg=typer.colors.RED,
            )
            raise typer.Exit(code=1)
        print("Modo interactivo rápido.")
    else:

        if state is None:
            state = initialize_project_context(api, project_path)
        else:
            state = update_context(api, project_path, state)

        save_project_context_state(project_path, state)

    if update_only:
        print("Sincronizado. Saliendo.")
    else:
        interactive_session(api, state, project_path)
