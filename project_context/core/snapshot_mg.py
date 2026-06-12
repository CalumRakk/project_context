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
            return None
        try:
            compressed = obj_path.read_bytes()
            return decompress_data(compressed)
        except Exception as e:
            logger.debug(
                f"[CAS Error] No se pudo leer o descomprimir el objeto {md5sum}: {e}"
            )
            return None

    def replicate_snapshot_assets(self, snapshot: Snapshot) -> dict[str, str]:
        """
        Garantiza que el usuario activo tenga todos los activos del snapshot en su Drive,
        resolviendo enlaces rotos y evitando subidas duplicadas de archivos que ya coincidan
        con el hash del CAS. Devuelve el mapa de traducción de IDs.

        Si el usuario tiene un chat o un documento de contexto activo en su estado local,
        se reutilizan esos mismos archivos sobreescribiendo su contenido para no alterar la URL activa.
        """
        active_email = self.project_context.email
        creator_assets = snapshot.get_assets_from_creator()

        if not creator_assets:
            raise ValueError("No se encontraron activos asociados a este snapshot.")

        # Cargar el estado actual para verificar si podemos reutilizar los archivos activos
        state = self.project_context.load_state()
        current_chat_id = state.chat_id
        current_file_id = state.file_id

        id_map = {}

        # Separamos los activos de tipo 'chat' de los de soporte (contexto y adjuntos)
        # para procesar primero los documentos que el chat referenciará internamente.
        non_chat_assets = [a for a in creator_assets if a.role != "chat"]
        chat_assets = [a for a in creator_assets if a.role == "chat"]

        # Sincronizar activos de soporte (Contexto, Adjuntos)
        for asset in non_chat_assets:
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
                needs_upload = True  # Forzamos la actualización para escribir el contenido del snapshot
                logger.debug(
                    f"Reutilizando el archivo de contexto activo actual para restauración: {current_file_id}"
                )
            elif existing_asset:
                # Verificamos si sigue existiendo físicamente en Drive
                meta = self.api.get_metadata(existing_asset.file_id)
                if meta:
                    target_file_id = existing_asset.file_id
                    # Comparamos hashes MD5 para evitar subidas innecesarias
                    if meta.md5sum == asset.md5sum:
                        needs_upload = False
                        logger.debug(
                            f"El activo '{asset.filename}' ya existe y está sincronizado (hashes coinciden)."
                        )
                    else:
                        logger.debug(
                            f"El activo '{asset.filename}' difiere en contenido en Drive. Se programará actualización."
                        )
                else:
                    logger.debug(
                        f"Se detectó un enlace roto en Drive para '{asset.filename}' (ID: {existing_asset.file_id}). Se re-creará."
                    )

            if needs_upload:
                content = self._retrieve_object(asset.md5sum)
                if not content:
                    raise ValueError(
                        f"No se pudo recuperar el contenido del CAS local para el MD5: {asset.md5sum}"
                    )

                if target_file_id:
                    # Actualización del archivo existente (o el activo reutilizado)
                    updated_file = self.api.update_file(
                        target_file_id, content, asset.mime_type
                    )
                    target_file_id = updated_file.id
                else:
                    # Subida de un archivo nuevo o restauración de un enlace roto
                    cloned_file = self.api.create_file(
                        folder_id=self.api.ai_studio_folder,
                        file_name=asset.filename,
                        content=content,
                        mime_type=asset.mime_type,
                    )
                    target_file_id = cloned_file.id

                # Persistencia atómica de la referencia local
                if existing_asset:
                    existing_asset.file_id = target_file_id
                    existing_asset.md5sum = asset.md5sum
                    existing_asset.modified_at = datetime.now()
                    existing_asset.save()
                else:
                    SnapshotAsset.create(
                        snapshot=snapshot,
                        file_id=target_file_id,
                        filename=asset.filename,
                        mime_type=asset.mime_type,
                        md5sum=asset.md5sum,
                        email=active_email,
                        role=asset.role,
                        modified_at=datetime.now(),
                    )

            id_map[asset.file_id] = target_file_id

        # Sincronizar el Chat traduciendo sus referencias internas
        for chat_asset in chat_assets:
            existing_chat_asset = SnapshotAsset.get_or_none(
                SnapshotAsset.snapshot == snapshot,
                SnapshotAsset.email == active_email,
                SnapshotAsset.role == "chat",
            )

            # Recuperamos el JSON histórico del chat desde el CAS local
            chat_bytes = self._retrieve_object(chat_asset.md5sum)
            if not chat_bytes:
                raise ValueError(
                    f"No se pudo recuperar el chat desde el CAS para el MD5: {chat_asset.md5sum}"
                )

            # Deserialización y re-mapeo dinámico de IDs internos
            chat_data = json.loads(chat_bytes.decode("utf-8"))
            chat_model = ChatIAStudio(**chat_data)

            for chunk in chat_model.chunkedPrompt.chunks:
                if chunk.file_id is not None:
                    translated_id = id_map.get(chunk.file_id)
                    if translated_id:
                        chunk.file_id = translated_id
                    else:
                        logger.warning(
                            f"Advertencia: No se encontró traducción en 'id_map' para el file_id: {chunk.file_id}"
                        )

            # Serializamos el modelo con los IDs actualizados para el usuario activo
            translated_content = chat_model.model_dump_json(
                exclude_none=True, exclude_unset=True
            ).encode("utf-8")

            target_chat_id = None
            needs_chat_upload = True

            # Si tenemos un chat activo en el estado, lo reutilizamos para preservar la URL
            if current_chat_id and self.api.can_access_file(current_chat_id):
                target_chat_id = current_chat_id
                logger.debug(
                    f"Reutilizando el chat activo actual para restauración: {current_chat_id}"
                )
            elif existing_chat_asset:
                meta = self.api.get_metadata(existing_chat_asset.file_id)
                if meta:
                    target_chat_id = existing_chat_asset.file_id
                else:
                    logger.debug(
                        f"Enlace roto del chat detectado para el ID: {existing_chat_asset.file_id}. Se creará uno nuevo."
                    )

            if needs_chat_upload:
                if target_chat_id:
                    self.api.update_file(
                        target_chat_id, translated_content, chat_asset.mime_type
                    )
                else:
                    cloned_chat = self.api.create_file(
                        folder_id=self.api.ai_studio_folder,
                        file_name=chat_asset.filename,
                        content=translated_content,
                        mime_type=chat_asset.mime_type,
                    )
                    target_chat_id = cloned_chat.id

                # Actualización de la referencia del chat en la base de datos
                if existing_chat_asset:
                    existing_chat_asset.file_id = target_chat_id
                    existing_chat_asset.md5sum = compute_md5(translated_content)
                    existing_chat_asset.modified_at = datetime.now()
                    existing_chat_asset.save()
                else:
                    SnapshotAsset.create(
                        snapshot=snapshot,
                        file_id=target_chat_id,
                        filename=chat_asset.filename,
                        mime_type=chat_asset.mime_type,
                        md5sum=compute_md5(translated_content),
                        email=active_email,
                        role="chat",
                        modified_at=datetime.now(),
                    )

            id_map[chat_asset.file_id] = target_chat_id

        return id_map

    def restore_snapshot(self, snapshot_id: str | int) -> bool:
        """
        Restaura el entorno de Drive y los archivos locales usando el snapshot id de forma multiusuario.
        """
        try:
            snap = cast(Snapshot, Snapshot.get_or_none(Snapshot.id == int(snapshot_id)))
            if not snap:
                logger.error(
                    f"No se encontró el snapshot con ID {snapshot_id} en la base de datos."
                )
                return False

            active_email = self.project_context.email

            # Ejecutar la sincronización idempotente de activos
            # (re-crea enlaces rotos, evita subidas redundantes de archivos inalterados)
            logger.info(
                f"Sincronizando recursos del snapshot {snapshot_id} para {active_email}..."
            )
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
                logger.error(
                    "Error crítico: No se pudieron resolver las referencias de chat o contexto para el usuario activo."
                )
                return False

            # Guardar el estado persistente en el archivo local de configuración del proyecto
            state = self.project_context.load_state()
            state.chat_id = chat_asset.file_id
            state.file_id = context_asset.file_id
            state.save()

            logger.info("Entorno restaurado exitosamente.")
            return True

        except Exception as e:
            logger.exception(
                f"Fallo inesperado durante la restauración del snapshot: {e}"
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
