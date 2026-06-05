import json
import logging
from pathlib import Path
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from project_context.exceptions import (
    AuthenticationFailedError,
    ProfileTokenNotFoundError,
)

logger = logging.getLogger(__name__)


class AuthService:
    """
    Servicio de bajo nivel para interactuar con la autenticación de Google.
    No maneja lógica de archivos locales ni configuración de perfiles.
    """

    SCOPES = ["https://www.googleapis.com/auth/drive"]

    @classmethod
    def authenticate(
        cls, token_path: str | Path, secret_path: str | Path
    ) -> Credentials:
        credentials = cls.load_credentials_from_file(token_path)
        if credentials and cls.refresh_credentials(credentials):
            return credentials

        credentials = cls.run_oauth_flow(secret_path)
        return cls.save_token(token_path, credentials)

    @classmethod
    def load_credentials_from_dict(cls, token_data: dict) -> Optional[Credentials]:
        """
        Construye las credenciales a partir de un diccionario de datos (token serializado).
        """
        try:
            return Credentials.from_authorized_user_info(token_data, cls.SCOPES)
        except Exception as e:
            logger.debug(
                f"No se pudieron cargar las credenciales desde el diccionario: {e}"
            )
            return None

    @classmethod
    def load_credentials_from_file(
        cls, token_path: Path | str
    ) -> Optional[Credentials]:
        token_path = Path(token_path)
        if not token_path.exists():
            raise ProfileTokenNotFoundError(
                f"El archivo de token de perfil '{token_path}' no existe."
            )

        data = token_path.read_text(encoding="utf-8")
        return cls.load_credentials_from_dict(json.loads(data))

    @classmethod
    def refresh_credentials(cls, creds: Credentials) -> bool:
        """
        Intenta refrescar las credenciales si están expiradas.
        Retorna True si son válidas (o se refrescaron exitosamente), False de lo contrario.
        """
        if creds.valid:
            return True

        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                return True
            except Exception as e:
                logger.debug(f"Fallo al refrescar token automáticamente: {e}")

        return False

    @classmethod
    def run_oauth_flow(cls, client_secrets_path: str | Path) -> Credentials:
        """
        Inicia el servidor local OAuth interactivo de Google.
        Requiere la ruta absoluta al archivo client_secrets.json de Google.
        """
        try:
            client_secrets_path = Path(client_secrets_path)
            flow = InstalledAppFlow.from_client_secrets_file(
                client_secrets_path, cls.SCOPES
            )
            return flow.run_local_server(port=0)  # type: ignore
        except Exception as e:
            raise AuthenticationFailedError(
                f"El flujo de autenticación OAuth interactivo fue cancelado o falló: {e}"
            )

    @classmethod
    def fetch_user_email(cls, creds: Credentials) -> str:
        """
        Consulta el correo electrónico de la cuenta activa en Google Drive usando credenciales válidas.
        """
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
                f"Error al consultar la identidad del usuario en Google Drive: {e}"
            )

    @classmethod
    def verify_email_consistency(cls, expected_email: str, fetched_email: str) -> None:
        """
        Compara el correo esperado con el correo autenticado y lanza un error si no coinciden.
        """
        if expected_email.lower() != fetched_email.lower():
            raise ValueError(
                f"Conflicto de seguridad: La cuenta autenticada ({fetched_email}) "
                f"no coincide con el correo asignado o esperado ({expected_email}).\n"
                f"Por favor, asegúrate de iniciar sesión con la cuenta correcta."
            )

    @classmethod
    def load_token(cls, token_path: Path | str) -> dict:
        token_path = Path(token_path)
        if not token_path.exists():
            raise ProfileTokenNotFoundError(
                f"El archivo de token de perfil '{token_path}' no existe."
            )

        data = token_path.read_text(encoding="utf-8")
        return json.loads(data)

    @classmethod
    def save_token(
        cls, token_path: Path | str, credentials: Credentials
    ) -> Credentials:
        token_path = Path(token_path)
        token_path.write_text(credentials.to_json(), encoding="utf-8")
        return credentials
