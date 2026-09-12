import enum
import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import List, Optional, cast

from project_context.core.database import Snapshot, SnapshotAsset, db
from project_context.core.project_context import ProjectContext
from project_context.core.schemas import ChatIAStudio
from project_context.services.api_drive import GoogleDriveManager
from project_context.utils import compress_data, compute_md5, decompress_data

logger = logging.getLogger(__name__)


class SnapCategory(enum.Enum):
    USER = "user"
    SYSTEM = "system"
    COMMIT = "commit"


class SnapshotManager:
    """
    Administrador de la lógica de negocio de snapshots.
    Sigue un modelo CAS (Content Addressable Storage) para almacenar archivos
    binarios en el disco de forma eficiente y soporta accesos multiusuario transparentes.
    """

    def __init__(self, api: GoogleDriveManager, project_context: ProjectContext):
        self.api = api
        self.project_context = project_context

    def initialize_schema(self):
        """
        Ejecuta de forma explícita las migraciones de esquema y datos heredados.
        Debe invocarse dentro de un bloque activo de `DatabaseSession`.
        """
        self._migrate_schema()
        self._migrate_legacy_snapshots()
        return self

    def _migrate_schema(self):
        """Inspecciona la base de datos para detectar esquemas antiguos de versiones anteriores."""
        with db.connection_context():
            try:
                # Si la tabla no existe aún, es una instalación limpia; no hay nada que migrar
                if not db.table_exists("snapshot"):
                    return

                columns = [c.name for c in db.get_columns("snapshot")]

                # DETECCIÓN DE ESQUEMA ANTIGUO (Existencia de 'timestamp' o ausencia de 'creator_email')
                if columns and (
                    "timestamp" in columns or "creator_email" not in columns
                ):
                    import sys

                    from project_context.ui import UI

                    UI.error(
                        "Se ha detectado una base de datos de snapshots antigua e incompatible.\n"
                        "Para conservar tu historial de snapshots, debes migrar la base de datos.\n"
                        "Por favor, ejecuta el script de migración ejecutando el siguiente comando:\n"
                        "  [bold yellow]python -m project_context.scripts.migrate_db[/]",
                        spacing="block",
                    )
                    sys.exit(1)

            except Exception as e:
                logger.debug(f"Error al verificar o migrar el esquema: {e}")

    def _get_object_path(self, md5sum: str) -> Path:
        prefix = md5sum[:2]
        suffix = md5sum[2:]
        return self.project_context.objects_dir / prefix / f"{suffix}.z"

    def _store_object(self, data: bytes) -> str:
        """Guarda un objeto en CAS local."""
        md5 = compute_md5(data)
        obj_path = self._get_object_path(md5)
        if not obj_path.exists():
            obj_path.parent.mkdir(parents=True, exist_ok=True)
            compressed = compress_data(data)
            obj_path.write_bytes(compressed)
        return md5

    def _retrieve_object(self, md5sum: str) -> Optional[bytes]:
        """Recupera un objeto almacenado en CAS local."""
        obj_path = self._get_object_path(md5sum)
        if not obj_path.exists():
            logger.error(
                f"CAS_OBJECT_MISSING: No se encontró el objeto con hash {md5sum} en '{obj_path}'"
            )
            return None
        try:
            compressed = obj_path.read_bytes()
            data = decompress_data(compressed)
            logger.debug(
                f"CAS_OBJECT_RETRIEVED: {md5sum} ({len(data)} bytes descomprimidos)"
            )
            return data
        except Exception as e:
            logger.exception(
                f"CAS_DECOMPRESS_ERROR: Fallo al descomprimir {md5sum} desde '{obj_path}': {e}"
            )
            return None

    def replicate_snapshot_assets(self, snapshot: Snapshot) -> dict[str, str]:
        """
        Garantiza que el usuario activo tenga todos los activos del snapshot en su Drive,
        resolviendo enlaces rotos y evitando subidas duplicadas de archivos que ya coincidan
        con el hash del CAS. Devuelve el mapa de traducción de IDs.
        """
        active_email = self.project_context.email
        creator_assets = snapshot.get_assets_from_creator()

        logger.info(
            f"REPLICATE_START: Snapshot ID={snapshot.id} | Creador='{snapshot.creator_email}' | Usuario activo='{active_email}'"
        )

        if not creator_assets:
            logger.error(
                f"REPLICATE_ERROR: No se encontraron activos registrados en la DB para el Snapshot ID={snapshot.id}"
            )
            raise ValueError("No se encontraron activos asociados a este snapshot.")

        # Cargar el estado actual para verificar si podemos reutilizar los archivos activos
        state = self.project_context.load_state()
        current_chat_id = state.chat_id
        current_file_id = state.file_id

        logger.debug(
            f"REPLICATE_CURRENT_STATE: chat_id='{current_chat_id}', file_id='{current_file_id}'"
        )

        id_map = {}

        non_chat_assets = [a for a in creator_assets if a.role != "chat"]
        chat_assets = [a for a in creator_assets if a.role == "chat"]

        logger.info(
            f"REPLICATE_BREAKDOWN: Total={len(creator_assets)} activos (Soporte/Contexto={len(non_chat_assets)}, Chat={len(chat_assets)})"
        )

        # Sincronizar activos de soporte (Contexto, Adjuntos/Imágenes)

        for asset in non_chat_assets:
            logger.info(
                f"ASSET_PROCESS: rol='{asset.role}' | archivo='{asset.filename}' | md5={asset.md5sum} | original_id='{asset.file_id}'"
            )

            existing_asset = SnapshotAsset.get_or_none(
                SnapshotAsset.snapshot == snapshot,
                SnapshotAsset.email == active_email,
                SnapshotAsset.role == asset.role,
                SnapshotAsset.filename == asset.filename,
            )

            target_file_id = None
            needs_upload = True

            # Si es el documento de contexto y tenemos un archivo activo en el estado, lo reutilizamos
            if (
                asset.role == "context"
                and current_file_id
                and self.api.can_access_file(current_file_id)
            ):
                target_file_id = current_file_id
                needs_upload = True
                logger.info(
                    f"ASSET_REUSE_ACTIVE: Reutilizando file_id activo actual '{current_file_id}' para sobrescribir con el snapshot."
                )
            elif existing_asset:
                meta = self.api.get_metadata(existing_asset.file_id)
                if meta:
                    target_file_id = existing_asset.file_id
                    if meta.md5sum == asset.md5sum:
                        needs_upload = False
                        logger.info(
                            f"ASSET_SYNC_SKIP: '{asset.filename}' ya está sincronizado en Drive (hash coincidente: {asset.md5sum}). Reutilizando ID '{target_file_id}'."
                        )
                    else:
                        logger.info(
                            f"ASSET_SYNC_DIFF: '{asset.filename}' difiere en Drive (Drive MD5={meta.md5sum} vs CAS MD5={asset.md5sum}). Se actualizará."
                        )
                else:
                    logger.warning(
                        f"ASSET_BROKEN_LINK: Enlace roto en Drive para '{asset.filename}' (ID '{existing_asset.file_id}'). Se creará de nuevo."
                    )

            if needs_upload:
                logger.debug(
                    f"CAS_RETRIEVE: Obteniendo blob local para MD5={asset.md5sum}..."
                )
                content = self._retrieve_object(asset.md5sum)
                if not content:
                    logger.critical(
                        f"CAS_MISSING_OBJECT: No se encontró el objeto físico comprimido en disco para MD5={asset.md5sum} ('{asset.filename}')"
                    )
                    raise ValueError(
                        f"No se pudo recuperar el contenido del CAS local para el MD5: {asset.md5sum}"
                    )

                if target_file_id:
                    logger.info(
                        f"DRIVE_UPDATE: Actualizando archivo '{asset.filename}' (ID: {target_file_id}, {len(content)} bytes)..."
                    )
                    updated_file = self.api.update_file(
                        target_file_id, content, asset.mime_type
                    )
                    target_file_id = updated_file.id
                else:
                    logger.info(
                        f"DRIVE_CREATE: Subiendo nuevo archivo '{asset.filename}' ({len(content)} bytes) a carpeta AI Studio..."
                    )
                    cloned_file = self.api.create_file(
                        folder_id=self.api.ai_studio_folder,
                        file_name=asset.filename,
                        content=content,
                        mime_type=asset.mime_type,
                    )
                    target_file_id = cloned_file.id

                # Persistencia de la referencia en DB
                if existing_asset:
                    existing_asset.file_id = target_file_id
                    existing_asset.md5sum = asset.md5sum
                    existing_asset.modified_at = datetime.now()
                    existing_asset.save()
                    logger.debug(
                        f"ASSET_DB_UPDATE: SnapshotAsset ID={existing_asset.id} actualizado con file_id='{target_file_id}'"
                    )
                else:
                    new_asset = SnapshotAsset.create(
                        snapshot=snapshot,
                        file_id=target_file_id,
                        filename=asset.filename,
                        mime_type=asset.mime_type,
                        md5sum=asset.md5sum,
                        email=active_email,
                        role=asset.role,
                        modified_at=datetime.now(),
                    )
                    logger.debug(
                        f"ASSET_DB_CREATE: Nuevo SnapshotAsset ID={new_asset.id} para usuario '{active_email}'"
                    )

            id_map[asset.file_id] = target_file_id
            logger.debug(f"ID_MAP_REGISTERED: {asset.file_id} -> {target_file_id}")

        # Sincronizar el Chat traduciendo sus referencias internas

        for chat_asset in chat_assets:
            logger.info(
                f"CHAT_PROCESS: archivo='{chat_asset.filename}' | md5={chat_asset.md5sum} | original_id='{chat_asset.file_id}'"
            )

            existing_chat_asset = SnapshotAsset.get_or_none(
                SnapshotAsset.snapshot == snapshot,
                SnapshotAsset.email == active_email,
                SnapshotAsset.role == "chat",
            )

            chat_bytes = self._retrieve_object(chat_asset.md5sum)
            if not chat_bytes:
                logger.critical(
                    f"CAS_MISSING_OBJECT: No se encontró el JSON del chat en el CAS local (MD5: {chat_asset.md5sum})"
                )
                raise ValueError(
                    f"No se pudo recuperar el chat desde el CAS para el MD5: {chat_asset.md5sum}"
                )

            logger.debug(
                f"CHAT_DESERIALIZE: Deserializando estructura de chat ({len(chat_bytes)} bytes)..."
            )
            chat_data = json.loads(chat_bytes.decode("utf-8"))
            chat_model = ChatIAStudio(**chat_data)

            translated_chunks_count = 0
            for chunk in chat_model.chunkedPrompt.chunks:
                if chunk.file_id is not None:
                    translated_id = id_map.get(chunk.file_id)
                    if translated_id:
                        logger.debug(
                            f"CHUNK_TRANSLATE: {chunk.file_id} -> {translated_id}"
                        )
                        chunk.file_id = translated_id
                        translated_chunks_count += 1
                    else:
                        logger.warning(
                            f"CHUNK_TRANSLATE_MISSED: No existe reemplazo en id_map para file_id={chunk.file_id}"
                        )

            logger.info(
                f"CHAT_TRANSLATION_DONE: {translated_chunks_count} referencias de archivos actualizadas en los chunks del chat."
            )

            # Forzar explícitamente que el Chunk de contexto del chat sea el ID del contexto restaurado
            context_target_id = None
            for non_chat in non_chat_assets:
                if non_chat.role == "context":
                    context_target_id = id_map.get(non_chat.file_id)
                    break

            if context_target_id:
                for chunk in chat_model.chunkedPrompt.chunks:
                    if chunk.is_document or hasattr(chunk, "driveDocument"):
                        chunk.file_id = context_target_id
                        logger.info(
                            f"CHAT_CONTEXT_BOUND: Vinculado ChunkDocument al context_target_id='{context_target_id}'"
                        )
                        break

            translated_content = chat_model.model_dump_json(
                exclude_none=True, exclude_unset=True
            ).encode("utf-8")

            target_chat_id = None
            needs_chat_upload = True

            # Si tenemos un chat activo en el estado, lo reutilizamos para preservar la URL
            if current_chat_id and self.api.can_access_file(current_chat_id):
                target_chat_id = current_chat_id
                logger.info(
                    f"CHAT_REUSE_ACTIVE: Reutilizando chat_id activo '{current_chat_id}' para preservar la URL en el navegador."
                )
            elif existing_chat_asset:
                meta = self.api.get_metadata(existing_chat_asset.file_id)
                if meta:
                    target_chat_id = existing_chat_asset.file_id
                    logger.info(
                        f"CHAT_EXISTING_FOUND: Encontrado chat en Drive con ID '{target_chat_id}'."
                    )
                else:
                    logger.warning(
                        f"CHAT_BROKEN_LINK: Chat en Drive inaccesible (ID '{existing_chat_asset.file_id}'). Se creará uno nuevo."
                    )

            if needs_chat_upload:
                if target_chat_id:
                    logger.info(
                        f"DRIVE_UPDATE_CHAT: Sobrescribiendo contenido del chat ID='{target_chat_id}' ({len(translated_content)} bytes)..."
                    )
                    self.api.update_file(
                        target_chat_id, translated_content, chat_asset.mime_type
                    )
                else:
                    logger.info(
                        "DRIVE_CREATE_CHAT: Creando nuevo recurso Chat en Google Drive..."
                    )
                    cloned_chat = self.api.create_file(
                        folder_id=self.api.ai_studio_folder,
                        file_name=chat_asset.filename,
                        content=translated_content,
                        mime_type=chat_asset.mime_type,
                    )
                    target_chat_id = cloned_chat.id

                # Guardamos también el contenido traducido en el CAS local para no romper la referencia
                translated_md5 = self._store_object(translated_content)

                # Actualización de la referencia del chat en DB
                if existing_chat_asset:
                    existing_chat_asset.file_id = target_chat_id
                    existing_chat_asset.md5sum = translated_md5
                    existing_chat_asset.modified_at = datetime.now()
                    existing_chat_asset.save()
                    logger.debug(
                        f"CHAT_DB_UPDATE: SnapshotAsset ID={existing_chat_asset.id} actualizado a file_id='{target_chat_id}'"
                    )
                else:
                    new_chat_asset = SnapshotAsset.create(
                        snapshot=snapshot,
                        file_id=target_chat_id,
                        filename=chat_asset.filename,
                        mime_type=chat_asset.mime_type,
                        md5sum=translated_md5,
                        email=active_email,
                        role="chat",
                        modified_at=datetime.now(),
                    )
                    logger.debug(
                        f"CHAT_DB_CREATE: Nuevo SnapshotAsset ID={new_chat_asset.id} registrado para chat"
                    )

            id_map[chat_asset.file_id] = target_chat_id

        logger.info(
            f"REPLICATE_COMPLETED: Replicación finalizada con éxito. Total mapeos={len(id_map)}"
        )
        return id_map

    def restore_snapshot(self, snapshot_id: str | int) -> bool:
        """
        Restaura el entorno de Drive y los archivos locales usando el snapshot id de forma multiusuario.
        """
        logger.info(
            f"RESTORE_START: Iniciando proceso de restauración para snapshot_id='{snapshot_id}'"
        )
        try:
            snap = cast(Snapshot, Snapshot.get_or_none(Snapshot.id == int(snapshot_id)))
            if not snap:
                logger.error(
                    f"RESTORE_NOT_FOUND: No se encontró ningún snapshot con ID={snapshot_id} en la base de datos SQLite."
                )
                return False

            active_email = self.project_context.email
            logger.info(
                f"RESTORE_FOUND: Snapshot ID={snap.id} | Creador='{snap.creator_email}' | Mensaje='{snap.message}' | Solicitante='{active_email}'"
            )

            # Sincronización idempotente de activos
            self.replicate_snapshot_assets(snap)

            # Recuperar las referencias de activos definitivas para el usuario activo
            chat_asset = SnapshotAsset.get_or_none(
                SnapshotAsset.snapshot == snap,
                SnapshotAsset.email == active_email,
                SnapshotAsset.role == "chat",
            )
            context_asset = SnapshotAsset.get_or_none(
                SnapshotAsset.snapshot == snap,
                SnapshotAsset.email == active_email,
                SnapshotAsset.role == "context",
            )

            if not chat_asset or not context_asset:
                logger.critical(
                    f"RESTORE_RESOLVE_FAILED: No se pudieron resolver las referencias esenciales en DB (chat_asset={chat_asset}, context_asset={context_asset}) para '{active_email}'."
                )
                return False

            # Actualizar el archivo de estado local
            state = self.project_context.load_state()
            state.chat_id = chat_asset.file_id
            state.file_id = context_asset.file_id
            state.save()

            logger.info(
                f"RESTORE_SUCCESS: Snapshot {snapshot_id} restaurado con éxito. Estado actualizado -> chat_id='{state.chat_id}', file_id='{state.file_id}'"
            )
            return True

        except Exception as e:
            logger.exception(
                f"RESTORE_EXCEPTION: Fallo inesperado durante la restauración del snapshot {snapshot_id}: {e}"
            )
            return False

    def get_all_snapshot_ids(self) -> List[str]:
        """Obtiene los identificadores numéricos de snapshot en orden inverso."""
        try:
            query = Snapshot.select(Snapshot.id).order_by(Snapshot.id.desc())
            return [str(snap.id) for snap in query]
        except Exception as e:
            logger.debug(f"[Error] Fallo al consultar identificadores: {e}")
            return []

    def get_snapshot_info(self, timestamp: str) -> Optional[dict]:
        """Carga el registro con los metadatos dinámicos del snapshot."""
        try:
            if timestamp.isdigit():
                snap = Snapshot.get_or_none(Snapshot.id == int(timestamp))
            else:
                snap = Snapshot.get_or_none(Snapshot.id == timestamp)

            if snap:
                human_time = datetime.fromtimestamp(snap.created_at).strftime(
                    "%H:%M:%S - %d/%m/%Y"
                )

                # Intentamos recuperar el MD5 del contexto de este snapshot.
                active_email = self.project_context.email
                context_asset = SnapshotAsset.get_or_none(
                    SnapshotAsset.snapshot == snap,
                    SnapshotAsset.email == active_email,
                    SnapshotAsset.role == "context",
                )
                if not context_asset:
                    context_asset = SnapshotAsset.get_or_none(
                        SnapshotAsset.snapshot == snap,
                        SnapshotAsset.email == snap.creator_email,
                        SnapshotAsset.role == "context",
                    )
                context_hash = context_asset.md5sum if context_asset else ""

                drive_mod = (
                    snap.drive_modified_time
                    if hasattr(snap, "drive_modified_time")
                    else snap.updated_at
                )
                drive_mod_str = (
                    drive_mod.isoformat()
                    if isinstance(drive_mod, datetime)
                    else str(drive_mod)
                )

                return {
                    "timestamp": str(snap.id),
                    "human_time": human_time,
                    "drive_modified_time": drive_mod_str,
                    "context_md5": context_hash,
                    "message": snap.message,
                    "category": getattr(snap, "category", "user"),
                    "creator_email": snap.creator_email,
                }
        except Exception as e:
            logger.debug(f"[Error] Fallo al consultar el snapshot: {e}")
        return None

    def list_snapshots(self) -> List[dict]:
        """Devuelve la lista total de snapshots registrados con formato de UI dinámico."""

        query = Snapshot.select().order_by(Snapshot.id.desc())
        results = []
        active_email = self.project_context.email
        for snap in query:
            human_time = snap.created_at.strftime("%H:%M:%S - %d/%m/%Y")

            context_asset = SnapshotAsset.get_or_none(
                SnapshotAsset.snapshot == snap,
                SnapshotAsset.email == active_email,
                SnapshotAsset.role == "context",
            )
            if not context_asset:
                context_asset = SnapshotAsset.get_or_none(
                    SnapshotAsset.snapshot == snap,
                    SnapshotAsset.email == snap.creator_email,
                    SnapshotAsset.role == "context",
                )
            context_hash = context_asset.md5sum if context_asset else ""

            drive_mod = (
                snap.drive_modified_time
                if hasattr(snap, "drive_modified_time")
                else snap.updated_at
            )
            drive_mod_str = (
                drive_mod.isoformat()
                if isinstance(drive_mod, datetime)
                else str(drive_mod)
            )

            results.append(
                {
                    "timestamp": str(snap.id),
                    "human_time": human_time,
                    "drive_modified_time": drive_mod_str,
                    "context_md5": context_hash,
                    "message": snap.message,
                    "category": getattr(snap, "category", "user"),
                    "creator_email": snap.creator_email,
                }
            )
        return results

    def delete_snapshot(self, timestamp: str) -> bool:
        """Elimina el registro de la base de datos y purga los objetos obsoletos del CAS."""
        try:
            if timestamp.isdigit():
                snap = Snapshot.get_or_none(Snapshot.id == int(timestamp))
            else:
                snap = Snapshot.get_or_none(Snapshot.id == timestamp)

            if snap:
                snap.delete_instance(recursive=True)
                self.prune_objects()
                return True
        except Exception as e:
            logger.debug(f"[Error] No se pudo eliminar el snapshot: {e}")
        return False

    def rename_snapshot(self, timestamp: str, new_message: str) -> bool:
        """Modifica la descripción de un snapshot."""
        try:
            if timestamp.isdigit():
                q = Snapshot.update({Snapshot.message: new_message}).where(
                    Snapshot.id == int(timestamp)
                )
            else:
                q = Snapshot.update({Snapshot.message: new_message}).where(
                    Snapshot.id == timestamp
                )
            q.execute()
            return True
        except Exception as e:
            logger.debug(f"[Error] No se pudo renombrar el snapshot: {e}")
        return False

    def prune_objects(self) -> int:
        """Elimina físicamente del CAS local todos los archivos no referenciados."""
        referenced_hashes = set()
        try:
            for asset in SnapshotAsset.select(SnapshotAsset.md5sum):
                referenced_hashes.add(asset.md5sum)
        except Exception as e:
            logger.debug(f"[Error] No se pudieron leer las referencias activas: {e}")
            return 0

        deleted_count = 0
        if not self.project_context.objects_dir.exists():
            return 0

        for path in self.project_context.objects_dir.glob("**/*.z"):
            if path.is_file():
                folder_name = path.parent.name
                file_name = path.stem
                file_hash = f"{folder_name}{file_name}"
                if file_hash not in referenced_hashes:
                    try:
                        path.unlink()
                        deleted_count += 1
                        try:
                            path.parent.rmdir()
                        except OSError:
                            pass
                    except Exception as e:
                        logger.debug(f"[Error] No se pudo eliminar {path.name}: {e}")
        return deleted_count

    def _migrate_legacy_snapshots(self):
        """Limpia los directorios obsoletos para evitar redundancia de espacio local."""
        if not self.project_context.snapshots_dir.exists():
            return

        for d in self.project_context.snapshots_dir.iterdir():
            if d.is_dir() and d.name not in ("objects", "context_store"):
                try:
                    shutil.rmtree(d)
                except Exception:
                    pass

    def _snapshot_and_store_asset(
        self, snapshot: Snapshot, file_id: str, role: str
    ) -> Optional[SnapshotAsset]:
        """
        Descarga un recurso desde Google Drive, lo comprime y lo almacena en el CAS local,
        creando finalmente el registro correspondiente en la base de datos.
        """
        # Obtener los metadatos necesarios (nombre de archivo, tipo mime)
        meta = self.api.get_metadata(file_id)
        if not meta:
            logger.warning(
                f"No se pudieron recuperar los metadatos para el recurso de Drive: {file_id}"
            )
            return None

        # Descargar el contenido físico del archivo
        try:
            content_bytes = self.api.get_file_content(file_id)
        except Exception as e:
            logger.error(
                f"Fallo al descargar el contenido del recurso '{meta.filename}' (ID: {file_id}): {e}"
            )
            return None

        # Almacenar el archivo de forma inmutable en el CAS local (devuelve el hash md5)
        md5 = self._store_object(content_bytes)

        # Registrar el activo en la base de datos para el usuario activo
        asset = SnapshotAsset.create(
            snapshot=snapshot,
            email=self.project_context.email,
            file_id=file_id,
            filename=meta.filename,
            mime_type=meta.mimetype,
            md5sum=md5,
            role=role,
            modified_at=datetime.now(),
        )
        return asset

    def create_named_snapshot(
        self, message: str, category: str = "user"
    ) -> Optional[str]:
        """
        Crea un punto de restauración atómico de la sesión de trabajo. Descarga el chat,
        el contexto y todos los archivos adjuntos vinculados para almacenarlos localmente.
        """
        state = self.project_context.load_state()
        if not state.chat_id or not state.file_id:
            logger.error(
                "No se puede crear un snapshot sin una sesión de chat y un archivo de contexto activos."
            )
            return None

        active_email = self.project_context.email

        try:
            # Crear el registro lógico principal (atómico)
            with db.atomic():
                snap = Snapshot.create(
                    message=message, category=category, creator_email=active_email
                )

            # Descargar y almacenar el archivo de contexto maestro en el CAS
            logger.info(
                "Respaldando archivo de contexto maestro en el almacenamiento local..."
            )
            context_asset = self._snapshot_and_store_asset(
                snap, state.file_id, role="context"
            )
            if not context_asset:
                raise ValueError(
                    "No se pudo completar el respaldo del archivo de contexto maestro."
                )

            # Descargar el Chat actual para analizar adjuntos y dependencias
            logger.info("Descargando estructura del chat activo para análisis...")
            chat_data = self.api.get_chat(state.chat_id)
            if not chat_data:
                raise ValueError("No se pudo obtener el archivo de chat remoto.")

            # Identificar y descargar recursos adicionales vinculados (Imágenes, documentos adjuntos)
            # Usamos un set para evitar descargar duplicados si un recurso aparece varias veces en el chat
            referenced_file_ids = set()
            for chunk in chat_data.chunkedPrompt.chunks:
                f_id = chunk.file_id
                # El id del contexto maestro se excluye porque se maneja por separado
                if f_id and f_id != state.file_id:
                    referenced_file_ids.add(f_id)

            for attachment_id in referenced_file_ids:
                logger.info(
                    f"Respaldando archivo adjunto detectado (ID: {attachment_id})..."
                )
                self._snapshot_and_store_asset(snap, attachment_id, role="attachment")

            # Guardar la conversación del chat en el CAS local
            # Serializamos la sesión actual para asegurar que el JSON histórico contenga todos los mensajes actuales
            chat_bytes = chat_data.model_dump_json(
                exclude_none=True, exclude_unset=True
            ).encode("utf-8")
            chat_md5 = self._store_object(chat_bytes)

            chat_meta = self.api.get_metadata(state.chat_id)
            chat_filename = (
                chat_meta.filename
                if chat_meta
                else f"{self.project_context.project_path.name}_chat.prompt"
            )
            chat_mime = (
                chat_meta.mimetype
                if chat_meta
                else "application/vnd.google-makersuite.prompt"
            )

            with db.atomic():
                SnapshotAsset.create(
                    snapshot=snap,
                    email=active_email,
                    file_id=state.chat_id,
                    filename=chat_filename,
                    mime_type=chat_mime,
                    md5sum=chat_md5,
                    role="chat",
                    modified_at=datetime.now(),
                )

            logger.info(f"Snapshot registrado de forma exitosa. ID: {snap.id}")
            return str(snap.id)

        except Exception as e:
            logger.exception(f"Fallo crítico durante la generación del snapshot: {e}")
            # Si ocurre un error, peewee revertirá de forma automática las operaciones
            # contenidas dentro de bloques 'with db.atomic()' evitando registros huérfanos.
            return None
