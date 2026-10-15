import json
import logging
from dataclasses import dataclass, field

from project_context.core.exceptions import ChatSessionError, ProfileNotFoundError
from project_context.core.profile_mg import ProfileManager
from project_context.core.project_context import ProjectContext
from project_context.core.schemas import ChatIAStudio, ProjectState
from project_context.services.api_drive import FileDriver, GoogleDriveManager
from project_context.services.auth_service import AuthService
from project_context.ui import UI
from project_context.utils import compute_md5, decompress_data

logger = logging.getLogger(__name__)


@dataclass
class ReplicationSummary:
    target_profile: str
    target_email: str
    cloned_chat_id: str
    cloned_chat_url: str
    total_dependencies: int
    reused_dependencies: int
    uploaded_dependencies: int
    mapped_ids: dict[str, str] = field(default_factory=dict)


class ReplicationService:
    """
    Servicio encargado de clonar un chat activo de Google AI Studio hacia otra cuenta/perfil,
    realizando un análisis de dependencias de Drive, deduplicación por MD5 y traducción de IDs.
    """

    def __init__(
        self, source_api: GoogleDriveManager, source_project_context: ProjectContext
    ):
        self.source_api = source_api
        self.source_project_context = source_project_context
        self.profile_manager = ProfileManager()

    def _get_cas_blob(self, md5sum: str) -> bytes | None:
        """Intenta recuperar el archivo directamente del Content Addressable Storage local."""
        if not md5sum:
            return None
        prefix = md5sum[:2]
        suffix = md5sum[2:]
        obj_path = self.source_project_context.objects_dir / prefix / f"{suffix}.z"
        if obj_path.exists():
            try:
                return decompress_data(obj_path.read_bytes())
            except Exception as e:
                logger.debug(f"No se pudo descomprimir desde CAS {md5sum}: {e}")
        return None

    def replicate_chat_to_profile(self, target_profile_name: str) -> ReplicationSummary:
        """Orquesta el flujo integral de clonación hacia el perfil destino."""
        self._validate_preconditions(target_profile_name)

        source_state = self.source_project_context.load_state()
        assert source_state.chat_id is not None

        # 1. Autenticar perfil destino validando identidad
        UI.info(f"Autenticando perfil destino: [bold cyan]{target_profile_name}[/]...")
        target_profile_data = self.profile_manager.load_profile_data(
            target_profile_name
        )
        target_creds = AuthService.authenticate(
            target_profile_data.token_path,
            target_profile_data.secret_path,
            expected_email=target_profile_data.email,
        )
        target_api = GoogleDriveManager(target_creds)

        # 2. Descargar chat origen e identificar dependencias
        UI.info("Descargando chat origen e inspeccionando dependencias...")
        source_chat = self.source_api.get_chat(source_state.chat_id)
        source_chat_meta = self.source_api.get_metadata(source_state.chat_id)

        referenced_file_ids = self._extract_dependencies(source_chat, source_state)
        logger.info(
            f"REPLICATION_DEPS_FOUND: {len(referenced_file_ids)} archivo(s) de Drive detectados en el chat."
        )

        # 3. Indexar catálogo de archivos en destino (con paginación completa)
        UI.info("Consultando catálogo de archivos en la cuenta destino...")
        target_folder_id = target_api.ai_studio_folder
        target_files = target_api.list_files_in_folder(target_folder_id)

        target_md5_index: dict[str, FileDriver] = {}
        for f in target_files:
            if f.md5sum:
                target_md5_index[f.md5sum] = f

        # 4. Transferir o reutilizar dependencias
        id_map: dict[str, str] = {}
        reused_count = 0
        uploaded_count = 0

        for file_id in referenced_file_ids:
            source_meta = self.source_api.get_metadata(file_id)
            if not source_meta:
                UI.warn(f"No se pudieron leer metadatos de '{file_id}'. Se omitirá.")
                continue

            filename = source_meta.filename
            md5 = source_meta.md5sum
            mime_type = source_meta.mimetype

            if md5 and md5 in target_md5_index:
                target_existing = target_md5_index[md5]
                id_map[file_id] = target_existing.file_id
                reused_count += 1
                logger.info(
                    f"REPLICATION_REUSE: '{filename}' (MD5: {md5}) reutilizado con ID {target_existing.file_id}"
                )
            else:
                # Intentar leer desde CAS local; si no, descargar de Drive
                content_bytes = self._get_cas_blob(md5)
                if content_bytes is None:
                    try:
                        UI.info(
                            f"Descargando desde Drive origen: [dim]{filename}[/]..."
                        )
                        content_bytes = self.source_api.get_file_content(file_id)
                    except Exception as e:
                        UI.error(
                            f"Error descargando '{filename}' ({file_id}): {e}. Se omitirá."
                        )
                        continue

                UI.info(f"Subiendo a cuenta destino: [cyan]{filename}[/]...")
                new_file = target_api.create_file(
                    folder_id=target_folder_id,
                    file_name=filename,
                    content=content_bytes,
                    mime_type=mime_type,
                )
                id_map[file_id] = new_file.file_id
                if md5:
                    target_md5_index[md5] = new_file
                uploaded_count += 1
                logger.info(
                    f"REPLICATION_UPLOADED: '{filename}' subido con ID {new_file.file_id}"
                )

        # 5. Traducir referencias dentro de los Chunks
        UI.info("Traduciendo referencias del chat para la nueva cuenta...")
        cloned_chat = self._translate_chat(source_chat, id_map)

        # Conservar el nombre original del archivo de chat si es posible
        chat_filename = (
            source_chat_meta.filename
            if source_chat_meta
            else f"{self.source_project_context.project_path.name}_chat.prompt"
        )

        UI.info("Creando archivo de chat en Google AI Studio de la cuenta destino...")
        target_chat_file = target_api.create_chat(
            folder_id=target_folder_id,
            file_name=chat_filename,
            chat_data=cloned_chat,
        )

        # 6. Persistir el estado para el perfil destino
        target_context_file_id = id_map.get(source_state.file_id or "")
        self._update_target_state(
            target_email=target_profile_data.email,
            target_chat_id=target_chat_file.id,
            target_context_file_id=target_context_file_id,
        )

        chat_url = f"https://aistudio.google.com/prompts/{target_chat_file.id}"

        return ReplicationSummary(
            target_profile=target_profile_name,
            target_email=target_profile_data.email,
            cloned_chat_id=target_chat_file.id,
            cloned_chat_url=chat_url,
            total_dependencies=len(referenced_file_ids),
            reused_dependencies=reused_count,
            uploaded_dependencies=uploaded_count,
            mapped_ids=id_map,
        )

    def _validate_preconditions(self, target_profile_name: str) -> None:
        """Verifica que el entorno sea consistente antes de comenzar la replicación."""
        available_profiles = self.profile_manager.list_profiles()
        if target_profile_name not in available_profiles:
            raise ProfileNotFoundError(
                f"El perfil destino '{target_profile_name}' no existe. "
                f"Perfiles disponibles: {', '.join(available_profiles)}"
            )

        target_profile_data = self.profile_manager.load_profile_data(
            target_profile_name
        )
        if (
            target_profile_data.email.strip().lower()
            == self.source_project_context.email.strip().lower()
        ):
            raise ValueError(
                "No puedes clonar el chat hacia el mismo perfil o correo activo actual."
            )

        source_state = self.source_project_context.load_state()
        if not source_state.chat_id:
            raise ChatSessionError(
                "No existe una sesión de chat activa en el proyecto actual para clonar."
            )

        backup_path = self.source_project_context.local_dir / "chat_backup.prompt"
        if backup_path.exists():
            raise ChatSessionError(
                "El chat actual se encuentra en un modo temporal (como modo commit). "
                "Ejecuta 'commit --restore' o 'update' antes de clonarlo."
            )

    def _extract_dependencies(
        self, chat: ChatIAStudio, state: ProjectState
    ) -> set[str]:
        deps: set[str] = set()
        if state.file_id:
            deps.add(state.file_id)

        for chunk in chat.chunkedPrompt.chunks:
            if chunk.file_id:
                deps.add(chunk.file_id)

        return deps

    def _translate_chat(
        self, source_chat: ChatIAStudio, id_map: dict[str, str]
    ) -> ChatIAStudio:
        chat_dict = json.loads(
            source_chat.model_dump_json(exclude_none=True, exclude_unset=True)
        )
        cloned = ChatIAStudio(**chat_dict)

        for chunk in cloned.chunkedPrompt.chunks:
            if chunk.file_id and chunk.file_id in id_map:
                chunk.file_id = id_map[chunk.file_id]

        cloned.reset_context_document_tokencount()
        return cloned

    def _update_target_state(
        self,
        target_email: str,
        target_chat_id: str,
        target_context_file_id: str | None,
    ) -> None:
        target_identity_hash = compute_md5(target_email.strip().lower())
        target_state_path = (
            self.source_project_context.local_dir / f"state_{target_identity_hash}.json"
        )

        target_state = ProjectState(state_path=target_state_path)
        if target_state_path.exists():
            try:
                data = json.loads(target_state_path.read_text(encoding="utf-8"))
                target_state = ProjectState(**data, state_path=target_state_path)
            except Exception:
                pass

        target_state.chat_id = target_chat_id
        if target_context_file_id:
            target_state.file_id = target_context_file_id

        target_state.save()
        logger.info(
            f"REPLICATION_TARGET_STATE_SAVED: {target_state_path.name} actualizado para '{target_email}'."
        )
