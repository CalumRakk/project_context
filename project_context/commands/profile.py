import shutil
from pathlib import Path

import typer

from project_context.utils import profile_manager

app = typer.Typer(help="Gestión de perfiles de usuario (Multicuentas).")


@app.command("list")
def list_profiles():
    """Lista los perfiles disponibles."""
    active = profile_manager.get_active_profile_name()
    profiles = profile_manager.list_profiles()

    typer.echo("\nPerfiles disponibles:")
    for p in profiles:
        prefix = "Ok" if p == active else "  "
        color = typer.colors.GREEN if p == active else None
        typer.secho(f"{prefix} {p}", fg=color)
    typer.echo("")


@app.command("add")
def add_profile(name: str):
    """Crea un nuevo perfil."""
    if name in profile_manager.list_profiles():
        typer.secho(f"El perfil '{name}' ya existe.", fg=typer.colors.YELLOW)
        return

    profile_manager.set_active_profile(name)
    typer.secho(f"Perfil '{name}' creado y activado.", fg=typer.colors.GREEN)
    typer.echo(
        "La próxima vez que ejecutes el comando 'run', se te pedirá autenticación."
    )


@app.command("use")
def switch_profile(name: str):
    """Cambia el perfil activo."""
    if name not in profile_manager.list_profiles():
        typer.secho(f"Error: El perfil '{name}' no existe.", fg=typer.colors.RED)
        return

    profile_manager.set_active_profile(name)
    typer.secho(f"Perfil cambiado a: {name}", fg=typer.colors.GREEN)


@app.command("info")
def profile_info():
    """Información del perfil actual y credenciales."""
    name = profile_manager.get_active_profile_name()
    working_dir = profile_manager.get_working_dir()
    secrets_path, secrets_type = profile_manager.resolve_secrets_file()
    token_path = working_dir / "token.json"

    typer.echo(f"\n--- Perfil Actual: {name} ---")
    typer.echo(f"Datos:      {working_dir}")
    typer.echo(f"Secretos:   {secrets_type}")
    typer.echo(f"   └── Ruta:    {secrets_path}")

    if token_path.exists():
        typer.secho("Estado:     Sesión activa (Token existe)", fg=typer.colors.GREEN)
    else:
        typer.secho(
            "Estado:     Sesión inactiva (Requiere login)", fg=typer.colors.YELLOW
        )
    typer.echo("")


@app.command("set-secrets")
def set_secrets(
    secrets_path: Path = typer.Argument(
        ...,
        exists=True,
        dir_okay=False,
        readable=True,
        help="Ruta al archivo client_secrets.json",
    )
):
    """
    Instala un client_secrets.json específico para este perfil.
    Util si este perfil usa una App de Google Cloud distinta a la global.
    """
    target_path = profile_manager.get_working_dir() / "client_secrets.json"
    shutil.copy(secrets_path, target_path)
    typer.secho(
        f"Secretos específicos instalados para '{profile_manager.get_active_profile_name()}'.",
        fg=typer.colors.GREEN,
    )

    token_path = profile_manager.get_working_dir() / "token.json"
    if token_path.exists():
        token_path.unlink()
        typer.secho(
            "Token anterior eliminado por seguridad. Re-autenticación requerida.",
            fg=typer.colors.YELLOW,
        )
