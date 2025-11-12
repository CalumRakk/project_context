import hashlib
import io
import json
import ssl
import time
from pathlib import Path
from typing import Optional, cast

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from pydantic import BaseModel

from project_context.schema import ChatIAStudio, FileDrive


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


class DownloadedFile(BaseModel):
    content: bytes
    mime_type: str
    name: str


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

    def get_file_metadata(self, file_id: str) -> dict | None:
        """
        Obtiene la metadata de un archivo por su ID.
        Campos importantes para v3: 'name', 'mimeType', 'modifiedTime', 'id'.
        """
        try:
            # Especificar los campos que queremos para eficiencia.
            file_metadata = (
                self.drive_service.files()
                .get(
                    fileId=file_id,
                    fields="id, name, mimeType, modifiedTime, webViewLink, webContentLink",
                )
                .execute()
            )
            return file_metadata
        except HttpError as error:
            if error.resp.status == 404:
                print(f"Error: Archivo con ID '{file_id}' no encontrado.")
            else:
                print(f"Error al obtener metadata del archivo '{file_id}': {error}")
            return None
        except Exception as e:
            print(f"Error inesperado al obtener metadata del archivo '{file_id}': {e}")
            return None

    def get_file_content(self, file_id: str) -> bytes | None:
        """
        Descarga el contenido de un archivo de Google Drive.

        Args:
            file_id (str): El ID del archivo en Drive.

        Returns:
            bytes | None: El contenido del archivo como bytes, o None si hubo un error.
        """
        try:
            request = self.drive_service.files().get_media(fileId=file_id)
            file_stream = io.BytesIO()
            downloader = MediaIoBaseDownload(file_stream, request)
            done = False
            retries = 0
            max_retries = 5

            while not done and retries < max_retries:
                try:
                    status, done = downloader.next_chunk()
                    print(f"Descargando... {int(status.progress() * 100)}%")
                except ssl.SSLEOFError:
                    retries += 1
                    print(
                        f"SSLEOFError al descargar. Reintentando ({retries}/{max_retries})..."
                    )
                    time.sleep(3 * retries)  # Espera exponencial
                except Exception as e:
                    print(f"Error inesperado durante la descarga: {e}")
                    return None

            if not done:
                print("La descarga falló después de múltiples reintentos.")
                return None

            return file_stream.getvalue()

        except HttpError as error:
            print(f"Error HTTP al descargar archivo '{file_id}': {error}")
            return None
        except Exception as e:
            print(f"Error inesperado al descargar archivo '{file_id}': {e}")
            return None

    def download_file_with_metadata(self, file_id: str) -> DownloadedFile | None:
        """
        Descarga un archivo y devuelve su contenido en bytes junto con sus metadatos.

        Args:
            file_id (str): El ID del archivo en Drive.

        Returns:
            DownloadedFile | None: Un objeto con el contenido, mime_type y nombre,
                                  o None si ocurre un error o es una carpeta.
        """
        metadata = self.get_file_metadata(file_id)
        if not metadata:
            return None

        if metadata.get("mimeType") == "application/vnd.google-apps.folder":
            print(
                f"Error: El ID '{file_id}' corresponde a una carpeta, la cual no se puede descargar."
            )
            return None

        content = self.get_file_content(file_id)
        if content is None:
            return None

        return DownloadedFile(
            content=content,
            mime_type=metadata.get("mimeType", "application/octet-stream"),
            name=metadata.get("name", "unknown"),
        )

    def get_chat_ia_studio(self, chat_id: str) -> ChatIAStudio | None:
        """
        Obtiene el contenido JSON asociado a un chat ID.

        Args:
            chat_id (str): El ID del chat.

        Returns:
            dict | None: El contenido JSON del chat, o None si hubo un error.
        """
        chat_content_bytes = self.get_file_content(chat_id)
        if not chat_content_bytes:
            print(f"No se pudo obtener el contenido del chat con ID '{chat_id}'.")
            return None

        try:
            chat_content = json.loads(chat_content_bytes.decode("utf-8"))
            return ChatIAStudio(**chat_content)
        except json.JSONDecodeError as e:
            print(f"Error al decodificar el contenido JSON del chat '{chat_id}': {e}")
            return None

    def update_file_content(
        self, file_id: str, local_path: Path, mime_type: str | None = None
    ) -> str | None:
        """
        Actualiza el contenido de un archivo existente en Google Drive.

        Args:
            file_id (str): El ID del archivo en Drive a actualizar.
            local_path (Path): La ruta al archivo local con el nuevo contenido.
            mime_type (str | None): Opcional. El nuevo tipo MIME si cambia.

        Returns:
            str | None: El ID del archivo actualizado en Drive, o None si hubo un error.
        """
        if not local_path.exists():
            print(f"Error: El archivo local '{local_path}' no existe para actualizar.")
            return None

        # Obtener el MIME type actual si no se proporciona uno nuevo
        if mime_type is None:
            metadata = self.get_file_metadata(file_id)
            if metadata:
                mime_type = metadata.get("mimeType", "application/octet-stream")
            else:
                print(
                    f"Advertencia: No se pudo obtener el mimeType del archivo Drive '{file_id}'. Usando 'application/octet-stream'."
                )
                mime_type = "application/octet-stream"

        file_metadata_body = {
            "name": local_path.name,
        }

        try:
            media = MediaFileUpload(
                local_path.resolve(), mimetype=mime_type, resumable=True
            )
            updated_file = (
                self.drive_service.files()
                .update(
                    fileId=file_id,
                    media_body=media,
                    body=file_metadata_body,
                    fields="id, name, modifiedTime",
                )
                .execute()
            )
            print(
                f'Archivo actualizado: "{updated_file.get("name")}" (ID: "{updated_file.get("id")}", Última modificación: {updated_file.get("modifiedTime")}).'
            )
            return updated_file.get("id")
        except HttpError as error:
            print(f"Error al actualizar archivo '{file_id}': {error}")
            return None
        except Exception as e:
            print(f"Error inesperado al actualizar archivo: {e}")
            return None
