import io
import json
import logging
import threading
from contextlib import contextmanager
from typing import Generator, List, Optional

from google.oauth2.credentials import Credentials
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
from project_context.utils import COMMIT_TASK_MARKER, UI

logger = logging.getLogger(__name__)


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
    MIME_PROMPT = "application/vnd.google-makersuite.prompt"

    def __init__(self, credentials: Credentials):
        # FIXME: Esto abre un bloqueo que no se libera.
        self._lock = threading.Lock()

        self.service = build("drive", "v3", credentials=credentials)
        self._ai_studio_folder_id = None
        UI.success("Google Drive Manager inicializado con éxito.")

    @property
    def ai_studio_folder(self) -> str:
        """
        Resuelve de manera perezosa (lazy) el ID de la carpeta de Google AI Studio.
        Lanza un FileNotFoundError si no se encuentra en el entorno de Drive.
        """
        if self._ai_studio_folder_id is not None:
            return self._ai_studio_folder_id

        folder = self.find_item_by_name("Google AI Studio", parent_id="root")
        if not folder:
            raise FileNotFoundError(
                "La carpeta 'Google AI Studio' no fue encontrada en Google Drive."
            )
        self._ai_studio_folder_id = folder["id"]
        return self._ai_studio_folder_id

    # --- MÉTODOS PRIVADOS DE BAJO NIVEL (NATIVOS) ---

    def _download_bytes(self, file_id: str) -> Optional[bytes]:
        try:
            with self._lock:
                request = self.service.files().get_media(fileId=file_id)
                file_stream = io.BytesIO()
                downloader = MediaIoBaseDownload(file_stream, request)
                done = False
                while not done:
                    status, done = downloader.next_chunk()
                return file_stream.getvalue()
        except HttpError as error:
            if error.resp.status == 404:
                logger.debug(
                    f"El archivo con ID '{file_id}' no está disponible para descarga (404)."
                )
            else:
                logger.error(f"Error HTTP al descargar archivo '{file_id}': {error}")
            return None

    def _upload_bytes(
        self,
        content: bytes,
        mime_type: str,
        metadata: Optional[dict] = None,
        file_id: Optional[str] = None,
        fields: str = "id, name",
    ) -> Optional[dict]:
        try:
            content_stream = io.BytesIO(content)
            media = MediaIoBaseUpload(
                content_stream, mimetype=mime_type, resumable=True
            )
            with self._lock:
                if file_id:
                    return (
                        self.service.files()
                        .update(fileId=file_id, media_body=media, fields=fields)
                        .execute()
                    )
                else:
                    return (
                        self.service.files()
                        .create(body=metadata, media_body=media, fields=fields)
                        .execute()
                    )
        except HttpError as error:
            UI.error(f"Error en operación de subida/actualización de Drive: {error}")
            return None

    def _get_metadata(
        self, file_id: str, fields: str = "id, name, modifiedTime, md5Checksum"
    ) -> Optional[dict]:
        try:
            return self.service.files().get(fileId=file_id, fields=fields).execute()
        except HttpError as error:
            if error.resp.status == 404:
                logger.debug(
                    f"El archivo con ID '{file_id}' no existe en Google Drive (404 esperado)."
                )
            else:
                logger.error(f"Error al obtener metadata de '{file_id}': {error}")
            return None

    def _list_files_by_query(
        self, query: str, fields: str = "files(id, name, mimeType)"
    ) -> list[dict]:
        try:
            response = (
                self.service.files()
                .list(q=query, spaces="drive", fields=fields)
                .execute()
            )
            return response.get("files", [])
        except HttpError as error:
            logger.debug(f"Error al buscar archivos por consulta '{query}': {error}")
            return []

    # --- MÉTODOS PÚBLICOS DE ALTO NIVEL (DOMINIO) ---

    def list_files(self, folder_id: str = "root") -> list[dict]:
        items = []
        page_token = None
        try:
            while True:
                with self._lock:
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
            logger.error(
                f"Error al listar archivos en la carpeta '{folder_id}': {error}"
            )
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
            logger.error(f"Error al buscar el item '{name}': {error}")
            return None

    def get_file_content(self, file_id: str) -> Optional[bytes]:
        return self._download_bytes(file_id)

    def update_file_from_memory(
        self, file_id: str, content: str, mime_type: str
    ) -> Optional[dict]:
        updated_file = self._upload_bytes(
            content.encode("utf-8"),
            mime_type,
            file_id=file_id,
            fields="id, name, modifiedTime",
        )
        if updated_file:
            UI.success("Archivo actualizado en Drive.")
        return updated_file

    def create_file_from_memory(
        self, folder_id: str, file_name: str, content: str, mime_type: str
    ) -> Optional[dict]:
        file_metadata = {
            "name": file_name,
            "parents": [folder_id],
            "mimeType": mime_type,
        }
        file = self._upload_bytes(
            content.encode("utf-8"), mime_type, metadata=file_metadata
        )
        if file:
            logger.debug(
                f'Archivo creado: "{file.get("name")}" (ID: "{file.get("id")}")'
            )
        return file

    def get_file_metadata(
        self, file_id: str, fields: str = "id, name, modifiedTime, md5Checksum"
    ) -> Optional[dict]:
        return self._get_metadata(file_id, fields)

    def find_files_by_query(
        self, query: str, fields: str = "files(id, name, mimeType)"
    ) -> list[dict]:
        return self._list_files_by_query(query, fields)

    def delete_file(self, file_id: str) -> bool:
        try:
            self.service.files().delete(fileId=file_id).execute()
            return True
        except HttpError as error:
            logger.error(f"Error al eliminar archivo '{file_id}': {error}")
            return False

    def upload_binary_to_drive(
        self, folder_id: str, file_name: str, content: bytes, mime_type: str
    ) -> Optional[dict]:
        file_metadata = {
            "name": file_name,
            "parents": [folder_id],
            "mimeType": mime_type,
        }
        return self._upload_bytes(content, mime_type, metadata=file_metadata)

    # --- OPERACIONES DEL CHAT (MÉTODOS DE DOMINIO) ---

    def get_chat_ia_studio(self, chat_id: str) -> Optional[ChatIAStudio]:
        content_bytes = self._download_bytes(chat_id)
        if not content_bytes:
            logger.debug(
                f"No se pudo obtener el contenido del chat con ID '{chat_id}'."
            )
            return None
        try:
            chat_content = json.loads(content_bytes.decode("utf-8"))
            return ChatIAStudio(**chat_content)
        except json.JSONDecodeError as e:
            logger.debug(f"Error al decodificar el JSON del chat '{chat_id}': {e}")
            return None

    def create_chat_file(
        self, folder_id: str, file_name: str, chat_data: ChatIAStudio
    ) -> Optional[str]:
        content_json = chat_data.model_dump_json(exclude_none=True, exclude_unset=True)
        result = self.create_file_from_memory(
            folder_id=folder_id,
            file_name=file_name,
            content=content_json,
            mime_type=self.MIME_PROMPT,
        )
        return result.get("id") if result else None

    def update_chat_file(self, chat_id: str, chat_data: ChatIAStudio) -> bool:
        try:
            content_json = chat_data.model_dump_json(
                exclude_none=True, exclude_unset=True
            )
            result = self.update_file_from_memory(
                file_id=chat_id,
                content=content_json,
                mime_type=self.MIME_PROMPT,
            )
            return bool(result)
        except Exception as e:
            logger.debug(f"Error actualizando chat: {e}")
            return False

    @contextmanager
    def modify_chat(self, chat_id: str) -> Generator[ChatIAStudio, None, None]:
        chat = self.get_chat_ia_studio(chat_id)
        if not chat:
            raise FileNotFoundError(f"Chat {chat_id} no encontrado o inaccesible.")

        try:
            yield chat
        except Exception as e:
            UI.error(f"Error procesando chat (cambios descartados): {e}")
            raise e
        else:
            if not self.update_chat_file(chat_id, chat):
                raise IOError("Falló la escritura del chat en Google Drive.")

    def clear_chat_ia_studio(self, chat_id: str) -> bool:
        try:
            with self.modify_chat(chat_id) as chat:
                chunks = chat.chunkedPrompt.chunks
                if not chunks:
                    logger.debug("El chat ya está vacío.")
                    return True

                cut_idx = -1
                for i, chunk in enumerate(chunks):
                    if chunk.role == "model":
                        cut_idx = i
                        break

                if cut_idx == -1:
                    doc_idx = -1
                    for i, chunk in enumerate(chunks):
                        if chunk.is_file_reference:
                            doc_idx = i

                    if doc_idx != -1:
                        if len(chunks) > doc_idx + 1 and isinstance(
                            chunks[doc_idx + 1], ChunksText
                        ):
                            cut_idx = doc_idx + 1
                        else:
                            cut_idx = doc_idx
                    else:
                        logger.debug("Error: Estructura de contexto inválida.")
                        return False

                original_count = len(chunks)
                new_chunks = chunks[: cut_idx + 1]

                if len(new_chunks) == original_count:
                    logger.debug("El chat ya está limpio.")
                    return True

                chat.chunkedPrompt.chunks = new_chunks
                logger.debug(
                    f"Limpieza completada. Eliminados: {original_count - len(new_chunks)}"
                )
            return True
        except Exception:
            return False

    def remove_commit_tasks(self, chat_id: str) -> int:
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
        try:
            with self.modify_chat(chat_id) as chat:
                new_chunk = ChunkFactory.create_text(text, role=role)
                chat.chunkedPrompt.chunks.append(new_chunk)
            return True
        except Exception:
            return False

    def append_chunks(self, chat_id: str, chunks: List[Chunk]) -> bool:
        try:
            with self.modify_chat(chat_id) as chat:
                chat.chunkedPrompt.chunks.extend(chunks)
            return True
        except Exception:
            return False

    def repair_chat_structure(self, chat_id: str) -> int:
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
