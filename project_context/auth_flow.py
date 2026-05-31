import json
from typing import Optional

import typer
from google.oauth2.credentials import Credentials

from project_context.exceptions import (
    AssociatedSecretMissingError,
    FreshInstallRequiredError,
    ProfileConfigNotFoundError,
    ProfileConfigurationCorruptError,
)
from project_context.profiles import profile_manager
from project_context.ui.ui import UI


class AuthContext:
    """Mantiene el estado de la validación actual del flujo de autenticación."""

    def __init__(self, requested_profile: Optional[str] = None):
        self.requested_profile: Optional[str] = requested_profile
        self.resolved_profile: Optional[str] = None
        self.profile_data: dict = {}
        self.credentials: Optional[Credentials] = None
        self.has_usable_token: bool = False


class AuthFlowEvaluator:
    """Implementación limpia de la secuencia de comprobaciones de credenciales."""

    def __init__(self, context: AuthContext):
        self.ctx = context
        self.pm = profile_manager

    def evaluate_static_preflight(self) -> None:
        """
        Ejecuta secuencialmente las validaciones estáticas del diagrama.
        Lanza excepciones específicas del dominio ante fallos de configuración.
        """
        self._resolve_profile_step()
        self._evaluate_token_step()

        # Si el token ya está autenticado y listo, finalizamos con éxito
        if self.ctx.has_usable_token:
            return

        self._verify_secret_association_step()
        self._verify_secret_file_exists_step()

    def _resolve_profile_step(self):
        """Rombos: ¿Se especificó perfil? -> ¿Existe perfil? -> ¿Hay perfil activo?"""
        available_profiles = self.pm.list_profiles()

        if self.ctx.requested_profile:
            if self.ctx.requested_profile not in available_profiles:
                raise ProfileConfigNotFoundError(
                    f"El perfil de usuario '{self.ctx.requested_profile}' no existe en este equipo."
                )
            self.ctx.resolved_profile = self.ctx.requested_profile
        else:
            active_profile = self.pm.get_active_profile_name()
            if not active_profile:
                available_secrets = list(self.pm.secrets_dir.glob("*.json"))
                if not available_profiles and not available_secrets:
                    raise FreshInstallRequiredError(
                        "No se han detectado perfiles ni credenciales de Google Drive configuradas."
                    )
                raise ProfileConfigNotFoundError(
                    "No hay ningún perfil de usuario activo configurado actualmente."
                )
            self.ctx.resolved_profile = active_profile

        # Cargar los datos del perfil validado
        profile_file = self.pm.profiles_dir / f"{self.ctx.resolved_profile}.json"
        try:
            self.ctx.profile_data = json.loads(profile_file.read_text(encoding="utf-8"))
        except Exception as e:
            raise ProfileConfigurationCorruptError(
                f"El archivo del perfil '{self.ctx.resolved_profile}' está dañado o corrupto: {e}"
            )

    def _evaluate_token_step(self):
        """Rombos: ¿Existe token de sesión? -> ¿Se puede usar o refrescar?"""
        email = self.ctx.profile_data.get("email")
        secret_name = self.ctx.profile_data.get("associated_secret")

        if not email or not secret_name:
            self.ctx.has_usable_token = False
            return

        associated_secret_clean = (
            secret_name if secret_name.endswith(".json") else f"{secret_name}.json"
        )
        token_name = f"{email}__{associated_secret_clean}"
        token_path = self.pm.tokens_dir / token_name

        if token_path.exists():
            try:
                creds = Credentials.from_authorized_user_file(str(token_path))
                if creds.valid or (creds.expired and creds.refresh_token):
                    self.ctx.credentials = creds
                    self.ctx.has_usable_token = True
            except Exception:
                self.ctx.has_usable_token = False

    def _verify_secret_association_step(self):
        """Rombo: ¿Tiene secreto asociado?"""
        secret_name = self.ctx.profile_data.get("associated_secret")
        if not secret_name:
            raise AssociatedSecretMissingError(
                f"El perfil '{self.ctx.resolved_profile}' requiere re-autenticarse, "
                f"pero no tiene un archivo de secretos asociado."
            )

    def _verify_secret_file_exists_step(self):
        """Rombo: ¿Existe el secreto?"""
        secret_name = self.ctx.profile_data.get("associated_secret", "")
        associated_secret_clean = (
            secret_name if secret_name.endswith(".json") else f"{secret_name}.json"
        )
        associated_secret_path = self.pm.secrets_dir / associated_secret_clean

        if not associated_secret_path.exists():
            raise AssociatedSecretMissingError(
                f"El perfil '{self.ctx.resolved_profile}' requiere re-autenticarse, pero su secreto asociado "
                f"'{associated_secret_clean}' no se encuentra físicamente en el disco."
            )


def safe_verify_profile(profile_name: Optional[str] = None) -> None:
    """
    Envoltura interactiva para capturar los fallos del flujo y asitir al usuario.
    Centraliza el UX de la interfaz CLI.
    """
    ctx = AuthContext(profile_name)
    evaluator = AuthFlowEvaluator(ctx)

    try:
        evaluator.evaluate_static_preflight()
    except FreshInstallRequiredError:
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

    except ProfileConfigNotFoundError as e:
        available_profiles = profile_manager.list_profiles()
        if not profile_name:
            UI.error(str(e), spacing="top")
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
        else:
            UI.error(
                f"ERROR: El perfil especificado '{profile_name}' no existe.",
                spacing="top",
            )
            if available_profiles:
                UI.educational_tip(
                    title="Perfiles de Usuario Disponibles",
                    message=(
                        f"Perfiles configurados en este equipo: {', '.join(available_profiles)}\n\n"
                        f"Si deseas cambiar a uno de ellos, ejecuta:"
                    ),
                    commands=["project_context profile use <nombre_perfil>"],
                    spacing="bottom",
                )
            else:
                UI.educational_tip(
                    title="Crea el perfil especificado",
                    message=(
                        f"No hay perfiles registrados. Si deseas crear el perfil '{profile_name}' "
                        "y asociarlo a tus credenciales, ejecuta:"
                    ),
                    commands=[f"project_context profile add {profile_name}"],
                    spacing="bottom",
                )
        raise typer.Exit(code=1)

    except AssociatedSecretMissingError as e:
        resolved_profile = ctx.resolved_profile or "default"
        profile_data = profile_manager.load_profile_data(resolved_profile)
        secret_name = profile_data.get("associated_secret", f"{resolved_profile}.json")
        if not secret_name.endswith(".json"):
            secret_name += ".json"

        UI.error(str(e), spacing="top")
        UI.educational_tip(
            title="Vincular Secreto Requerido",
            message=(
                f"La sesión para el perfil '{resolved_profile}' requiere iniciar el flujo OAuth en el navegador, "
                f"pero se necesita el archivo de secretos físicos '{secret_name}' en el banco global."
            ),
            commands=[
                f"project_context secrets add /ruta/a/tu/archivo.json --name {secret_name.replace('.json', '')}"
            ],
            spacing="bottom",
        )
        raise typer.Exit(code=1)

    except ProfileConfigurationCorruptError as e:
        UI.error(str(e), spacing="top")
        if profile_name:
            UI.educational_tip(
                title="Perfil Corrupto",
                message=(
                    "La estructura del archivo del perfil no es válida. Puedes restablecerlo "
                    "creándolo nuevamente."
                ),
                commands=[f"project_context profile add {profile_name}"],
                spacing="bottom",
            )
        raise typer.Exit(code=1)
        raise typer.Exit(code=1)
