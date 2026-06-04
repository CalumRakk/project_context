from typing import NoReturn

import typer

from project_context.exceptions import (
    AuthenticationFailedError,
    FreshInstallRequiredError,
    ProfileConfigCorruptError,
    ProfileConfigNotFoundError,
    ProjectContextError,
    SecretAssociationMissingError,
    SecretFileMissingError,
)
from project_context.profiles import ProfileManager
from project_context.ui.ui import UI


class AuthConsolePresenter:
    """Atiende, formatea e interrumpe la CLI ante errores de sesión."""

    @classmethod
    def handle_error(cls, error: Exception) -> NoReturn:
        if isinstance(error, FreshInstallRequiredError):
            UI.error(
                "No se ha detectado ninguna credencial de Google Drive en este equipo.",
                spacing="top",
            )
            UI.educational_tip(
                title="Configuración Inicial de Credenciales",
                message=(
                    "Para conectar project_context con tu cuenta de Google Drive necesitas un secreto cliente OAuth:\n\n"
                    "1. Accede a la Google Cloud Console (https://console.cloud.google.com/).\n"
                    "2. Habilita la Google Drive API en tu proyecto.\n"
                    "3. Crea credenciales de tipo 'OAuth Client ID' (Desktop App).\n"
                    "4. Descarga el archivo JSON resultante."
                ),
                commands=["project_context secrets add /ruta/a/tus_credenciales.json"],
                spacing="bottom",
            )
            raise typer.Exit(code=1)

        elif isinstance(error, ProfileConfigNotFoundError):
            profile_manager = ProfileManager()
            available_profiles = profile_manager.list_profiles()
            UI.error(str(error), spacing="top")

            if available_profiles:
                UI.educational_tip(
                    title="Selecciona un Perfil Activo",
                    message=(
                        f"Perfiles ya configurados en este equipo: {', '.join(available_profiles)}\n\n"
                        "Para activar uno de estos perfiles, ejecuta:"
                    ),
                    commands=["project_context profile use <nombre_perfil>"],
                    spacing="bottom",
                )
            else:
                UI.educational_tip(
                    title="Crea tu primer perfil",
                    message=(
                        "No se han detectado perfiles creados en este sistema.\n"
                        "Si ya has guardado tu archivo de secretos, puedes crear un perfil ejecutando:"
                    ),
                    commands=["project_context profile add <nombre_perfil>"],
                    spacing="bottom",
                )
            raise typer.Exit(code=1)

        elif isinstance(error, SecretAssociationMissingError):
            UI.error(str(error), spacing="top")
            UI.educational_tip(
                title="Vincular Secreto Requerido",
                message=(
                    "La sesión para este perfil requiere iniciar el flujo OAuth, "
                    "pero no tiene asignado un secreto de Google Cloud Console en el sistema."
                ),
                commands=[
                    "project_context profile set-secrets /ruta/a/tu/archivo.json"
                ],
                spacing="bottom",
            )
            raise typer.Exit(code=1)

        elif isinstance(error, SecretFileMissingError):
            UI.error(str(error), spacing="top")
            UI.educational_tip(
                title="Archivo de Secreto Faltante",
                message=(
                    "El perfil tiene asignado un secreto en sus metadatos, "
                    "pero el archivo no se encuentra físicamente en la carpeta de secretos."
                ),
                commands=["project_context secrets add /ruta/a/tu/archivo.json"],
                spacing="bottom",
            )
            raise typer.Exit(code=1)

        elif isinstance(error, ProfileConfigCorruptError):
            UI.error(str(error), spacing="top")
            raise typer.Exit(code=1)

        elif isinstance(error, AuthenticationFailedError):
            UI.error(str(error), spacing="top")
            raise typer.Exit(code=1)

        elif isinstance(error, ValueError) and "Conflicto de seguridad" in str(error):
            UI.error(str(error), spacing="top")
            raise typer.Exit(code=1)

        elif isinstance(error, ProjectContextError):
            UI.error(str(error), spacing="top")
            raise typer.Exit(code=1)

        UI.error(f"Error crítico en la inicialización: {error}", spacing="top")
        raise typer.Exit(code=1)
