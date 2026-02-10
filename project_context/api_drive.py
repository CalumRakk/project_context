import io
import json
from typing import List, Optional, cast

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

from project_context.schema import (
    ChatIAStudio,
    Chunk,
    ChunksDocument,
    ChunksImage,
    ChunksText,
    Role,
)
from project_context.utils import COMMIT_TASK_MARKER, UI, profile_manager


class GoogleDriveManager:
    SCOPES = ["https://www.googleapis.com/auth/drive"]

    def __init__(self):
        """Inicializa el cliente de Drive y se autentica usando el perfil activo."""
        self.profile_name = profile_manager.get_active_profile_name()
        self.working_dir = profile_manager.get_working_dir()

        # Estrategia de Cascada para Secretos
        self.client_secrets_file, source_type = profile_manager.resolve_secrets_file()

        # El token siempre es específico del perfil
        self.token_file = self.working_dir / "token.json"

        UI.info(f"Perfil activo: [bold]{self.profile_name}[/]")

        self.credentials = self._authenticate()
        self.service = build("drive", "v3", credentials=self.credentials)
        UI.success("Google Drive Manager inicializado con éxito.")

    def _authenticate(self) -> Credentials:
        """
        Gestiona el proceso de autenticación de OAuth 2.0.
        """
        creds: Optional[Credentials] = None
        if self.token_file.exists():
            creds = Credentials.from_authorized_user_file(self.token_file, self.SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    UI.info("Credenciales de Drive refrescadas.")
                except Exception as e:
                    UI.error(f"Error al refrescar: {e}. Se requiere re-autenticación.")
                    creds = None

            if not creds:
                if not self.client_secrets_file.exists():
                    raise FileNotFoundError(
                        f"No se encontró 'client_secrets.json'.\n"
                        f"Ruta esperada: {self.client_secrets_file}\n"
                        f"Por favor, coloca el archivo JSON en la carpeta global ({profile_manager.root_dir})\n"
                        f"o en la carpeta del perfil actual ({self.working_dir})."
                    )

                UI.info("Iniciando flujo de autenticación de Google Drive...")
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.client_secrets_file, self.SCOPES
                )
                creds = cast(Credentials, flow.run_local_server(port=0))
                UI.success("Autenticación completada.")

            # Guardamos el token en la carpeta del perfil
            with open(self.token_file, "w") as token:
                token.write(creds.to_json())
            UI.info(f"Credenciales guardadas en el perfil.")

        return creds

    def list_files(self, folder_id: str = "root") -> list[dict]:
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
        try:
            request = self.service.files().get_media(fileId=file_id)
            file_stream = io.BytesIO()
            downloader = MediaIoBaseDownload(file_stream, request)
            done = False
            while not done:
                status, done = downloader.next_chunk()
                # print(f"Descargando... {int(status.progress() * 100)}%")
            return file_stream.getvalue()
        except HttpError as error:
            print(f"Error HTTP al descargar archivo '{file_id}': {error}")
            return None

    def update_file_from_memory(
        self, file_id: str, content: str, mime_type: str
    ) -> Optional[dict]:
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
            UI.success(f"Archivo de contexto actualizado en Drive.")
            return updated_file
        except HttpError as error:
            UI.error(f"Error al modificar archivo '{file_id}': {error}")
            return None

    def create_file_from_memory(
        self, folder_id: str, file_name: str, content: str, mime_type: str
    ) -> Optional[dict]:
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

    def get_file_metadata(self, file_id: str) -> Optional[dict]:
        try:
            return (
                self.service.files()
                .get(fileId=file_id, fields="id, name, modifiedTime, md5Checksum")
                .execute()
            )
        except HttpError as error:
            print(f"Error al obtener metadata de '{file_id}': {error}")
            return None

    def upload_binary_to_drive(
        self, folder_id: str, file_name: str, content: bytes, mime_type: str
    ) -> Optional[dict]:
        """Sube cualquier archivo binario (como imágenes)."""
        file_metadata = {
            "name": file_name,
            "parents": [folder_id],
            "mimeType": mime_type,
        }
        try:
            content_stream = io.BytesIO(content)
            media = MediaIoBaseUpload(
                content_stream, mimetype=mime_type, resumable=True
            )
            file = (
                self.service.files()
                .create(body=file_metadata, media_body=media, fields="id, name")
                .execute()
            )
            return file
        except HttpError as error:
            print(f"Error al subir binario '{file_name}': {error}")
            return None


class AIStudioDriveManager:
    AI_STUDIO_FOLDER_NAME = "Google AI Studio"

    def __init__(self):
        self.gdm = GoogleDriveManager()
        self.ai_studio_folder = cast(str, self._find_ai_studio_folder())
        if not self.ai_studio_folder:
            raise FileNotFoundError(
                f"La carpeta '{self.AI_STUDIO_FOLDER_NAME}' no fue encontrada en Google Drive."
            )

    def _find_ai_studio_folder(self) -> Optional[str]:
        # print(f"Buscando la carpeta '{self.AI_STUDIO_FOLDER_NAME}'...")
        folder = self.gdm.find_item_by_name(self.AI_STUDIO_FOLDER_NAME)
        if not folder:
            print(f"La carpeta '{self.AI_STUDIO_FOLDER_NAME}' no fue encontrada.")
            raise FileNotFoundError(
                f"La carpeta '{self.AI_STUDIO_FOLDER_NAME}' no fue encontrada en Google Drive."
            )
        return folder.get("id")

    def get_chat_ia_studio(self, chat_id: str) -> Optional[ChatIAStudio]:
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
        print(f"Analizando estructura del chat para limpieza...")
        chat = self.get_chat_ia_studio(chat_id)
        if not chat:
            return False

        chunks = chat.chunkedPrompt.chunks
        if not chunks:
            print("El chat ya está vacío.")
            return True

        cut_idx = -1  # Primera respuesta del modelo
        for i, chunk in enumerate(chunks):
            if chunk.role == "model":
                cut_idx = i
                break

        # No se encontro respuesta del modelo
        if cut_idx == -1:
            print("Aviso: No se encontró respuesta del modelo para anclar la limpieza.")
            doc_idx = -1
            for i, chunk in enumerate(chunks):
                if isinstance(chunk, (ChunksDocument, ChunksImage)) or hasattr(
                    chunk, "driveDocument"
                ):
                    doc_idx = i

            if doc_idx != -1:
                if len(chunks) > doc_idx + 1 and isinstance(
                    chunks[doc_idx + 1], ChunksText
                ):
                    cut_idx = doc_idx + 1
                else:
                    cut_idx = doc_idx
            else:
                print("Error: No se detectó una estructura de contexto válida.")
                print("Usa el comando 'reset' para reconstruir el chat desde cero.")
                return False

        original_count = len(chunks)
        new_chunks = chunks[: cut_idx + 1]

        if len(new_chunks) == original_count:
            print("El chat ya está limpio (solo contiene el contexto inicial).")
            return True

        chat.chunkedPrompt.chunks = new_chunks

        if self.update_chat_file(chat_id, chat):
            print(
                f"Limpieza completada. Se conservaron los primeros {len(new_chunks)} bloques (Setup)."
            )
            print(f"Mensajes eliminados: {original_count - len(new_chunks)}")
            return True

        return False

    def create_chat_file(
        self, file_name: str, chat_data: ChatIAStudio
    ) -> Optional[str]:
        content_json = chat_data.model_dump_json()
        result = self.gdm.create_file_from_memory(
            folder_id=self.ai_studio_folder,
            file_name=file_name,
            content=content_json,
            mime_type="application/vnd.google-makersuite.prompt",
        )
        return result.get("id") if result else None

    def update_chat_file(self, chat_id: str, chat_data: ChatIAStudio) -> bool:
        """
        Serializa y actualiza un objeto chat directamente en Drive.
        Encapsula el MIME type y la serialización JSON.
        """
        try:
            content_json = chat_data.model_dump_json()
            result = self.gdm.update_file_from_memory(
                file_id=chat_id,
                content=content_json,
                mime_type="application/vnd.google-makersuite.prompt",
            )
            return bool(result)
        except Exception as e:
            print(f"Error actualizando chat: {e}")
            return False

    def remove_commit_tasks(self, chat_id: str) -> int:
        """
        Busca y elimina los bloques de commit (user) y sus respuestas (model).
        Retorna la cantidad de bloques eliminados.
        """
        chat = self.get_chat_ia_studio(chat_id)
        if not chat:
            return 0

        original_chunks = chat.chunkedPrompt.chunks
        new_chunks = []
        skip_next = False
        removed_count = 0

        for i, chunk in enumerate(original_chunks):
            if skip_next:
                skip_next = False
                removed_count += 1
                continue

            # Detectar nuestro marcador
            if isinstance(chunk, ChunksText) and COMMIT_TASK_MARKER in chunk.text:
                removed_count += 1
                # Si el siguiente bloque es la respuesta del modelo, marcar para saltar
                if i + 1 < len(original_chunks):
                    next_chunk = original_chunks[i + 1]
                    if getattr(next_chunk, "role", None) == "model":
                        skip_next = True
                continue

            new_chunks.append(chunk)

        if removed_count > 0:
            chat.chunkedPrompt.chunks = new_chunks
            self.update_chat_file(chat_id, chat)

        return removed_count

    def append_message(self, chat_id: str, text: str, role: Role = "user") -> bool:
        """
        Agrega un mensaje de texto simple al chat y lo guarda en Drive.
        """
        chat = self.get_chat_ia_studio(chat_id)
        if not chat:
            return False

        new_chunk = ChunksText(text=text, role=role)
        chat.chunkedPrompt.chunks.append(new_chunk)

        return self.update_chat_file(chat_id, chat)

    def append_chunks(self, chat_id: str, chunks: List[Chunk]) -> bool:
        """
        Agrega una lista de chunks (texto, imágenes, archivos) al chat.
        """
        chat = self.get_chat_ia_studio(chat_id)
        if not chat:
            return False

        chat.chunkedPrompt.chunks.extend(chunks)
        return self.update_chat_file(chat_id, chat)

    def repair_chat_structure(self, chat_id: str) -> int:
        """
        Corrige inconsistencias en el chat (ej: finishReason).
        Retorna la cantidad de bloques corregidos.
        """
        chat = self.get_chat_ia_studio(chat_id)
        if not chat:
            return 0

        fixed_count = 0
        for chunk in chat.chunkedPrompt.chunks:
            if isinstance(chunk, ChunksText) and hasattr(chunk, "finishReason"):
                if chunk.finishReason != "STOP":
                    chunk.finishReason = "STOP"
                    fixed_count += 1

        if fixed_count > 0:
            if not self.update_chat_file(chat_id, chat):
                return 0  # Falló el guardado

        return fixed_count

    def has_pending_commit_suggestion(self, chat_id: str) -> bool:
        """
        Verifica si ya existe una sugerencia de commit (User) sin respuesta (Model)
        o si simplemente existe el marcador en el chat.
        """
        chat = self.get_chat_ia_studio(chat_id)
        if not chat:
            return False

        chunks = chat.chunkedPrompt.chunks
        for i in range(len(chunks) - 1, -1, -1):
            chunk = chunks[i]
            if isinstance(chunk, ChunksText) and COMMIT_TASK_MARKER in chunk.text:
                if i + 1 < len(chunks):
                    next_chunk = chunks[i + 1]
                    if getattr(next_chunk, "role", "") == "model":
                        return False
                return True

        return False
