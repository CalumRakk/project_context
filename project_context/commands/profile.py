import shutil
import time
from pathlib import Path
from typing import Annotated, Optional

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
    secret: Annotated[
        Optional[str],
        typer.Option(
            "--secret",
            "-s",
            help="Nombre del archivo de credenciales a usar si hay múltiples disponibles (ej: credenciales.json).",
        ),
    ] = None,
):
    """
    Crea un nuevo perfil asociándolo a un secreto existente y validándolo de forma atómica.
    """

    if name in profile_manager.list_profiles():
        typer.secho(f"El perfil '{name}' ya existe.", fg=typer.colors.YELLOW)
        return

    available_secrets = sorted(
        [f for f in profile_manager.secrets_dir.glob("*.json") if f.is_file()]
    )
    num_secrets = len(available_secrets)

    if num_secrets == 0:
        typer.secho(
            "Error: No se encontraron archivos de credenciales en el banco de secretos.\n"
            "Para poder crear un perfil, primero debes instalar un archivo de secretos de Google Drive.\n\n"
            "Instrucciones:\n"
            "1. Descarga el JSON de credenciales (OAuth Desktop app) de la consola de Google Cloud.\n"
            "2. Instálalo en el programa ejecutando:\n"
            "   project_context profile set-secrets /ruta/a/tu/archivo.json --secret-name mi_secreto\n",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)

    selected_secret_path: Optional[Path] = None

    if secret:
        target_name = secret if secret.endswith(".json") else f"{secret}.json"
        candidate_path = profile_manager.secrets_dir / target_name
        if not candidate_path.exists():
            typer.secho(
                f"Error: El secreto '{target_name}' no existe en el banco de secretos.\n"
                f"Credenciales instaladas: {', '.join(s.name for s in available_secrets)}",
                fg=typer.colors.RED,
            )
            raise typer.Exit(code=1)
        selected_secret_path = candidate_path

    else:
        if num_secrets == 1:
            selected_secret_path = available_secrets[0]
            typer.echo(
                f"Secreto único detectado de forma automática: {selected_secret_path.name}"
            )

        else:
            secret_names = [s.name for s in available_secrets]
            typer.secho(
                f"Conflicto: Se detectaron {num_secrets} credenciales instaladas, "
                "pero no has especificado cuál utilizar para este perfil.\n\n"
                "Credenciales instaladas disponibles:\n"
                + "\n".join(f"  - {name}" for name in secret_names)
                + "\n\n"
                "Por favor, indica la credencial que deseas utilizar con la opción --secret:\n"
                f"  project_context profile add {name} --secret <nombre_credencial.json>",
                fg=typer.colors.RED,
            )
            raise typer.Exit(code=1)

    assert selected_secret_path is not None
    profile_manager.get_active_profile_name()

    try:
        typer.echo(
            f"Validando inicio de sesión utilizando: {selected_secret_path.name}..."
        )

        from project_context.api_drive import GoogleDriveManager

        gdm = GoogleDriveManager(secrets_file=selected_secret_path, profile_name=name)

        email = gdm.fetched_email

        profile_data = {
            "email": email,
            "associated_secret": selected_secret_path.name,
            "created_at": time.time(),
        }
        profile_manager.save_profile_data(name, profile_data)

        token_name = f"{email}__{selected_secret_path.name}"
        token_path = profile_manager.tokens_dir / token_name
        with open(token_path, "w") as token_fh:
            token_fh.write(gdm.credentials.to_json())

        profile_manager.set_active_profile(name)

        typer.secho(
            f"\n¡Perfil '{name}' ({email}) configurado y activado con éxito!",
            fg=typer.colors.GREEN,
        )

    except Exception as e:
        typer.secho(f"\nError de autenticación: {e}", fg=typer.colors.RED)
        typer.echo("Operación cancelada. No se modificó la configuración del sistema.")


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

    if secrets_path.exists():
        typer.secho("Secreto físico:    Encontrado en disco", fg=typer.colors.GREEN)
    else:
        typer.secho(
            "Secreto físico:    FALTA ARCHIVO (Instala con 'set-secrets')",
            fg=typer.colors.RED,
        )

    if email:
        associated_secret_clean = (
            secret_name if secret_name.endswith(".json") else f"{secret_name}.json"
        )
        token_name = f"{email}__{associated_secret_clean}"
        token_path = profile_manager.tokens_dir / token_name
        if token_path.exists():
            typer.secho(
                f"Token de Acceso:   Sesión activa ({token_name})",
                fg=typer.colors.GREEN,
            )
        else:
            typer.secho(
                "Token de Acceso:   No encontrado (Requiere login en próxima ejecución)",
                fg=typer.colors.YELLOW,
            )
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
        help="Nombre que recibirá el archivo en el banco global de secretos (por defecto usa el perfil activo).",
    ),
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

        profile_data = profile_manager.get_active_profile_data()
        profile_data["associated_secret"] = target_name
        profile_manager.save_active_profile_data(profile_data)

        typer.secho(
            f"Secretos '{target_name}' instalados con éxito y asociados al perfil '{active_profile}'.",
            fg=typer.colors.GREEN,
        )
    except Exception as e:
        typer.secho(f"Error al copiar archivo: {e}", fg=typer.colors.RED)
