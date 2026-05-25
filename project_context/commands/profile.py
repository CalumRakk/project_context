import shutil
from pathlib import Path
from typing import Optional

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
        prefix = ">>" if p == active else "  "
        color = typer.colors.GREEN if p == active else None

        profile_data = profile_manager.load_profile_data(p)
        email = profile_data.get("email") or "Sin autenticar"
        secret = profile_data.get("associated_secret") or "Sin asociar"

        typer.secho(f"{prefix} {p:<15} [Cuenta: {email} | Secreto: {secret}]", fg=color)
    typer.echo("")


@app.command("add")
def add_profile(
    name: str,
    secret_name: Optional[str] = typer.Option(
        None,
        help="Nombre del secreto a asociar (opcional, por defecto usa el nombre del perfil)."
    )
):
    """Crea un nuevo descriptor de perfil."""
    if name in profile_manager.list_profiles():
        typer.secho(f"El perfil '{name}' ya existe.", fg=typer.colors.YELLOW)
        return

    profile_manager.set_active_profile(name)
    if secret_name:
        profile_data = profile_manager.get_active_profile_data()
        profile_data["associated_secret"] = secret_name
        profile_manager.save_active_profile_data(profile_data)

    typer.secho(f"Perfil '{name}' creado y activado.", fg=typer.colors.GREEN)
    typer.echo("Usa 'profile set-secrets' para instalar su llave client_secrets.json.")


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
    """Muestra información del perfil activo y recursos relacionados."""
    name = profile_manager.get_active_profile_name()
    profile_data = profile_manager.get_active_profile_data()
    secrets_path, secrets_type = profile_manager.resolve_secrets_file()

    email = profile_data.get("email")
    secret_name = profile_data.get("associated_secret", "client_secrets")

    typer.echo(f"\n--- Perfil Activo: {name} ---")
    typer.echo(f"Cuenta de Google:  {email or 'No autenticado aún'}")
    typer.echo(f"Asociación:        {secrets_type}")
    typer.echo(f"   └── Ruta:       {secrets_path}")

    # Verificar existencia física del secreto
    if secrets_path.exists():
        typer.secho("Secreto físico:    Encontrado en disco", fg=typer.colors.GREEN)
    else:
        typer.secho("Secreto físico:    FALTA ARCHIVO (Instala con 'set-secrets')", fg=typer.colors.RED)

    # Identificar token correspondiente
    if email:
        associated_secret_clean = secret_name if secret_name.endswith(".json") else f"{secret_name}.json"
        token_name = f"{email}__{associated_secret_clean}"
        token_path = profile_manager.tokens_dir / token_name
        if token_path.exists():
            typer.secho(f"Token de Acceso:   Sesión activa ({token_name})", fg=typer.colors.GREEN)
        else:
            typer.secho("Token de Acceso:   No encontrado (Requiere login en próxima ejecución)", fg=typer.colors.YELLOW)
    else:
        typer.echo("Token de Acceso:   Desconocido (Falta flujo OAuth inicial)")
    typer.echo("")


@app.command("set-secrets")
def set_secrets(
    secrets_path: Path = typer.Argument(
        ...,
        exists=True,
        dir_okay=False,
        readable=True,
        help="Ruta al archivo client_secrets.json que deseas instalar.",
    ),
    secret_name: Optional[str] = typer.Option(
        None,
        help="Nombre que recibirá el archivo en el banco global de secretos (por defecto usa el perfil activo)."
    )
):
    """
    Instala un client_secrets.json en el banco de secretos y lo asocia al perfil actual.
    """
    active_profile = profile_manager.get_active_profile_name()
    name = secret_name or active_profile

    target_name = name if name.endswith(".json") else f"{name}.json"
    target_path = profile_manager.secrets_dir / target_name

    try:
        shutil.copy2(secrets_path, target_path)

        # Actualizar la asociación en los metadatos del perfil
        profile_data = profile_manager.get_active_profile_data()
        profile_data["associated_secret"] = target_name
        profile_manager.save_active_profile_data(profile_data)

        typer.secho(
            f"Secretos '{target_name}' instalados con éxito y asociados al perfil '{active_profile}'.",
            fg=typer.colors.GREEN,
        )
    except Exception as e:
        typer.secho(f"Error al copiar archivo: {e}", fg=typer.colors.RED)
