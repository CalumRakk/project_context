import io
import json
from contextlib import contextmanager
from typing import Generator, List, Optional, cast

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
    DriveDocument,
    Role,
)
from project_context.utils import COMMIT_TASK_MARKER, UI, profile_manager


class ChunkFactory:
    """Centraliza la creación de bloques de mensaje para el Chat."""

    @staticmethod
    def create_text(text: str, role: Role = "user") -> ChunksText:
        return ChunksText(text=text, role=role)

    @staticmethod
    def create_file(
        file_id: str, role: Role = "user", tokens: int = 0
    ) -> ChunksDocument:
        return ChunksDocument(
            driveDocument=DriveDocument(id=file_id), role=role, tokenCount=tokens
        )

    @staticmethod
    def create_image(file_id: str, role: Role = "user") -> ChunksImage:
        return ChunksImage(driveImage=DriveDocument(id=file_id), role=role)


class GoogleDriveManager:
    SCOPES = ["https://www.googleapis.com/auth/drive"]

    def __init__(self):
        """Inicializa el cliente de Drive y se autentica usando el perfil activo."""
        self.profile_name = profile_manager.get_active_profile_name()
        self.working_dir = profile_manager.get_working_dir()
        self.client_secrets_file, source_type = profile_manager.resolve_secrets_file()
        self.token_file = self.working_dir / "token.json"

        UI.info(f"Perfil activo: [bold]{self.profile_name}[/]")

        self.credentials = self._authenticate()
        self.service = build("drive", "v3", credentials=self.credentials)
        UI.success("Google Drive Manager inicializado con éxito.")

    def _authenticate(self) -> Credentials:
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
                        f"Ruta esperada: {self.client_secrets_file}"
                    )

                UI.info("Iniciando flujo de autenticación de Google Drive...")
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.client_secrets_file, self.SCOPES
                )
                creds = cast(Credentials, flow.run_local_server(port=0))
                UI.success("Autenticación completada.")

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
            UI.success(f"Archivo actualizado en Drive.")
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
    MIME_PROMPT = "application/vnd.google-makersuite.prompt"

    def __init__(self):
        self.gdm = GoogleDriveManager()
        self.ai_studio_folder = cast(str, self._find_ai_studio_folder())
        if not self.ai_studio_folder:
            raise FileNotFoundError(
                f"La carpeta '{self.AI_STUDIO_FOLDER_NAME}' no fue encontrada en Google Drive."
            )

    def _find_ai_studio_folder(self) -> Optional[str]:
        folder = self.gdm.find_item_by_name(self.AI_STUDIO_FOLDER_NAME)
        if not folder:
            print(f"La carpeta '{self.AI_STUDIO_FOLDER_NAME}' no fue encontrada.")
            return None
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

    def create_chat_file(
        self, file_name: str, chat_data: ChatIAStudio
    ) -> Optional[str]:
        content_json = chat_data.model_dump_json()
        result = self.gdm.create_file_from_memory(
            folder_id=self.ai_studio_folder,
            file_name=file_name,
            content=content_json,
            mime_type=self.MIME_PROMPT,
        )
        return result.get("id") if result else None

    def update_chat_file(self, chat_id: str, chat_data: ChatIAStudio) -> bool:
        """
        Serializa y actualiza un objeto chat directamente en Drive.
        """
        try:
            content_json = chat_data.model_dump_json()
            result = self.gdm.update_file_from_memory(
                file_id=chat_id,
                content=content_json,
                mime_type=self.MIME_PROMPT,
            )
            return bool(result)
        except Exception as e:
            print(f"Error actualizando chat: {e}")
            return False

    @contextmanager
    def modify_chat(self, chat_id: str) -> Generator[ChatIAStudio, None, None]:
        """
        Context Manager para realizar modificaciones atómicas en un Chat.
        Encapsula el ciclo: Obtener -> Modificar -> Guardar.
        Si ocurre un error dentro del 'with', NO guarda los cambios.
        """
        chat = self.get_chat_ia_studio(chat_id)
        if not chat:
            raise FileNotFoundError(f"Chat {chat_id} no encontrado o inaccesible.")

        try:
            yield chat
        except Exception as e:
            UI.error(f"Error procesando chat (cambios descartados): {e}")
            raise e
        else:
            # Solo guardamos si no hubo excepciones
            if not self.update_chat_file(chat_id, chat):
                raise IOError("Falló la escritura del chat en Google Drive.")

    def clear_chat_ia_studio(self, chat_id: str) -> bool:
        """
        Limpia el historial manteniendo el contexto inicial.
        """
        try:
            with self.modify_chat(chat_id) as chat:
                chunks = chat.chunkedPrompt.chunks
                if not chunks:
                    print("El chat ya está vacío.")
                    return True

                cut_idx = -1
                for i, chunk in enumerate(chunks):
                    if chunk.role == "model":
                        cut_idx = i
                        break

                if cut_idx == -1:
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
                        print("Error: Estructura de contexto inválida.")
                        return False

                original_count = len(chunks)
                new_chunks = chunks[: cut_idx + 1]

                if len(new_chunks) == original_count:
                    print("El chat ya está limpio.")
                    return True

                chat.chunkedPrompt.chunks = new_chunks
                print(
                    f"Limpieza completada. Eliminados: {original_count - len(new_chunks)}"
                )
            return True
        except Exception:
            return False

    def remove_commit_tasks(self, chat_id: str) -> int:
        """
        Busca y elimina los bloques de commit (user) y sus respuestas (model).
        """
        removed_count = 0
        try:
            with self.modify_chat(chat_id) as chat:
                original_chunks = chat.chunkedPrompt.chunks
                new_chunks = []
                skip_next = False

                for i, chunk in enumerate(original_chunks):
                    if skip_next:
                        skip_next = False
                        removed_count += 1
                        continue

                    if (
                        isinstance(chunk, ChunksText)
                        and COMMIT_TASK_MARKER in chunk.text
                    ):
                        removed_count += 1
                        if i + 1 < len(original_chunks):
                            next_chunk = original_chunks[i + 1]
                            if getattr(next_chunk, "role", None) == "model":
                                skip_next = True
                        continue

                    new_chunks.append(chunk)

                if removed_count > 0:
                    chat.chunkedPrompt.chunks = new_chunks
            return removed_count
        except Exception:
            return 0

    def append_message(self, chat_id: str, text: str, role: Role = "user") -> bool:
        """
        Agrega un mensaje de texto simple al chat y lo guarda en Drive.
        """
        try:
            with self.modify_chat(chat_id) as chat:
                new_chunk = ChunkFactory.create_text(text, role=role)
                chat.chunkedPrompt.chunks.append(new_chunk)
            return True
        except Exception:
            return False

    def append_chunks(self, chat_id: str, chunks: List[Chunk]) -> bool:
        """
        Agrega una lista de chunks (texto, imágenes, archivos) al chat.
        """
        try:
            with self.modify_chat(chat_id) as chat:
                chat.chunkedPrompt.chunks.extend(chunks)
            return True
        except Exception:
            return False

    def repair_chat_structure(self, chat_id: str) -> int:
        """
        Corrige inconsistencias en el chat (ej: finishReason).
        Retorna la cantidad de bloques corregidos.
        """
        fixed_count = 0
        try:
            with self.modify_chat(chat_id) as chat:
                for chunk in chat.chunkedPrompt.chunks:
                    if isinstance(chunk, ChunksText) and hasattr(chunk, "finishReason"):
                        if chunk.finishReason != "STOP":
                            chunk.finishReason = "STOP"
                            fixed_count += 1
            return fixed_count
        except Exception:
            return 0

    def has_pending_commit_suggestion(self, chat_id: str) -> bool:
        """
        Verifica si existe una sugerencia de commit pendiente.r
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
