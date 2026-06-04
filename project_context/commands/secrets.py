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


# @app.command("list")
# def list_secrets():
#     """
#     Muestra los secretos instalados en el sistema y los perfiles que dependen de ellos.
#     """
#     association_map = profile_manager.get_secrets_association_map()

#     if not association_map:
#         UI.info(
#             "No se han encontrado secretos registrados en el banco de credenciales."
#         )
#         return

#     table = Table(
#         title="\nBanco Global de Secretos",
#         show_header=True,
#         header_style="bold magenta",
#     )
#     table.add_column("Nombre del Secreto", style="cyan")
#     table.add_column("Estado Físico", style="green")
#     table.add_column("Perfiles que lo utilizan", style="yellow")

#     for secret_name, data in association_map.items():
#         status_str = (
#             "Encontrado" if data["exists_on_disk"] else "[bold red]FALTANTE EN DISCO[/]"
#         )
#         profiles_list = data["associated_profiles"]
#         profiles_str = (
#             ", ".join(profiles_list)
#             if profiles_list
#             else "Sin perfiles vinculados (Huérfano)"
#         )

#         table.add_row(secret_name, status_str, profiles_str)

#     console.print(table)
#     console.print("")


# @app.command("remove")
# def remove_secret(
#     name: Annotated[
#         str,
#         typer.Argument(
#             help="Nombre del secreto que deseas eliminar (ej: personal o personal.json)."
#         ),
#     ],
# ):
#     """
#     Elimina un archivo de secretos del sistema de forma segura.
#     """
#     target_name = name if name.endswith(".json") else f"{name}.json"
#     association_map = profile_manager.get_secrets_association_map()

#     if target_name not in association_map:
#         UI.error(f"El secreto '{target_name}' no está registrado en el sistema.")
#         raise typer.Exit(code=1)

#     secret_data = association_map[target_name]

#     associated_profiles = secret_data["associated_profiles"]
#     if associated_profiles:
#         UI.warn(
#             f"⚠️ El secreto '{target_name}' está siendo utilizado por los siguientes perfiles:\n"
#             f"   - {', '.join(associated_profiles)}\n\n"
#             f"Si lo eliminas, estos perfiles no podrán renovar sus sesiones de Google Drive "
#             f"hasta que se les asocie otro secreto válido."
#         )
#         confirm = typer.confirm(
#             f"¿Estás seguro de que deseas eliminar permanentemente '{target_name}'?",
#             default=False,
#         )
#         if not confirm:
#             UI.info("Eliminación cancelada.")
#             raise typer.Exit()
#     else:
#         confirm = typer.confirm(
#             f"¿Deseas eliminar permanentemente el secreto '{target_name}'?",
#             default=True,
#         )
#         if not confirm:
#             UI.info("Eliminación cancelada.")
#             raise typer.Exit()

#     # Proceder con la eliminación física del archivo
#     file_path = secret_data["path"]
#     try:
#         if file_path.exists():
#             file_path.unlink()
#             UI.success(f"Archivo de credenciales '{target_name}' eliminado del disco.")

#         # Eliminar tokens de acceso residuales asociados a este secreto
#         removed_tokens = profile_manager.remove_tokens_for_secret(target_name)
#         if removed_tokens > 0:
#             UI.info(f"Se limpiaron {removed_tokens} token(s) de sesión residual(es).")

#     except Exception as e:
#         UI.error(f"Ocurrió un error al intentar eliminar el recurso: {e}")
#         raise typer.Exit(code=1)
