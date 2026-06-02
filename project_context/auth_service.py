import json
import logging
from pathlib import Path
from typing import Any, Optional, Tuple

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

logger = logging.getLogger(__name__)


class AuthSession:
    """Contenedor simple de datos para transportar la sesión inicializada."""

    def __init__(
        self,
        profile_name: str,
        api: Any,  # AIStudioDriveManager
        credentials: Credentials,
        state: Optional[Any] = None,  # ProjectState
    ):
        self.profile_name = profile_name
        self.api = api
        self.credentials = credentials
        self.state = state


class AuthService:
    SCOPES = ["https://www.googleapis.com/auth/drive"]

    @classmethod
    def initialize_session(
        cls,
        profile_override: Optional[str] = None,
        project_path: Optional[Path] = None,
    ) -> AuthSession:
        """
        Orquesta y valida explícitamente el flujo de autenticación.
        Lanza excepciones de dominio específicas ante fallos lógicos o de seguridad.
        """
        # 1. Resolver el perfil activo o especificado
        profile_name = cls._resolve_profile_name(profile_override)

        # 2. Cargar metadatos del perfil
        profile_data = cls._load_profile_data(profile_name)

        # 3. Validar credenciales o ejecutar el flujo de inicio de sesión
        credentials, secrets_file = cls._authenticate_profile(
            profile_name, profile_data
        )

        # 4. Inicializar los administradores de Google Drive de manera interna
        from project_context.api_drive import AIStudioDriveManager, GoogleDriveManager

        gdm = GoogleDriveManager(
            secrets_file=secrets_file,
            profile_name=profile_name,
            credentials=credentials,
        )
        api = AIStudioDriveManager(gdm=gdm)

        # 5. Intentar resolver el estado del workspace si se provee una ruta de proyecto
        state = None
        if project_path:
            from project_context.workspace import ProjectContext

            workspace = ProjectContext(project_path)
            if workspace.is_initialized:
                state = workspace.load_project_context_state()

        return AuthSession(
            profile_name=profile_name,
            api=api,
            credentials=credentials,
            state=state,
        )

    @classmethod
    def _resolve_profile_name(cls, override: Optional[str]) -> str:
        if override:
            available = profile_manager.list_profiles()
            if override not in available:
                raise ProfileConfigNotFoundError(
                    f"El perfil de usuario '{override}' no existe en este equipo."
                )
            return override

        active = profile_manager.get_active_profile_name()
        if active:
            return active

        available_secrets = list(profile_manager.secrets_dir.glob("*.json"))
        if not available_secrets:
            raise FreshInstallRequiredError(
                "No se han detectado perfiles ni credenciales de Google Drive en este sistema."
            )
        else:
            raise ProfileConfigNotFoundError(
                "No hay ningún perfil de usuario activo configurado actualmente."
            )

    @classmethod
    def _load_profile_data(cls, profile_name: str) -> dict:
        profile_file = profile_manager.profiles_dir / f"{profile_name}.json"
        try:
            return json.loads(profile_file.read_text(encoding="utf-8"))
        except Exception as e:
            raise ProfileConfigurationCorruptError(
                f"El archivo del perfil '{profile_name}' está dañado o corrupto: {e}"
            )

    @classmethod
    def _authenticate_profile(
        cls, profile_name: str, profile_data: dict
    ) -> Tuple[Credentials, Path]:
        # Intentar cargar credenciales del caché local (token guardado)
        creds = cls._load_cached_token(profile_data)

        if creds and cls._validate_and_refresh_token(creds, profile_data, profile_name):
            return creds, cls._get_secrets_file_path(profile_name, profile_data)

        # No hay credenciales válidas en caché. Se requiere flujo OAuth interactivo.
        secrets_file = cls._get_secrets_file_path(profile_name, profile_data)
        creds = cls._run_oauth_flow(secrets_file)

        fetched_email = cls._fetch_user_email(creds)
        cls._verify_email_consistency(profile_name, profile_data, fetched_email)
        cls._save_authorized_token(profile_name, profile_data, fetched_email, creds)

        return creds, secrets_file

    @classmethod
    def _load_cached_token(cls, profile_data: dict) -> Optional[Credentials]:
        email = profile_data.get("email")
        secret_name = profile_data.get("associated_secret")
        if not email or not secret_name:
            return None

        clean_secret = (
            secret_name if secret_name.endswith(".json") else f"{secret_name}.json"
        )
        token_path = profile_manager.tokens_dir / f"{email}__{clean_secret}"

        if not token_path.exists():
            return None

        try:
            return Credentials.from_authorized_user_file(str(token_path), cls.SCOPES)
        except Exception:
            return None

    @classmethod
    def _validate_and_refresh_token(
        cls, creds: Credentials, profile_data: dict, profile_name: str
    ) -> bool:
        if creds.valid:
            return True

        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                email = profile_data.get("email")
                secret_name = profile_data.get("associated_secret", "")
                clean_secret = (
                    secret_name
                    if secret_name.endswith(".json")
                    else f"{secret_name}.json"
                )

                token_path = profile_manager.tokens_dir / f"{email}__{clean_secret}"
                token_path.write_text(creds.to_json(), encoding="utf-8")
                return True
            except Exception as e:
                logger.debug(
                    f"Fallo al refrescar token automáticamente para {profile_name}: {e}"
                )

        return False

    @classmethod
    def _get_secrets_file_path(cls, profile_name: str, profile_data: dict) -> Path:
        secret_name = profile_data.get("associated_secret")
        if not secret_name:
            raise SecretAssociationMissingError(
                f"El perfil '{profile_name}' requiere iniciar sesión, "
                f"pero no tiene un archivo de secretos asociado en sus metadatos."
            )

        clean_secret = (
            secret_name if secret_name.endswith(".json") else f"{secret_name}.json"
        )
        secrets_file = profile_manager.secrets_dir / clean_secret

        if not secrets_file.exists():
            raise SecretFileMissingError(
                f"No se encontró el archivo de credenciales '{clean_secret}' "
                f"asignado al perfil '{profile_name}'."
            )

        return secrets_file

    @classmethod
    def _run_oauth_flow(cls, secrets_file: Path) -> Credentials:
        try:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(secrets_file), cls.SCOPES
            )
            return flow.run_local_server(port=0)  # type: ignore
        except Exception as e:
            raise AuthenticationFailedError(
                f"El flujo de autenticación OAuth interactivo fue cancelado o falló: {e}"
            )

    @classmethod
    def _fetch_user_email(cls, creds: Credentials) -> str:
        try:
            temp_service = build("drive", "v3", credentials=creds)
            about_info = temp_service.about().get(fields="user(emailAddress)").execute()
            email = about_info.get("user", {}).get("emailAddress")
            if not email:
                raise ValueError(
                    "La API de Google no devolvió una dirección de correo válida."
                )
            return email
        except Exception as e:
            raise AuthenticationFailedError(
                f"Error al verificar la identidad del usuario: {e}"
            )

    @classmethod
    def _verify_email_consistency(
        cls, profile_name: str, profile_data: dict, fetched_email: str
    ) -> None:
        registered_email = profile_data.get("email")

        if registered_email and fetched_email.lower() != registered_email.lower():
            raise ValueError(
                f"Conflicto de seguridad: La cuenta autenticada ({fetched_email}) "
                f"no coincide con el correo asignado a este perfil ({registered_email}).\n"
                f"Cambia de perfil o vuelve a configurar las credenciales."
            )

        if not registered_email:
            profile_data["email"] = fetched_email
            profile_manager.save_profile_data(profile_name, profile_data)

    @classmethod
    def _save_authorized_token(
        cls, profile_name: str, profile_data: dict, email: str, creds: Credentials
    ) -> None:
        secret_name = profile_data.get("associated_secret", "")
        clean_secret = (
            secret_name if secret_name.endswith(".json") else f"{secret_name}.json"
        )
        token_path = profile_manager.tokens_dir / f"{email}__{clean_secret}"

        try:
            token_path.write_text(creds.to_json(), encoding="utf-8")
        except Exception as e:
            logger.error(f"Fallo al registrar token de sesión para {profile_name}: {e}")
