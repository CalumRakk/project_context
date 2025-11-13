import io
import json
from pathlib import Path
from typing import Optional, cast

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

from project_context.schema import ChatIAStudio


class GoogleDriveManager:
    SCOPES = ["https://www.googleapis.com/auth/drive"]
    CLIENT_SECRETS_FILE = "client_secrets.json"
    TOKEN_FILE = "token.json"

    def __init__(self):
        """Inicializa el cliente de Drive y se autentica."""
        self.credentials = self._authenticate()
        self.service = build("drive", "v3", credentials=self.credentials)
        print("Google Drive Manager inicializado con éxito.")

    def _authenticate(self) -> Credentials:
        """
        Gestiona el proceso de autenticación de OAuth 2.0.
        Carga credenciales existentes o realiza un nuevo flujo de autorización.
        """
        creds: Optional[Credentials] = None
        if Path(self.TOKEN_FILE).exists():
            creds = Credentials.from_authorized_user_file(self.TOKEN_FILE, self.SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    print("Credenciales de Drive refrescadas.")
                except Exception as e:
                    print(
                        f"Error al refrescar credenciales: {e}. Se requiere re-autenticación."
                    )
                    creds = None

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

            with open(self.TOKEN_FILE, "w") as token:
                token.write(creds.to_json())
            print(f"Credenciales guardadas en '{self.TOKEN_FILE}'.")

        return creds

    def list_files(self, folder_id: str = "root") -> list[dict]:
        """Lista archivos y carpetas en una carpeta específica, devolviendo los datos crudos."""
        items = []
        page_token = None
        try:
            while True:
                response = (
                    self.service.files()
                    .list(
                        q=f"'{folder_id}' in parents and trashed = false",
                        spaces="drive",
                        fields="nextPageToken, files(id, name, mimeType, modifiedTime)",
                        pageToken=page_token,
                    )
                    .execute()
                )
                items.extend(response.get("files", []))
                page_token = response.get("nextPageToken")
                if not page_token:
                    break
            return items
        except HttpError as error:
            print(f"Error al listar archivos en la carpeta '{folder_id}': {error}")
            return []

    def find_item_by_name(self, name: str, parent_id: str = "root") -> Optional[dict]:
        """Busca un archivo o carpeta por nombre en una carpeta padre."""
        try:
            response = (
                self.service.files()
                .list(
                    q=f"name = '{name}' and '{parent_id}' in parents and trashed = false",
                    spaces="drive",
                    fields="files(id, name, mimeType, modifiedTime)",
                )
                .execute()
            )
            items = response.get("files", [])
            return items[0] if items else None
        except HttpError as error:
            print(f"Error al buscar el item '{name}': {error}")
            return None

    def get_file_content(self, file_id: str) -> Optional[bytes]:
        """Descarga el contenido crudo (bytes) de un archivo."""
        try:
            request = self.service.files().get_media(fileId=file_id)
            file_stream = io.BytesIO()
            downloader = MediaIoBaseDownload(file_stream, request)
            done = False
            while not done:
                status, done = downloader.next_chunk()
                print(f"Descargando... {int(status.progress() * 100)}%")
            return file_stream.getvalue()
        except HttpError as error:
            print(f"Error HTTP al descargar archivo '{file_id}': {error}")
            return None

    def update_file_from_memory(
        self, file_id: str, content: str, mime_type: str
    ) -> Optional[dict]:
        """Actualiza el contenido de un archivo desde un string en memoria."""
        try:
            content_stream = io.BytesIO(content.encode("utf-8"))
            media_body = MediaIoBaseUpload(
                content_stream, mimetype=mime_type, resumable=True
            )
            updated_file = (
                self.service.files()
                .update(
                    fileId=file_id,
                    media_body=media_body,
                    fields="id, name, modifiedTime",
                )
                .execute()
            )
            print(
                f'Contenido del archivo "{updated_file.get("name")}" actualizado con éxito.'
            )
            return updated_file
        except HttpError as error:
            print(f"Error al modificar archivo '{file_id}': {error}")
            return None

    def create_file_from_memory(
        self, folder_id: str, file_name: str, content: str, mime_type: str
    ) -> Optional[dict]:
        """Crea un nuevo archivo desde un string en memoria."""
        file_metadata = {
            "name": file_name,
            "parents": [folder_id],
            "mimeType": mime_type,
        }
        try:
            content_stream = io.BytesIO(content.encode("utf-8"))
            media = MediaIoBaseUpload(
                content_stream, mimetype=mime_type, resumable=True
            )
            file = (
                self.service.files()
                .create(body=file_metadata, media_body=media, fields="id, name")
                .execute()
            )
            print(f'Archivo creado: "{file.get("name")}" (ID: "{file.get("id")}")')
            return file
        except HttpError as error:
            print(f"Error al crear archivo '{file_name}': {error}")
            return None


class AIStudioDriveManager:
    """
    Gestiona las operaciones específicas de la aplicación con Google AI Studio en Drive.
    Utiliza GoogleDriveManager para las interacciones de bajo nivel con la API.
    Esta clase conoce la estructura de los chats, la carpeta "Google AI Studio", etc.
    """

    AI_STUDIO_FOLDER_NAME = "Google AI Studio"

    def __init__(self):
        self.gdm = GoogleDriveManager()
        self.ai_studio_folder = cast(str, self._find_ai_studio_folder())
        if not self.ai_studio_folder:
            raise FileNotFoundError(
                f"La carpeta '{self.AI_STUDIO_FOLDER_NAME}' no fue encontrada en Google Drive."
            )

    def _find_ai_studio_folder(self) -> Optional[str]:
        """Encuentra la carpeta raíz de Google AI Studio."""
        print(f"Buscando la carpeta '{self.AI_STUDIO_FOLDER_NAME}'...")
        folder = self.gdm.find_item_by_name(self.AI_STUDIO_FOLDER_NAME)
        if not folder:
            print(f"La carpeta '{self.AI_STUDIO_FOLDER_NAME}' no fue encontrada.")
            raise FileNotFoundError(
                f"La carpeta '{self.AI_STUDIO_FOLDER_NAME}' no fue encontrada en Google Drive."
            )
        return folder.get("id")

    def get_chat_ia_studio(self, chat_id: str) -> Optional[ChatIAStudio]:
        """
        Obtiene y parsea el contenido de un archivo de chat de AI Studio.
        """
        content_bytes = self.gdm.get_file_content(chat_id)
        if not content_bytes:
            print(f"No se pudo obtener el contenido del chat con ID '{chat_id}'.")
            return None
        try:
            chat_content = json.loads(content_bytes.decode("utf-8"))
            return ChatIAStudio(**chat_content)
        except json.JSONDecodeError as e:
            print(f"Error al decodificar el JSON del chat '{chat_id}': {e}")
            return None

    def clear_chat_ia_studio(self, chat_id: str) -> bool:
        """
        Limpia el historial de un chat, conservando las 3 primeras instrucciones (contexto y prompts iniciales).
        """
        print(f"Intentando limpiar el chat con ID: {chat_id}")
        chat = self.get_chat_ia_studio(chat_id)
        if not chat:
            return False

        # Mantiene solo los 3 primeros chunks (archivo de contexto, prompt del usuario, respuesta del modelo)
        original_chunks_count = len(chat.chunkedPrompt.chunks)
        chat.chunkedPrompt.chunks = chat.chunkedPrompt.chunks[:3]

        # Convierte el objeto Pydantic modificado de nuevo a un string JSON
        cleared_content_json = chat.model_dump_json()

        result = self.gdm.update_file_from_memory(
            file_id=chat_id,
            content=cleared_content_json,
            mime_type="application/vnd.google-makersuite.prompt",
        )

        if result:
            print(
                f"Chat limpiado con éxito. Se eliminaron {original_chunks_count - 3} mensajes."
            )
            return True
        else:
            print("Falló la actualización del archivo del chat en Google Drive.")
            return False

    def create_chat_file(
        self, file_name: str, chat_data: ChatIAStudio
    ) -> Optional[str]:
        """
        Crea un nuevo archivo de chat en la carpeta de AI Studio.
        """
        content_json = chat_data.model_dump_json()
        result = self.gdm.create_file_from_memory(
            folder_id=self.ai_studio_folder,
            file_name=file_name,
            content=content_json,
            mime_type="application/vnd.google-makersuite.prompt",
        )
        return result.get("id") if result else None
