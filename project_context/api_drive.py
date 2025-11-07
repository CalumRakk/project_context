import hashlib
from pathlib import Path
from typing import Optional, cast

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from project_context.schema import FileDrive


def generate_md5(content: str) -> str:
    """
    Genera el hash MD5 de una cadena de texto.

    Args:
        content (str): La cadena de texto.

    Returns:
        str: El hash MD5.
    """
    md5_hash = hashlib.md5()
    md5_hash.update(content.encode("utf-8"))
    return md5_hash.hexdigest()


class GoogleDriveManager:
    # Alcance de acceso completo a Google Drive
    SCOPES = ["https://www.googleapis.com/auth/drive"]
    CLIENT_SECRETS_FILE = "client_secrets.json"
    TOKEN_FILE = "token.json"

    def __init__(self):
        self.credentials = self._authenticate()
        self.drive_service = build("drive", "v3", credentials=self.credentials)
        print("Google Drive Manager inicializado con éxito.")

    def _authenticate(self) -> Credentials:
        """
        Gestiona el proceso de autenticación de OAuth 2.0.
        Carga credenciales existentes o realiza un nuevo flujo de autorización.
        """
        creds: Optional[Credentials] = None
        # El archivo token.json almacena los tokens de acceso y refresco del usuario.
        # Se crea automáticamente cuando el flujo de autorización se completa por primera vez.
        if Path(self.TOKEN_FILE).exists():
            creds = Credentials.from_authorized_user_file(self.TOKEN_FILE, self.SCOPES)

        # Si no hay credenciales válidas (o no existen), inicia el flujo de autorización.
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    print("Credenciales de Drive refrescadas.")
                except Exception as e:
                    print(
                        f"Error al refrescar credenciales: {e}. Se requiere re-autenticación."
                    )
                    creds = None  # Forzar nueva autenticación si falla el refresh

            if not creds:
                if not Path(self.CLIENT_SECRETS_FILE).exists():
                    raise FileNotFoundError(
                        f"El archivo '{self.CLIENT_SECRETS_FILE}' no se encontró. "
                        "Por favor, descárgalo de Google Cloud Console."
                    )
                print("Iniciando flujo de autenticación de Google Drive...")
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.CLIENT_SECRETS_FILE, self.SCOPES
                )
                creds = cast(Credentials, flow.run_local_server(port=0))
                print("Autenticación completada.")

            # Guarda las credenciales para la próxima vez
            with open(self.TOKEN_FILE, "w") as token:
                token.write(creds.to_json())
            print(f"Credenciales guardadas en '{self.TOKEN_FILE}'.")

        return creds

    def list_files_in_folder(self, folder_id: str) -> list[FileDrive]:
        """
        Lista los archivos y carpetas dentro de una carpeta específica de Google Drive.

        Args:
            folder_id (str): El ID de la carpeta.

        Returns:
            list[dict]: Una lista de diccionarios con la metadata de los archivos.
        """
        raw_results = []
        results: list[FileDrive] = []

        page_token = None
        try:
            while True:
                # v3 usa 'files', 'name', 'modifiedTime'
                response = (
                    self.drive_service.files()
                    .list(
                        q=f"'{folder_id}' in parents and trashed = false",
                        spaces="drive",
                        fields="nextPageToken, files(id, name, mimeType, modifiedTime)",
                        pageToken=page_token,
                    )
                    .execute()
                )
                raw_results.extend(response.get("files", []))
                page_token = response.get("nextPageToken", None)
                if not page_token:
                    break

            print(f"Archivos encontrados en la carpeta '{folder_id}':")
            for file in raw_results:
                is_folder = file["mimeType"] == "application/vnd.google-apps.folder"
                is_app_folder = file.get("isAppFolder", False)

                file_type = (
                    "Carpeta de App"
                    if is_app_folder
                    else ("Carpeta" if is_folder else "Archivo")
                )
                print(
                    f"  - [{file_type}] '{file['name']}' (ID: {file['id']}, AppFolder: {is_app_folder})"
                )

                results.append(
                    FileDrive(
                        id=file["id"],
                        name=file["name"],
                        mimeType=file["mimeType"],
                        modifiedTime=file["modifiedTime"],
                        is_folder=is_folder,
                    )
                )

            return results
        except HttpError as error:
            print(f"Error al listar archivos en la carpeta '{folder_id}': {error}")
            return []
        except Exception as e:
            print(f"Error inesperado al listar archivos: {e}")
            return []

    def _find_folder_by_name(self, folder_name: str) -> Optional[FileDrive]:
        folders = [i for i in self.list_files_in_folder("root") if i.is_folder]
        for folder in folders:
            if folder.name == folder_name:
                return folder
        return None

    def list_files_google_ia_studio(self):
        folder_id = self._find_folder_by_name("Google AI Studio")
        if folder_id:
            return self.list_files_in_folder(folder_id.id)
