import json
from pathlib import Path
from typing import Optional, cast

import typer
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from project_context.exceptions import (
    AuthenticationFailedError,
    FreshInstallRequiredError,
    ProfileConfigNotFoundError,
    ProfileConfigurationCorruptError,
    SecretAssociationMissingError,
    SecretFileMissingError,
)
from project_context.profiles import profile_manager
from project_context.schema import (
    CliSessionPayload,
    ProfileMetadata,
    ValidationRequirement,
)
from project_context.ui.ui import UI


class AuthFlowEvaluator:
    """Implementación limpia y secuencial del diagrama de flujo de autenticación."""

    def __init__(self, requested_profile: Optional[str] = None):
        self.requested_profile: Optional[str] = requested_profile
        self.resolved_profile: Optional[str] = None
        self.profile_data: dict = {}
        self.credentials: Optional[Credentials] = None
        self.secrets_file: Optional[Path] = None

    def execute_preflight(
        self, requirement: ValidationRequirement
    ) -> CliSessionPayload:
        """Sigue de manera estricta los rombos de decisión del diagrama de flujo."""
        if self.requested_profile:
            available_profiles = profile_manager.list_profiles()
            if self.requested_profile not in available_profiles:
                raise ProfileConfigNotFoundError(
                    f"El perfil de usuario '{self.requested_profile}' no existe en este equipo."
                )
            self.resolved_profile = self.requested_profile
        else:
            active_profile = profile_manager.get_active_profile_name()
            if active_profile:
                self.resolved_profile = active_profile
            else:
                available_secrets = list(profile_manager.secrets_dir.glob("*.json"))
                if not available_secrets:
                    raise FreshInstallRequiredError(
                        "No se han detectado perfiles ni credenciales de Google Drive en este sistema."
                    )
                else:
                    raise ProfileConfigNotFoundError(
                        "No hay ningún perfil de usuario activo configurado actualmente."
                    )

        profile_file = profile_manager.profiles_dir / f"{self.resolved_profile}.json"
        try:
            self.profile_data = json.loads(profile_file.read_text(encoding="utf-8"))
        except Exception as e:
            raise ProfileConfigurationCorruptError(
                f"El archivo del perfil '{self.resolved_profile}' está dañado o corrupto: {e}"
            )

        if requirement == ValidationRequirement.PROFILE:
            return CliSessionPayload(
                profile_name=self.resolved_profile,
                profile_data=ProfileMetadata(**self.profile_data),
            )

        creds = self._load_cached_token()

        if creds:
            if self._validate_and_refresh_token(creds):
                self.credentials = creds
                return CliSessionPayload(
                    profile_name=self.resolved_profile,
                    profile_data=ProfileMetadata(**self.profile_data),
                    credentials=self.credentials,
                )

        secret_name = self.profile_data.get("associated_secret")
        if not secret_name:
            raise SecretAssociationMissingError(
                f"El perfil '{self.resolved_profile}' requiere iniciar sesión, "
                f"pero no tiene un archivo de secretos asociado en sus metadatos."
            )

        associated_secret_clean = (
            secret_name if secret_name.endswith(".json") else f"{secret_name}.json"
        )
        self.secrets_file = profile_manager.secrets_dir / associated_secret_clean

        if not self.secrets_file.exists():
            raise SecretFileMissingError(
                f"El secreto asociado '{associated_secret_clean}' no se encuentra físicamente en el disco."
            )

        UI.info("Iniciando flujo de autenticación interactivo en el navegador...")
        try:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(self.secrets_file), ["https://www.googleapis.com/auth/drive"]
            )
            creds = cast(Credentials, flow.run_local_server(port=0))
        except Exception as e:
            raise AuthenticationFailedError(
                f"El flujo de autenticación OAuth interactivo fue cancelado o falló: {e}"
            )

        fetched_email = self._fetch_user_email(creds)
        self._verify_email_consistency(fetched_email)

        self._save_authorized_token(fetched_email, creds)
        self.credentials = creds

        return CliSessionPayload(
            profile_name=self.resolved_profile,
            profile_data=ProfileMetadata(**self.profile_data),
            credentials=self.credentials,
        )

    def _load_cached_token(self) -> Optional[Credentials]:
        email = self.profile_data.get("email")
        secret_name = self.profile_data.get("associated_secret")
        if not email or not secret_name:
            return None

        associated_secret_clean = (
            secret_name if secret_name.endswith(".json") else f"{secret_name}.json"
        )
        token_name = f"{email}__{associated_secret_clean}"
        token_path = profile_manager.tokens_dir / token_name

        if not token_path.exists():
            return None

        try:
            return Credentials.from_authorized_user_file(
                str(token_path), ["https://www.googleapis.com/auth/drive"]
            )
        except Exception:
            return None

    def _validate_and_refresh_token(self, creds: Credentials) -> bool:
        if creds.valid:
            return True

        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())

                email = self.profile_data.get("email")
                secret_name = self.profile_data.get("associated_secret", "")
                associated_secret_clean = (
                    secret_name
                    if secret_name.endswith(".json")
                    else f"{secret_name}.json"
                )

                token_name = f"{email}__{associated_secret_clean}"
                token_path = profile_manager.tokens_dir / token_name
                token_path.write_text(creds.to_json(), encoding="utf-8")

                UI.info("Token de acceso renovado automáticamente de forma segura.")
                return True
            except Exception as e:
                UI.warn(f"No se pudo refrescar el token de forma automática: {e}")

        return False

    def _fetch_user_email(self, creds: Credentials) -> str:
        try:
            temp_service = build("drive", "v3", credentials=creds)
            about_info = temp_service.about().get(fields="user(emailAddress)").execute()
            email = about_info.get("user", {}).get("emailAddress")
            if not email:
                raise ValueError(
                    "La respuesta de Google API no contiene una dirección de correo válida."
                )
            return email
        except Exception as e:
            raise AuthenticationFailedError(
                f"Validación de sesión de usuario fallida: {e}"
            )

    def _verify_email_consistency(self, fetched_email: str) -> None:
        registered_email = self.profile_data.get("email")

        if registered_email and fetched_email.lower() != registered_email.lower():
            raise ValueError(
                f"Conflicto de seguridad: La cuenta de Google autenticada ({fetched_email}) "
                f"no coincide con el correo asignado a este perfil ({registered_email}).\n"
                f"Por favor, cambia de perfil o vuelve a configurar las credenciales."
            )

        if not registered_email:
            self.profile_data["email"] = fetched_email
            assert self.resolved_profile is not None, (
                "El perfil resuelto no puede ser None en este punto del flujo."
            )
            profile_manager.save_profile_data(self.resolved_profile, self.profile_data)
            UI.info(
                f"Correo electrónico [bold]{fetched_email}[/] asociado al perfil '{self.resolved_profile}'."
            )

    def _save_authorized_token(self, email: str, creds: Credentials) -> None:
        secret_name = self.profile_data.get("associated_secret", "")
        associated_secret_clean = (
            secret_name if secret_name.endswith(".json") else f"{secret_name}.json"
        )
        token_name = f"{email}__{associated_secret_clean}"
        token_path = profile_manager.tokens_dir / token_name

        try:
            token_path.write_text(creds.to_json(), encoding="utf-8")
            UI.success(
                "Token de acceso guardado de forma segura para futuras sesiones."
            )
        except Exception as e:
            UI.error(
                f"Fallo al registrar el token de sesión en el almacenamiento local: {e}"
            )


def verify_and_populate_context(
    ctx: typer.Context,
    requirement: ValidationRequirement,
    profile_override: Optional[str] = None,
) -> None:
    """
    Asistente de preflight para el contexto de Typer.
    Analiza el requerimiento, ejecuta el flujo y asiste visualmente ante errores de seguridad.
    """
    if requirement == ValidationRequirement.NONE:
        return

    evaluator = AuthFlowEvaluator(requested_profile=profile_override)

    try:
        payload = evaluator.execute_preflight(requirement)

        if requirement == ValidationRequirement.FULL_AUTH:
            from project_context.api_drive import (
                AIStudioDriveManager,
                GoogleDriveManager,
            )
            from project_context.workspace import ProjectContext

            gdm = GoogleDriveManager(
                secrets_file=evaluator.secrets_file,
                profile_name=payload.profile_name,
                credentials=payload.credentials,
            )
            payload.api = AIStudioDriveManager(gdm=gdm)

            project_path = Path.cwd()
            workspace = ProjectContext(project_path)
            if workspace.is_initialized:
                payload.state = workspace.load_project_context_state()
            else:
                payload.state = None

        ctx.obj = payload

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
        if not profile_override:
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
                f"ERROR: El perfil especificado '{profile_override}' no existe.",
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
                        f"No hay perfiles registrados. Si deseas crear el perfil '{profile_override}' "
                        "y asociarlo a tus credenciales, ejecuta:"
                    ),
                    commands=[f"project_context profile add {profile_override}"],
                    spacing="bottom",
                )
        raise typer.Exit(code=1)

    except SecretAssociationMissingError as e:
        resolved_profile = evaluator.resolved_profile or "default"
        UI.error(str(e), spacing="top")
        UI.educational_tip(
            title="Vincular Secreto Requerido",
            message=(
                f"La sesión para el perfil '{resolved_profile}' requiere iniciar el flujo OAuth, "
                f"pero no tiene asignado un secreto de Google Cloud Console en el sistema."
            ),
            commands=["project_context profile set-secrets /ruta/a/tu/archivo.json"],
            spacing="bottom",
        )
        raise typer.Exit(code=1)

    except SecretFileMissingError as e:
        resolved_profile = evaluator.resolved_profile or "default"
        secret_name = evaluator.profile_data.get(
            "associated_secret", f"{resolved_profile}.json"
        )
        if not secret_name.endswith(".json"):
            secret_name += ".json"

        UI.error(str(e), spacing="top")
        UI.educational_tip(
            title="Archivo de Secreto Faltante",
            message=(
                f"El perfil '{resolved_profile}' tiene asignado el secreto '{secret_name}', "
                f"pero el archivo no se encuentra físicamente en la carpeta de secretos."
            ),
            commands=[
                f"project_context secrets add /ruta/a/tu/archivo.json --name {secret_name.replace('.json', '')}"
            ],
            spacing="bottom",
        )
        raise typer.Exit(code=1)

    except ProfileConfigurationCorruptError as e:
        UI.error(str(e), spacing="top")
        if profile_override:
            UI.educational_tip(
                title="Perfil Corrupto",
                message="La estructura del archivo del perfil no es válida. Puedes restablecerlo creándolo nuevamente.",
                commands=[f"project_context profile add {profile_override}"],
                spacing="bottom",
            )
        raise typer.Exit(code=1)

    except AuthenticationFailedError as e:
        UI.error(str(e), spacing="top")
        raise typer.Exit(code=1)
