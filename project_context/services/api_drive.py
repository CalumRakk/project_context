import io
import json
import logging
import threading
from pathlib import Path
from typing import Optional, Union

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
from pydantic import BaseModel, ConfigDict

from project_context.core.schemas import (
    ChatIAStudio,
    ChunkDocument,
    ChunkedPrompt,
    ChunkImage,
    ChunkText,
    ContextRemote,
    DriveDocument,
    Role,
    RunSettings,
    SystemInstruction,
)
from project_context.utils import (
    COMMIT_TASK_MARKER,
    PROMPT_TEMPLATE,
    RESPONSE_TEMPLATE,
    UI,
)

logger = logging.getLogger(__name__)


class FileDriver(BaseModel):
    id: str
    name: str
    mimeType: str
    md5Checksum: str
    size: int
    modifiedTime: str

    model_config = ConfigDict(extra="allow")

    # Propiedades de google Driver traducidas a la semantica del proyecto.
    @property
    def mimetype(self):
        return self.mimeType

    @property
    def md5sum(self):
        return self.md5Checksum

    @property
    def filename(self):
        return self.name

    @property
    def file_id(self):
        return self.id


class ChunkFactory:
    """Centraliza la creación de bloques de mensaje para el Chat."""

    @staticmethod
    def create_text(text: str, role: Role = "user") -> ChunkText:
        return ChunkText(text=text, role=role)

    @staticmethod
    def create_file(
        file_id: str, role: Role = "user", tokens: int = 0
    ) -> ChunkDocument:
        return ChunkDocument(
            driveDocument=DriveDocument(id=file_id), role=role, tokenCount=tokens
        )

    @staticmethod
    def create_image(file_id: str, role: Role = "user") -> ChunkImage:
        return ChunkImage(driveImage=DriveDocument(id=file_id), role=role)

    @classmethod
    def create_default_run_settings(cls) -> RunSettings:
        """Retorna una configuración de RunSettings con valores iniciales explícitos y seguros."""
        thinkingLevel = "THINKING_MEDIUM"
        return RunSettings(
            model="models/gemini-3.5-flash",
            temperature=1.0,
            topP=0.95,
            topK=64,
            maxOutputTokens=65536,
            thinkingBudget=None,
            thinkingLevel=thinkingLevel,
        )

    @classmethod
    def build_initial_chat(cls, context_remote: ContextRemote):
        """Construye los tres chunks iniciales estándar (Contexto, Prompt de bienvenida, Acuse de recibo)."""

        context = context_remote.context
        context_chunk = ChunkFactory.create_file(
            context_remote.file_id, role="user", tokens=context.token_count
        )
        prompt_chunk = ChunkFactory.create_text(PROMPT_TEMPLATE, role="user")
        model_chunk = ChunkFactory.create_text(RESPONSE_TEMPLATE, role="model")
        chunks = [context_chunk, prompt_chunk, model_chunk]
        return ChatIAStudio(
            runSettings=cls.create_default_run_settings(),
            systemInstruction=SystemInstruction(),
            chunkedPrompt=ChunkedPrompt(
                chunks=chunks,
                pendingInputs=[],
            ),
        )


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

    def _download_bytes(self, file_id: str) -> Optional[bytes]:
        """Descarga un archivo de Drive en memoria y devuelve sus bytes."""
        try:
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
    ) -> dict:
        """Sube o actualiza un archivo en Drive. Si file_id es proporcionado, se actualiza el archivo existente, en caso contrario, se crea un nuevo archivo.

        Args:
            content: Contenido del archivo en bytes.
            mime_type: Tipo MIME del archivo.
            metadata: Metadatos adicionales del archivo. Defaults to None.
            file_id: ID del archivo existente a actualizar. Defaults to None.
            fields: Campos a incluir en la respuesta. Defaults to "id, name".

        """

        content_stream = io.BytesIO(content)
        media = MediaIoBaseUpload(content_stream, mimetype=mime_type, resumable=True)
        fields = " ,".join(FileDriver.model_fields.keys())
        if file_id:
            return (
                self.service.files()
                .update(fileId=file_id, media_body=media, fields=fields)
                .execute()
            )

        return (
            self.service.files()
            .create(body=metadata, media_body=media, fields=fields)
            .execute()
        )

    def _resolver_content(self, content: Union[str, Path, bytes]):
        if isinstance(content, Path):
            return content.read_bytes()
        elif isinstance(content, str):
            return content.encode("utf-8")
        elif isinstance(content, bytes):
            return content
        else:
            raise ValueError("content debe ser una cadena, un Path o un bytes")

    def get_metadata(self, file_id: str) -> Optional[FileDriver]:
        # Google Drive API v3 permite controlar qué propiedades del recurso File
        # se devuelven mediante el parámetro `fields`.
        #
        # - Si se omite, la API devuelve solo un conjunto reducido de campos por defecto.
        # - Para solicitar todos los campos disponibles se puede usar `fields="*"`.
        # - Al ejecutar métodos sobre un único recurso (`get`, `create`, `update`),
        #   se especifica una lista simple de propiedades:
        #       fields="id,name,mimeType"
        # - En métodos que devuelven colecciones (`list`), se utiliza una sintaxis
        #   anidada para indicar los campos de cada elemento:
        #       fields="nextPageToken,files(id,name)"
        #
        # Referencia:
        # https://developers.google.com/workspace/drive/api/reference/rest/v3/files

        fields = ", ".join(FileDriver.model_fields.keys())

        try:
            data = self.service.files().get(fileId=file_id, fields=fields).execute()

            return FileDriver(**data)

        except HttpError:
            # La API devuelve HttpError tanto cuando el archivo no existe
            # como cuando existe pero el usuario no tiene permisos para acceder.
            logger.debug(f"El archivo {file_id} no existe o no se tiene acceso.")
            return None

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

    def update_file(
        self, file_id: str, content: Union[str, Path, bytes], mime_type: str
    ) -> FileDriver:
        """Actualiza un archivo en Drive con el contenido proporcionado."""
        bytes = self._resolver_content(content)
        data = self._upload_bytes(bytes, mime_type, file_id=file_id)
        return FileDriver(**data)

    def create_file(
        self,
        folder_id: str,
        file_name: str,
        content: Union[str, Path, bytes],
        mime_type: str,
    ) -> FileDriver:
        """Crea un archivo en Drive con el contenido proporcionado."""
        file_metadata = {
            "name": file_name,
            "parents": [folder_id],
            "mimeType": mime_type,
        }
        bytes = self._resolver_content(content)
        file = self._upload_bytes(bytes, mime_type, metadata=file_metadata)
        return FileDriver(**file)

    def delete_file(self, file_id: str) -> bool:
        try:
            self.service.files().delete(fileId=file_id).execute()
            return True
        except HttpError as error:
            logger.error(f"Error al eliminar archivo '{file_id}': {error}")
            return False

    def get_chat(self, chat_id: str) -> ChatIAStudio:
        content_bytes = self._download_bytes(chat_id)
        if not content_bytes:
            logger.debug(
                f"No se pudo obtener el contenido del chat con ID '{chat_id}'."
            )
            raise

        data = json.loads(content_bytes.decode("utf-8"))
        return ChatIAStudio(**data)

    def create_chat(self, folder_id: str, file_name: str, chat_data: ChatIAStudio):
        content_json = chat_data.model_dump_json(exclude_none=True, exclude_unset=True)
        return self.create_file(
            folder_id=folder_id,
            file_name=file_name,
            content=content_json,
            mime_type=self.MIME_PROMPT,
        )

    def update_chat(self, chat_id: str, chat_data: ChatIAStudio):
        """Actualiza el chat en Drive."""
        content_json = chat_data.model_dump_json(exclude_none=True, exclude_unset=True)
        self.update_file(
            file_id=chat_id,
            content=content_json,
            mime_type=self.MIME_PROMPT,
        )

    def has_pending_commit_suggestion(self, chat_id: str) -> bool:
        chat = self.get_chat(chat_id)
        if not chat:
            return False

        chunks = chat.chunkedPrompt.chunks
        for i in range(len(chunks) - 1, -1, -1):
            chunk = chunks[i]
            if isinstance(chunk, ChunkText) and COMMIT_TASK_MARKER in chunk.text:
                if i + 1 < len(chunks):
                    next_chunk = chunks[i + 1]
                    if getattr(next_chunk, "role", "") == "model":
                        return False
                return True
        return False

    def can_access_file(self, file_id):
        try:
            self.service.files().get(fileId=file_id, fields="id").execute()
            return True

        except HttpError as e:
            if e.resp.status == 404:
                return False
            raise

    def clear_chat(self, chat_id: str):
        # TODO: REFACTOR ESTE MÉTODO. QUÉ DOLOR ANALIZARLO!
        chat = self.get_chat(chat_id)

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
                    chunks[doc_idx + 1], ChunkText
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
        self.update_chat(chat_id, chat)
        return True

    def get_file_content(self, file_id: str) -> bytes:

        request = self.service.files().get_media(fileId=file_id)
        file_stream = io.BytesIO()
        downloader = MediaIoBaseDownload(file_stream, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
        return file_stream.getvalue()

    # def _list_files_by_query(
    #     self, query: str, fields: str = "files(id, name, mimeType)"
    # ) -> list[dict]:

    #     response = (
    #         self.service.files().list(q=query, spaces="drive", fields=fields).execute()
    #     )
    #     return response.get("files", [])

    # def list_files(self, folder_id: str = "root") -> list[dict]:
    #     items = []
    #     page_token = None
    #     try:
    #         while True:
    #             with self._lock:
    #                 response = (
    #                     self.service.files()
    #                     .list(
    #                         q=f"'{folder_id}' in parents and trashed = false",
    #                         spaces="drive",
    #                         fields="nextPageToken, files(id, name, mimeType, modifiedTime)",
    #                         pageToken=page_token,
    #                     )
    #                     .execute()
    #                 )
    #             items.extend(response.get("files", []))
    #             page_token = response.get("nextPageToken")
    #             if not page_token:
    #                 break
    #         return items
    #     except HttpError as error:
    #         logger.error(
    #             f"Error al listar archivos en la carpeta '{folder_id}': {error}"
    #         )
    #         return []

    # def find_files_by_query(
    #     self, query: str, fields: str = "files(id, name, mimeType)"
    # ) -> list[dict]:
    #     return self._list_files_by_query(query, fields)

    # def upload_binary_to_drive(
    #     self, folder_id: str, file_name: str, content: bytes, mime_type: str
    # ) -> Optional[dict]:
    #     file_metadata = {
    #         "name": file_name,
    #         "parents": [folder_id],
    #         "mimeType": mime_type,
    #     }
    #     return self._upload_bytes(content, mime_type, metadata=file_metadata)
