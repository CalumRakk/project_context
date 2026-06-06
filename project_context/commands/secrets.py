import shutil
from pathlib import Path
from typing import Annotated, Optional

import typer

from project_context.profiles import ProfileManager
from project_context.utils import (
    UI,
    validate_google_secrets_file,
)

app = typer.Typer(
    help="Gestión del banco global de secretos (OAuth Client Credentials)."
)


@app.command("add")
def add_secret(
    secrets_path: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="Ruta local al archivo client_secrets.json que deseas instalar.",
        ),
    ],
    name: Annotated[
        Optional[str],
        typer.Option(
            "--name",
            "-n",
            help="Nombre que recibirá el archivo en el banco global de secretos (ej: personal).",
        ),
    ] = None,
    associate: Annotated[
        bool,
        typer.Option(
            "--associate",
            "-a",
            help="Asociar automáticamente este secreto al perfil activo actual.",
        ),
    ] = False,
):
    """
    Instala un archivo de secretos OAuth en el banco global de credenciales.
    """
    profile_manager = ProfileManager()
    if not validate_google_secrets_file(secrets_path):
        UI.error(
            "El archivo provisto no tiene una estructura de secretos de Google OAuth válida.\n"
            "Asegúrate de haber descargado la credencial tipo 'Desktop App' desde Google Cloud Console."
        )
        raise typer.Exit(code=1)

    if not name:
        default_name = secrets_path.stem
        name_input = typer.prompt(
            "Ingresa el nombre que deseas darle a este secreto en el sistema",
            default=default_name,
        ).strip()
        name = name_input if name_input else default_name

    assert name is not None
    target_name = name if name.endswith(".json") else f"{name}.json"
    target_path = profile_manager.secrets_dir / target_name

    if target_path.exists():
        confirm_overwrite = typer.confirm(
            f"Ya existe un secreto llamado '{target_name}' in el banco global.\n"
            f"¿Deseas sobrescribirlo con las nuevas credenciales?",
            default=False,
        )
        if not confirm_overwrite:
            UI.info("Operación cancelada. No se han guardado los cambios.")
            raise typer.Exit()

    try:
        shutil.copy2(secrets_path, target_path)
        UI.success(f"Secreto '{target_name}' registrado con éxito en el banco global.")

        if associate:
            active_profile = profile_manager.get_active_profile_name()
            assert active_profile is not None, "Perfil activo no encontrado"

            profile_data = profile_manager.load_profile_data(active_profile)
            profile_data.associated_secret = target_name
            UI.success(
                f"Secreto '{target_name}' asociado al perfil activo '{active_profile}'."
            )

    except Exception as e:
        UI.error(f"Fallo al registrar el secreto en el sistema: {e}")
        raise typer.Exit(code=1)
