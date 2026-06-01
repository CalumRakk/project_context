import io
import json
import logging
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, List, Optional, cast

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

from project_context.exceptions import (
    AssociatedSecretMissingError,
    AuthenticationFailedError,
    FreshInstallRequiredError,
)
from project_context.profiles import profile_manager
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
    SCOPES = ["https://www.googleapis.com/auth/drive"]

    def __init__(
        self, secrets_file: Optional[Path] = None, profile_name: Optional[str] = None
    ):
        self._lock = threading.Lock()
        if secrets_file:
            self.client_secrets_file = secrets_file
            self.profile_name = profile_name or "temp_validation"
            self.credentials = self._authenticate_explicit()
        else:
            self.profile_name = profile_manager.get_active_profile_name()
            if not self.profile_name:
                raise FreshInstallRequiredError(
                    "No se ha configurado un perfil activo en el sistema."
                )
            self.client_secrets_file, _ = profile_manager.resolve_secrets_file()
            self.credentials = self._authenticate()

        self.service = build("drive", "v3", credentials=self.credentials)
        UI.success("Google Drive Manager inicializado con éxito.")

    def _authenticate(self) -> Credentials:
        """
        Orquesta el flujo de autenticación de forma declarativa.
        Sigue de manera secuencial los rombos de decisión del diagrama de flujo.
        """
        # ¿Existe token de sesión?
        creds = self._load_cached_credentials()

        # ¿Se puede usar o refrescar?
        if creds and self._validate_and_refresh_credentials(creds):
            UI.success("Conexión exitosa utilizando credenciales existentes.")
            return creds

        # ¿Tiene secreto asociado? e ¿Existe el secreto?
        self._verify_secret_files_readiness()

        # [Iniciar flujo OAuth]
        creds = self._run_interactive_oauth_flow()
        return creds

    def _load_cached_credentials(self) -> Optional[Credentials]:
        """Intenta leer el token local correspondiente al perfil activo."""
        profile_data = profile_manager.get_active_profile_data()
        registered_email = profile_data.get("email")
        secret_name = self.client_secrets_file.name

        if not registered_email:
            return None

        token_name = f"{registered_email}__{secret_name}"
        token_path = profile_manager.tokens_dir / token_name

        if not token_path.exists():
            return None

        try:
            return Credentials.from_authorized_user_file(str(token_path), self.SCOPES)
        except Exception as e:
            UI.warn(f"No se pudieron cargar las credenciales desde {token_name}: {e}")
            return None

    def _validate_and_refresh_credentials(self, creds: Credentials) -> bool:
        """Verifica la validez de las credenciales o intenta renovar el token de acceso."""
        if creds.valid:
            return True

        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())

                # Actualizar el archivo de token modificado en disco
                profile_data = profile_manager.get_active_profile_data()
                registered_email = profile_data.get("email")
                secret_name = self.client_secrets_file.name

                token_name = f"{registered_email}__{secret_name}"
                token_path = profile_manager.tokens_dir / token_name
                token_path.write_text(creds.to_json(), encoding="utf-8")

                UI.info("Token de acceso renovado automáticamente.")
                return True
            except Exception as e:
                UI.error(f"Fallo al refrescar el token de acceso: {e}")

        return False

    def _verify_secret_files_readiness(self) -> None:
        """Garantiza la presencia del archivo físico de secretos necesario para OAuth."""
        if not self.client_secrets_file.name:
            raise AssociatedSecretMissingError(
                f"El perfil '{self.profile_name}' no tiene un secreto asociado."
            )

        if not self.client_secrets_file.exists():
            raise AssociatedSecretMissingError(
                f"No se encontró el archivo de credenciales '{self.client_secrets_file.name}'.\n"
                f"Ruta esperada: {self.client_secrets_file}"
            )

    def _run_interactive_oauth_flow(self) -> Credentials:
        """Levanta el servidor local interactivo de Google y gestiona la validación del usuario."""
        UI.info("Iniciando flujo de autenticación interactivo de Google Drive...")
        try:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(self.client_secrets_file), self.SCOPES
            )
            creds = cast(Credentials, flow.run_local_server(port=0))
        except Exception as e:
            raise AuthenticationFailedError(
                f"El flujo de autenticación OAuth interactivo fue cancelado o falló: {e}"
            )

        # Validación del correo obtenido (¿Funcionó?)
        fetched_email = self._fetch_user_email(creds)
        self._verify_email_consistency(fetched_email)
        self._save_authorized_token(fetched_email, creds)

        return creds

    def _fetch_user_email(self, creds: Credentials) -> str:
        """Consulta el endpoint 'about' de Google para obtener la dirección de correo electrónico."""
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
                f"Se completó la autenticación, pero falló la validación de la sesión de usuario: {e}"
            )

    def _verify_email_consistency(self, fetched_email: str) -> None:
        """Valida que la identidad autenticada coincida con el registro del perfil."""
        profile_data = profile_manager.get_active_profile_data()
        registered_email = profile_data.get("email")

        if registered_email and fetched_email.lower() != registered_email.lower():
            raise ValueError(
                f"Conflicto de seguridad: La cuenta de Google autenticada ({fetched_email}) "
                f"no coincide con el correo asignado a este perfil ({registered_email}).\n"
                f"Por favor, cambia de perfil o vuelve a configurar las credenciales."
            )

        if not registered_email:
            profile_data["email"] = fetched_email
            profile_manager.save_active_profile_data(profile_data)
            UI.info(
                f"Correo electrónico [bold]{fetched_email}[/] asociado al perfil '{self.profile_name}'."
            )

    def _save_authorized_token(self, email: str, creds: Credentials) -> None:
        """Guarda el token autorizado en el directorio de credenciales para futuras sesiones."""
        secret_name = self.client_secrets_file.name
        token_name = f"{email}__{secret_name}"
        token_path = profile_manager.tokens_dir / token_name

        try:
            token_path.write_text(creds.to_json(), encoding="utf-8")
            UI.success("Token de acceso guardado de forma segura.")
        except Exception as e:
            UI.error(
                f"Fallo al registrar el token de sesión en el almacenamiento local: {e}"
            )

    def _authenticate_explicit(self) -> Credentials:
        """Flujo simplificado que autentica directamente usando un archivo de secretos."""
        if not self.client_secrets_file.exists():
            raise FileNotFoundError(
                f"No se encontró el archivo de secretos: {self.client_secrets_file}"
            )

        flow = InstalledAppFlow.from_client_secrets_file(
            str(self.client_secrets_file), self.SCOPES
        )
        creds = cast(Credentials, flow.run_local_server(port=0))

        temp_service = build("drive", "v3", credentials=creds)
        about_info = temp_service.about().get(fields="user(emailAddress)").execute()
        self.fetched_email = about_info.get("user", {}).get("emailAddress")

        if not self.fetched_email:
            raise ValueError(
                "No se pudo recuperar el correo asociado a estas credenciales."
            )

        return creds

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

    def update_file_from_memory(
        self, file_id: str, content: str, mime_type: str
    ) -> Optional[dict]:
        with self._lock:
            updated_file = self._upload_to_drive(
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
        file = self._upload_to_drive(
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
        """Obtiene metadatos de un archivo en Drive de forma segura."""
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

    def find_files_by_query(
        self, query: str, fields: str = "files(id, name, mimeType)"
    ) -> list[dict]:
        """Busca archivos en Drive utilizando un filtro query estándar."""
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

    def delete_file(self, file_id: str) -> bool:
        """Elimina un archivo de Google Drive dado su ID."""
        try:
            self.service.files().delete(fileId=file_id).execute()
            return True
        except HttpError as error:
            logger.error(f"Error al eliminar archivo '{file_id}': {error}")
            return False

    def _upload_to_drive(
        self,
        content: bytes,
        mime_type: str,
        metadata: Optional[dict] = None,
        file_id: Optional[str] = None,
        fields: str = "id, name",
    ) -> Optional[dict]:
        """Centraliza el flujo de subida y actualización de archivos en Google Drive."""
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

    def upload_binary_to_drive(
        self, folder_id: str, file_name: str, content: bytes, mime_type: str
    ) -> Optional[dict]:
        file_metadata = {
            "name": file_name,
            "parents": [folder_id],
            "mimeType": mime_type,
        }
        return self._upload_to_drive(content, mime_type, metadata=file_metadata)


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
            logger.debug(
                f"La carpeta '{self.AI_STUDIO_FOLDER_NAME}' no fue encontrada."
            )
            return None
        return folder.get("id")

    def get_chat_ia_studio(self, chat_id: str) -> Optional[ChatIAStudio]:
        content_bytes = self.gdm.get_file_content(chat_id)
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
        self, file_name: str, chat_data: ChatIAStudio
    ) -> Optional[str]:
        content_json = chat_data.model_dump_json(exclude_none=True, exclude_unset=True)
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
            content_json = chat_data.model_dump_json(
                exclude_none=True, exclude_unset=True
            )
            result = self.gdm.update_file_from_memory(
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
