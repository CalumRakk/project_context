import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from peewee import CharField, ForeignKeyField, Model, SqliteDatabase

from project_context.services.api_drive import GoogleDriveManager
from project_context.utils import compute_md5
from project_context.workspace import ProjectContext

db = SqliteDatabase(None)
logger = logging.getLogger(__name__)


class BaseModel(Model):
    class Meta:
        database = db


class Snapshot(BaseModel):
    """Representa un punto de restauración del chat y el contexto."""

    timestamp = CharField(unique=True, primary_key=True)
    human_time = CharField()
    drive_modified_time = CharField()
    message = CharField(null=True)
    chat_hash = CharField()
    context_hash = CharField()
    category = CharField(default="user")  # 'user', 'stash', 'auto'


class SnapshotAsset(BaseModel):
    """Recursos binarios vinculados a un snapshot."""

    snapshot = ForeignKeyField(Snapshot, backref="assets", on_delete="CASCADE")
    drive_file_id = CharField()
    filename = CharField()
    mime_type = CharField()
    file_hash = CharField()


def compress_data(data: bytes) -> bytes:
    import zlib

    return zlib.compress(data)


def decompress_data(data: bytes) -> bytes:
    import zlib

    return zlib.decompress(data)


class SnapshotManager:
    """
    Administrador de contexto para la gestión de snapshots.
    Sigue un modelo CAS (Content Addressable Storage) para almacenar archivos
    binarios de chat y contexto en el disco local de forma eficiente.
    """

    def __init__(self, api: GoogleDriveManager, project_context: ProjectContext):
        self.api = api
        self.project_context = project_context

    def __enter__(self):
        """Inicializa la conexión de la base de datos al ingresar al contexto."""
        db_path = self.project_context.local_dir / "snapshots.db"
        db.init(
            str(db_path),
            pragmas={
                "journal_mode": "wal",
                "cache_size": -1024 * 64,
                "foreign_keys": 1,
                "ignore_check_constraints": 0,
                "synchronous": 1,
            },
        )
        if db.is_closed():
            db.connect(reuse_if_open=True)

        db.create_tables([Snapshot, SnapshotAsset], safe=True)
        self._migrate_schema()
        self._migrate_legacy_snapshots()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Cierra de forma segura la conexión de la base de datos al salir."""
        if not db.is_closed():
            db.close()

    def _migrate_schema(self):
        """Aplica migraciones ligeras sobre el esquema de la base de datos."""
        with db.connection_context():
            try:
                db.execute_sql("SELECT category FROM snapshot LIMIT 1")
            except Exception:
                try:
                    db.execute_sql(
                        "ALTER TABLE snapshot ADD COLUMN category VARCHAR(255) DEFAULT 'user'"
                    )
                    logger.debug(
                        "[Schema Migration] Columna 'category' añadida con éxito."
                    )
                except Exception as e:
                    logger.debug(
                        f"[Schema Migration Error] No se pudo añadir la columna: {e}"
                    )

    def _get_object_path(self, file_hash: str) -> Path:
        prefix = file_hash[:2]
        suffix = file_hash[2:]
        return self.project_context.objects_dir / prefix / f"{suffix}.z"

    def _store_object(self, data: bytes) -> str:
        file_hash = compute_md5(data)
        obj_path = self._get_object_path(file_hash)
        if not obj_path.exists():
            obj_path.parent.mkdir(parents=True, exist_ok=True)
            compressed = compress_data(data)
            obj_path.write_bytes(compressed)
        return file_hash

    def _retrieve_object(self, file_hash: str) -> Optional[bytes]:
        obj_path = self._get_object_path(file_hash)
        if not obj_path.exists():
            return None
        try:
            compressed = obj_path.read_bytes()
            return decompress_data(compressed)
        except Exception as e:
            logger.debug(
                f"[CAS Error] No se pudo leer o decompress el objeto {file_hash}: {e}"
            )
            return None

    def create_snapshot(
        self,
        drive_modified_time: str,
        message: Optional[str] = None,
        category: str = "auto",
    ) -> Optional[str]:
        """Crea un snapshot atómico y retorna su timestamp asignado."""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            current_md5 = self.project_context.md5
            if not current_md5:
                return None

            current_context_path = self.project_context.local_dir / "last_context.txt"
            if not current_context_path.exists():
                return None

            context_content = current_context_path.read_bytes()
            chat_id = self.project_context.chat_id
            chat_content = self.api.get_file_content(chat_id)

            if chat_content:
                chat_hash = self._store_object(chat_content)
                context_hash = self._store_object(context_content)

                Snapshot.create(
                    timestamp=timestamp,
                    human_time=datetime.now().strftime("%H:%M:%S - %d/%m/%Y"),
                    drive_modified_time=drive_modified_time,
                    message=message,
                    chat_hash=chat_hash,
                    context_hash=context_hash,
                    category=category,
                )
                return timestamp
        except Exception as e:
            logger.debug(f"[Error Snapshot]: {e}")
        return None

    def create_named_snapshot(
        self, message: str, category: str = "user"
    ) -> Optional[str]:
        """Genera un snapshot etiquetado resolviendo metadatos remotos."""
        chat_id = self.project_context.chat_id
        if not chat_id:
            return None

        metadata = self.api.get_file_metadata(chat_id)
        mod_time = (
            metadata.get("modifiedTime", "Manual Save") if metadata else "Unknown"
        )
        return self.create_snapshot(
            drive_modified_time=mod_time, message=message, category=category
        )

    def get_latest_snapshot_by_category(self, category: str) -> Optional[dict]:
        """Recupera el último snapshot correspondiente a una categoría."""
        try:
            snap = (
                Snapshot.select()
                .where(Snapshot.category == category)
                .order_by(Snapshot.timestamp.desc())
                .first()
            )
            if snap:
                return {
                    "timestamp": snap.timestamp,
                    "human_time": snap.human_time,
                    "drive_modified_time": snap.drive_modified_time,
                    "context_md5": snap.context_hash,
                    "message": snap.message,
                    "category": getattr(snap, "category", "user"),
                }
        except Exception as e:
            logger.debug(f"[Error] Fallo al buscar snapshot por categoría: {e}")
        return None

    def restore_snapshot(self, timestamp: str) -> bool:
        """Restaura la información del snapshot en el entorno de Drive y disco local."""
        try:
            snap = Snapshot.get_or_none(Snapshot.timestamp == timestamp)
            if not snap:
                logger.debug("Snapshot no encontrado en la base de datos.")
                return False

            logger.debug(f"Restaurando snapshot {timestamp}...")

            chat_bytes = self._retrieve_object(snap.chat_hash)
            if not chat_bytes:
                logger.debug(f"Error: Chat {snap.chat_hash} no disponible localmente.")
                return False

            try:
                chat_json = json.loads(chat_bytes.decode("utf-8"))
            except Exception as e:
                logger.debug(f"Error al decodificar chat JSON: {e}")
                return False

            assets_to_repair = list(
                SnapshotAsset.select().where(SnapshotAsset.snapshot == snap)
            )
            id_map = {}

            for asset in assets_to_repair:
                logger.debug(f"Verificando recurso en la nube: {asset.filename}...")
                metadata = self.api.get_file_metadata(asset.drive_file_id)
                if metadata:
                    continue

                logger.debug(
                    f"  Recurso no encontrado. Buscando por hash (MD5: {asset.file_hash})..."
                )
                files = self.api.find_files_by_query(
                    f"md5Checksum = '{asset.file_hash}' and trashed = false",
                    fields="files(id, name, mimeType)",
                )

                if files:
                    repaired_id = files[0]["id"]
                    logger.debug(
                        f"  ¡Recurso recuperado de Drive! Vinculando ID: {repaired_id}"
                    )
                    id_map[asset.drive_file_id] = repaired_id
                    asset.drive_file_id = repaired_id
                    asset.save()
                else:
                    logger.debug(
                        "  Recurso no encontrado en Drive. Recuperando de objects/..."
                    )
                    asset_bytes = self._retrieve_object(asset.file_hash)
                    if not asset_bytes:
                        logger.debug(
                            f"  [Error] No hay respaldo físico para {asset.filename}."
                        )
                        continue

                    logger.debug("  Subiendo recurso restaurado a Drive...")
                    try:
                        new_file = self.api.upload_binary_to_drive(
                            folder_id=self.api.ai_studio_folder,
                            file_name=asset.filename,
                            content=asset_bytes,
                            mime_type=asset.mime_type,
                        )
                        if new_file and "id" in new_file:
                            repaired_id = new_file["id"]
                            logger.debug(f"  Recurso restaurado con ID: {repaired_id}")
                            id_map[asset.drive_file_id] = repaired_id
                            asset.drive_file_id = repaired_id
                            asset.save()
                    except Exception as e:
                        logger.debug(f"  [Error] No se pudo restaurar el archivo: {e}")

            if id_map:
                logger.debug(
                    "Aplicando mapeo de identificadores reparados en el chat..."
                )
                chunks = chat_json.get("chunkedPrompt", {}).get("chunks", [])
                for chunk in chunks:
                    if (
                        "driveDocument" in chunk
                        and chunk["driveDocument"].get("id") in id_map
                    ):
                        chunk["driveDocument"]["id"] = id_map[
                            chunk["driveDocument"]["id"]
                        ]
                    if (
                        "driveImage" in chunk
                        and chunk["driveImage"].get("id") in id_map
                    ):
                        chunk["driveImage"]["id"] = id_map[chunk["driveImage"]["id"]]

            repaired_chat_content = json.dumps(chat_json, ensure_ascii=False)

            context_bytes = self._retrieve_object(snap.context_hash)
            if context_bytes is None:
                logger.debug(
                    f"Error: Contexto {snap.context_hash} no disponible localmente."
                )
                return False

            context_content = context_bytes.decode("utf-8")

            file_id = self.project_context.file_id
            chat_id = self.project_context.chat_id

            if not file_id or not chat_id:
                logger.debug(
                    "Error: No hay identificadores de chat en la sesión actual."
                )
                return False

            meta_ctx = self.api.get_file_metadata(file_id)
            if not meta_ctx:
                logger.debug(
                    "  [Auto-reparación] Recreando archivo de contexto maestro en Drive..."
                )
                project_path = self.project_context.local_dir
                filename = Path(project_path).name + "_context.txt"
                new_ctx_file = self.api.create_file_from_memory(
                    folder_id=self.api.ai_studio_folder,
                    file_name=filename,
                    content=context_content,
                    mime_type="text/plain",
                )
                if new_ctx_file and "id" in new_ctx_file:
                    file_id = new_ctx_file["id"]
                    self.project_context.file_id = file_id
                else:
                    logger.debug(
                        "  Error crítico: No se pudo recrear el archivo de contexto."
                    )
                    return False
            else:
                self.api.update_file_from_memory(file_id, context_content, "text/plain")

            meta_chat = self.api.get_file_metadata(chat_id)
            if not meta_chat:
                logger.debug(
                    "  [Auto-reparación] Recreando archivo de chat en Drive..."
                )
                from project_context.schema import ChatIAStudio

                chat_data = ChatIAStudio(**chat_json)
                project_path = self.project_context.local_dir
                chat_filename = Path(project_path).name + "_chat.prompt"
                new_chat_id = self.api.create_chat_file(
                    folder_id=file_id, file_name=chat_filename, chat_data=chat_data
                )
                if new_chat_id:
                    chat_id = new_chat_id
                    self.project_context.chat_id = chat_id
                else:
                    logger.debug("  Error crítico: No se pudo recrear el chat.")
                    return False
            else:
                self.api.update_file_from_memory(
                    chat_id, repaired_chat_content, self.api.MIME_PROMPT
                )

            current_local_context = (
                self.project_context.local_dir / "project_context.txt"
            )
            last_context = self.project_context.local_dir / "last_context.txt"
            last_context.write_text(context_content, encoding="utf-8")
            shutil.copy2(last_context, current_local_context)

            self.project_context.md5 = snap.context_hash
            logger.debug("Restauración completada con éxito.")
            return True

        except Exception as e:
            logger.debug(f"[Error] No se pudo restaurar el snapshot: {e}")
            return False

    def get_all_snapshot_ids(self) -> List[str]:
        """Obtiene solo los timestamps ordenados descendentemente."""
        try:
            query = Snapshot.select(Snapshot.timestamp).order_by(
                Snapshot.timestamp.desc()
            )
            return [snap.timestamp for snap in query]
        except Exception as e:
            logger.debug(f"[Error] Fallo al consultar los timestamps: {e}")
            return []

    def get_snapshot_info(self, timestamp: str) -> Optional[dict]:
        """Carga el registro de un snapshot específico."""
        try:
            snap = Snapshot.get_or_none(Snapshot.timestamp == timestamp)
            if snap:
                return {
                    "timestamp": snap.timestamp,
                    "human_time": snap.human_time,
                    "drive_modified_time": snap.drive_modified_time,
                    "context_md5": snap.context_hash,
                    "message": snap.message,
                    "category": getattr(snap, "category", "user"),
                }
        except Exception as e:
            logger.debug(f"[Error] Fallo al consultar el snapshot: {e}")
        return None

    def list_snapshots(self) -> List[dict]:
        """Devuelve una lista de todos los snapshots registrados."""
        try:
            query = Snapshot.select().order_by(Snapshot.timestamp.desc())
            return [
                {
                    "timestamp": snap.timestamp,
                    "human_time": snap.human_time,
                    "drive_modified_time": snap.drive_modified_time,
                    "context_md5": snap.context_hash,
                    "message": snap.message,
                    "category": getattr(snap, "category", "user"),
                }
                for snap in query
            ]
        except Exception as e:
            logger.debug(f"[Error] Fallo al listar historial: {e}")
            return []

    def delete_snapshot(self, timestamp: str) -> bool:
        """Elimina un snapshot y realiza limpieza en cascada de binarios sin referencias."""
        try:
            snap = Snapshot.get_or_none(Snapshot.timestamp == timestamp)
            if snap:
                snap.delete_instance(recursive=True)
                self.prune_objects()
                return True
        except Exception as e:
            logger.debug(f"[Error] No se pudo eliminar el snapshot: {e}")
        return False

    def rename_snapshot(self, timestamp: str, new_message: str) -> bool:
        """Modifica la descripción de un snapshot existente."""
        try:
            q = Snapshot.update({Snapshot.message: new_message}).where(
                Snapshot.timestamp == timestamp
            )
            q.execute()
            return True
        except Exception as e:
            logger.debug(f"[Error] No se pudo renombrar el snapshot: {e}")
        return False

    def prune_objects(self) -> int:
        """Elimina físicamente del disco los archivos .z en CAS que no tengan referencias en base de datos."""
        referenced_hashes = set()
        try:
            for snap in Snapshot.select(Snapshot.chat_hash, Snapshot.context_hash):
                referenced_hashes.add(snap.chat_hash)
                referenced_hashes.add(snap.context_hash)
            for asset in SnapshotAsset.select(SnapshotAsset.file_hash):
                referenced_hashes.add(asset.file_hash)
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
        """Migra de forma transparente los snapshots antiguos."""
        if not self.project_context.snapshots_dir.exists():
            return

        legacy_folders = []
        for d in self.project_context.snapshots_dir.iterdir():
            if (
                d.is_dir()
                and d.name not in ("objects", "context_store")
                and (d / "info.json").exists()
            ):
                legacy_folders.append(d)

        if not legacy_folders:
            return

        logger.debug(
            f"\n[Migration] Se detectaron {len(legacy_folders)} snapshots antiguos. Migrando..."
        )

        for folder in legacy_folders:
            try:
                info_path = folder / "info.json"
                chat_path = folder / "chat.prompt"

                info = json.loads(info_path.read_text(encoding="utf-8"))
                chat_bytes = chat_path.read_bytes()

                timestamp = info.get("timestamp")
                human_time = info.get("human_time")
                drive_modified_time = info.get("drive_modified_time", "Unknown")
                message = info.get("message")
                context_md5 = info.get("context_md5")

                chat_hash = self._store_object(chat_bytes)

                context_bytes = b""
                stored_context = (
                    self.project_context.context_store_dir / f"{context_md5}.txt"
                )
                if stored_context.exists():
                    context_bytes = stored_context.read_bytes()
                else:
                    current_ctx_file = (
                        self.project_context.local_dir / "last_context.txt"
                    )
                    if current_ctx_file.exists():
                        context_bytes = current_ctx_file.read_bytes()

                context_hash = self._store_object(context_bytes)

                snap_record, created = Snapshot.get_or_create(
                    timestamp=timestamp,
                    defaults={
                        "human_time": human_time,
                        "drive_modified_time": drive_modified_time,
                        "message": message,
                        "chat_hash": chat_hash,
                        "context_hash": context_hash,
                    },
                )

                try:
                    chat_json = json.loads(chat_bytes.decode("utf-8"))
                    chunks = chat_json.get("chunkedPrompt", {}).get("chunks", [])
                    for chunk in chunks:
                        file_id = None
                        mtype = "application/octet-stream"
                        fname = "unnamed"
                        if "driveDocument" in chunk:
                            file_id = chunk["driveDocument"].get("id")
                            fname = "context_document.txt"
                        elif "driveImage" in chunk:
                            file_id = chunk["driveImage"].get("id")
                            mtype = "image/jpeg"
                            fname = f"image_{file_id}.jpg"

                        if file_id:
                            try:
                                asset_bytes = self.api.get_file_content(file_id)
                                if asset_bytes:
                                    asset_hash = self._store_object(asset_bytes)
                                    SnapshotAsset.get_or_create(
                                        snapshot=snap_record,
                                        drive_file_id=file_id,
                                        defaults={
                                            "filename": fname,
                                            "mime_type": mtype,
                                            "file_hash": asset_hash,
                                        },
                                    )
                            except Exception:
                                pass
                except Exception:
                    pass

                shutil.rmtree(folder)

            except Exception as e:
                logger.debug(
                    f"[Migration Warning] No se pudo migrar la carpeta legacy {folder.name}: {e}"
                )

        if self.project_context.context_store_dir.exists():
            try:
                shutil.rmtree(self.project_context.context_store_dir)
            except Exception:
                pass

        logger.debug("[Migration] Proceso de migración finalizado.")
