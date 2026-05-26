import hashlib
import json
import shutil
import threading
import time
import zlib
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from peewee import CharField, ForeignKeyField, Model, SqliteDatabase

from project_context.api_drive import AIStudioDriveManager

db = SqliteDatabase(None)


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


class SnapshotAsset(BaseModel):
    """
    Recursos binarios (como imágenes o documentos de contexto) vinculados a un snapshot.
    Permite rastrear su estado en la nube y localmente de forma independiente.
    """

    snapshot = ForeignKeyField(Snapshot, backref="assets", on_delete="CASCADE")
    drive_file_id = CharField()
    filename = CharField()
    mime_type = CharField()
    file_hash = CharField()


def compress_data(data: bytes) -> bytes:
    return zlib.compress(data)


def decompress_data(data: bytes) -> bytes:
    return zlib.decompress(data)


def calculate_md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


class SnapshotManager:
    def __init__(self, api: AIStudioDriveManager, project_path: Path, state: Dict):
        self.api = api
        self.project_path = project_path
        self.state = state
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.interval = 10

        self.base_dir = project_path / ".project_context"
        self.snapshots_dir = self.base_dir / "snapshots"
        self.objects_dir = self.snapshots_dir / "objects"
        self.context_store_dir = self.snapshots_dir / "context_store"

        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self.objects_dir.mkdir(parents=True, exist_ok=True)

        self.last_known_chat_mod_time = None

        db_path = self.base_dir / "snapshots.db"
        if not db.is_closed():
            db.close()

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
        db.connect(reuse_if_open=True)
        db.create_tables([Snapshot, SnapshotAsset], safe=True)

        self._migrate_legacy_snapshots()

    def _get_object_path(self, file_hash: str) -> Path:
        prefix = file_hash[:2]
        suffix = file_hash[2:]
        return self.objects_dir / prefix / f"{suffix}.z"

    def _store_object(self, data: bytes) -> str:
        file_hash = calculate_md5(data)
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
            print(
                f"[CAS Error] No se pudo leer o descomprimir el objeto {file_hash}: {e}"
            )
            return None

    def start_monitoring(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        print(f"\n[Auto-Snapshot] Activado. Verificando cambios cada {self.interval}s.")

    def stop_monitoring(self):
        try:
            self.running = False
            if self.thread and self.thread.is_alive():
                self.thread.join(timeout=1.0)
            if not db.is_closed():
                db.close()
            print("\n[Auto-Snapshot] Detenido.")
        except Exception as e:
            print(f"\n[Error Auto-Snapshot]: {e}")

    def _loop(self):
        while self.running:
            try:
                self._check_and_snapshot()
            except Exception as e:
                print(f"[Error Auto-Snapshot]: {e}")

            for _ in range(self.interval):
                if not self.running:
                    break
                time.sleep(1)

    def _check_and_snapshot(self):
        chat_id = self.state.get("chat_id")
        if not chat_id:
            return

        metadata = self.api.gdm.get_file_metadata(chat_id)
        if not metadata:
            return

        remote_mod_time = metadata.get("modifiedTime", "")

        if (
            self.last_known_chat_mod_time
            and self.last_known_chat_mod_time != remote_mod_time
        ):
            with db.connection_context():
                self.create_snapshot(remote_mod_time)

        self.last_known_chat_mod_time = remote_mod_time

    def create_snapshot(self, mod_time_str: str, message: Optional[str] = None):
        """Crea un snapshot atómico en la base de datos SQLite y almacena los datos en CAS."""
        with db.connection_context():
            try:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

                current_md5 = self.state.get("md5")
                if not current_md5:
                    return

                current_context_path = self.base_dir / "last_context.txt"
                if not current_context_path.exists():
                    print("\n[Auto-Snapshot] Error: Falta contexto fuente.")
                    return

                context_content = current_context_path.read_bytes()
                chat_id = self.state.get("chat_id", "")
                chat_content = self.api.gdm.get_file_content(chat_id)

                if chat_content:
                    chat_hash = self._store_object(chat_content)
                    context_hash = self._store_object(context_content)

                    snapshot, created = Snapshot.get_or_create(
                        timestamp=timestamp,
                        defaults={
                            "human_time": datetime.now().strftime(
                                "%H:%M:%S - %d/%m/%Y"
                            ),
                            "drive_modified_time": mod_time_str,
                            "message": message,
                            "chat_hash": chat_hash,
                            "context_hash": context_hash,
                        },
                    )

                    try:
                        chat_json = json.loads(chat_content.decode("utf-8"))
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
                                    raw_meta = (
                                        self.api.gdm.service.files()
                                        .get(
                                            fileId=file_id, fields="id, name, mimeType"
                                        )
                                        .execute()
                                    )
                                    fname = raw_meta.get("name", fname)
                                    mtype = raw_meta.get("mimeType", mtype)
                                except Exception:
                                    pass

                                asset_bytes = self.api.gdm.get_file_content(file_id)
                                if asset_bytes:
                                    asset_hash = self._store_object(asset_bytes)
                                    SnapshotAsset.get_or_create(
                                        snapshot=snapshot,
                                        drive_file_id=file_id,
                                        defaults={
                                            "filename": fname,
                                            "mime_type": mtype,
                                            "file_hash": asset_hash,
                                        },
                                    )
                    except Exception as e:
                        print(
                            f"\n[Auto-Snapshot Info] Omitiendo procesamiento detallado de assets: {e}"
                        )

                    if message:
                        print(f" Snapshot manual '{message}' creado exitosamente.")
                    else:
                        print(".", end="", flush=True)

            except Exception as e:
                print(f"\n[Error Auto-Snapshot]: {e}")

    def create_named_snapshot(self, message: str):
        """Fuerza la creación de un snapshot manual con un comentario."""
        chat_id = self.state.get("chat_id")
        if not chat_id:
            print("Error: No hay chat ID activo.")
            return

        metadata = self.api.gdm.get_file_metadata(chat_id)
        mod_time = (
            metadata.get("modifiedTime", "Manual Save") if metadata else "Unknown"
        )
        self.create_snapshot(mod_time, message=message)

    def restore_snapshot(self, timestamp: str) -> bool:
        """Restaura de forma segura un snapshot, verificando y auto-reparando recursos."""
        with db.connection_context():
            try:
                snap = Snapshot.get_or_none(Snapshot.timestamp == timestamp)
                if not snap:
                    print("Snapshot no encontrado en la base de datos.")
                    return False

                print(f"Restaurando snapshot {timestamp}...")

                chat_bytes = self._retrieve_object(snap.chat_hash)
                if not chat_bytes:
                    print(f"Error: Chat {snap.chat_hash} no disponible localmente.")
                    return False

                try:
                    chat_json = json.loads(chat_bytes.decode("utf-8"))
                except Exception as e:
                    print(f"Error al decodificar chat JSON: {e}")
                    return False

                assets_to_repair = list(
                    SnapshotAsset.select().where(SnapshotAsset.snapshot == snap)
                )
                id_map = {}

                for asset in assets_to_repair:
                    print(f"Verificando recurso en la nube: {asset.filename}...")
                    metadata = self.api.gdm.get_file_metadata(asset.drive_file_id)
                    if metadata:
                        continue

                    print(
                        f"  Recurso no encontrado. Buscando por hash (MD5: {asset.file_hash})..."
                    )
                    try:
                        response = (
                            self.api.gdm.service.files()
                            .list(
                                q=f"md5Checksum = '{asset.file_hash}' and trashed = false",
                                spaces="drive",
                                fields="files(id, name, mimeType)",
                            )
                            .execute()
                        )
                        files = response.get("files", [])
                    except Exception as e:
                        print(f"  Error en búsqueda de Drive: {e}")
                        files = []

                    if files:
                        repaired_id = files[0]["id"]
                        print(
                            f"  ¡Recurso recuperado de Drive! Vinculando ID: {repaired_id}"
                        )
                        id_map[asset.drive_file_id] = repaired_id
                        asset.drive_file_id = repaired_id
                        asset.save()
                    else:
                        print(
                            "  Recurso no encontrado en Drive. Recuperando de objects/..."
                        )
                        asset_bytes = self._retrieve_object(asset.file_hash)
                        if not asset_bytes:
                            print(
                                f"  [Error] No hay respaldo físico para {asset.filename}."
                            )
                            continue

                        print("  Subiendo recurso restaurado a Drive...")
                        try:
                            new_file = self.api.gdm.upload_binary_to_drive(
                                folder_id=self.api.ai_studio_folder,
                                file_name=asset.filename,
                                content=asset_bytes,
                                mime_type=asset.mime_type,
                            )
                            if new_file and "id" in new_file:
                                repaired_id = new_file["id"]
                                print(f"  Recurso restaurado con ID: {repaired_id}")
                                id_map[asset.drive_file_id] = repaired_id
                                asset.drive_file_id = repaired_id
                                asset.save()
                        except Exception as e:
                            print(f"  [Error] No se pudo restaurar el archivo: {e}")

                if id_map:
                    print("Aplicando mapeo de identificadores reparados en el chat...")
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
                            chunk["driveImage"]["id"] = id_map[
                                chunk["driveImage"]["id"]
                            ]

                repaired_chat_content = json.dumps(chat_json, ensure_ascii=False)

                context_bytes = self._retrieve_object(snap.context_hash)
                if context_bytes is None:
                    print(
                        f"Error: Contexto {snap.context_hash} no disponible localmente."
                    )
                    return False

                context_content = context_bytes.decode("utf-8")

                file_id = self.state.get("file_id")
                chat_id = self.state.get("chat_id")

                if not file_id or not chat_id:
                    print("Error: No hay identificadores de chat en la sesión actual.")
                    return False

                meta_ctx = self.api.gdm.get_file_metadata(file_id)
                if not meta_ctx:
                    print(
                        "  [Auto-reparación] Recreando archivo de contexto maestro en Drive..."
                    )
                    filename = Path(self.project_path).name + "_context.txt"
                    new_ctx_file = self.api.gdm.create_file_from_memory(
                        folder_id=self.api.ai_studio_folder,
                        file_name=filename,
                        content=context_content,
                        mime_type="text/plain",
                    )
                    if new_ctx_file and "id" in new_ctx_file:
                        file_id = new_ctx_file["id"]
                        self.state["file_id"] = file_id
                    else:
                        print(
                            "  Error crítico: No se pudo recrear el archivo de contexto."
                        )
                        return False
                else:
                    self.api.gdm.update_file_from_memory(
                        file_id, context_content, "text/plain"
                    )

                meta_chat = self.api.gdm.get_file_metadata(chat_id)
                if not meta_chat:
                    print("  [Auto-reparación] Recreando archivo de chat en Drive...")
                    from project_context.schema import ChatIAStudio

                    chat_data = ChatIAStudio(**chat_json)
                    chat_filename = Path(self.project_path).name + "_chat.prompt"
                    new_chat_id = self.api.create_chat_file(
                        file_name=chat_filename, chat_data=chat_data
                    )
                    if new_chat_id:
                        chat_id = new_chat_id
                        self.state["chat_id"] = chat_id
                    else:
                        print("  Error crítico: No se pudo recrear el chat.")
                        return False
                else:
                    self.api.gdm.update_file_from_memory(
                        chat_id, repaired_chat_content, self.api.MIME_PROMPT
                    )

                current_local_context = self.base_dir / "project_context.txt"
                last_context = self.base_dir / "last_context.txt"
                last_context.write_text(context_content, encoding="utf-8")
                shutil.copy2(last_context, current_local_context)

                self.state["md5"] = snap.context_hash
                print("Restauración completada con éxito.")
                return True

            except Exception as e:
                print(f"[Error] No se pudo restaurar el snapshot: {e}")
                return False

    def get_all_snapshot_ids(self) -> List[str]:
        """Obtiene solo los timestamps ordenados del más reciente al más antiguo."""
        with db.connection_context():
            try:
                query = Snapshot.select(Snapshot.timestamp).order_by(
                    Snapshot.timestamp.desc()
                )
                return [snap.timestamp for snap in query]
            except Exception as e:
                print(f"[Error] Fallo al consultar los timestamps: {e}")
                return []

    def get_snapshot_info(self, timestamp: str) -> Optional[dict]:
        """Carga el registro de un snapshot específico."""
        with db.connection_context():
            try:
                snap = Snapshot.get_or_none(Snapshot.timestamp == timestamp)
                if snap:
                    return {
                        "timestamp": snap.timestamp,
                        "human_time": snap.human_time,
                        "drive_modified_time": snap.drive_modified_time,
                        "context_md5": snap.context_hash,
                        "message": snap.message,
                    }
            except Exception as e:
                print(f"[Error] Fallo al consultar el snapshot: {e}")
            return None

    def list_snapshots(self) -> List[dict]:
        """Devuelve una lista de todos los snapshots."""
        with db.connection_context():
            try:
                query = Snapshot.select().order_by(Snapshot.timestamp.desc())
                return [
                    {
                        "timestamp": snap.timestamp,
                        "human_time": snap.human_time,
                        "drive_modified_time": snap.drive_modified_time,
                        "context_md5": snap.context_hash,
                        "message": snap.message,
                    }
                    for snap in query
                ]
            except Exception as e:
                print(f"[Error] Fallo al listar historial: {e}")
                return []

    def delete_snapshot(self, timestamp: str) -> bool:
        """Elimina un snapshot de la base de datos (con cascada de assets) y limpia archivos huérfanos."""
        with db.connection_context():
            try:
                snap = Snapshot.get_or_none(Snapshot.timestamp == timestamp)
                if snap:
                    snap.delete_instance(recursive=True)
                    self.prune_objects()
                    return True
            except Exception as e:
                print(f"[Error] No se pudo eliminar el snapshot: {e}")
            return False

    def rename_snapshot(self, timestamp: str, new_message: str) -> bool:
        """Modifica la descripción del snapshot en SQLite."""
        with db.connection_context():
            try:
                q = Snapshot.update({Snapshot.message: new_message}).where(
                    Snapshot.timestamp == timestamp
                )
                q.execute()
                return True
            except Exception as e:
                print(f"[Error] No se pudo renombrar el snapshot: {e}")
                return False

    def prune_objects(self) -> int:
        """Elimina físicamente del disco los archivos .z en CAS que no tengan referencias en SQLite."""
        with db.connection_context():
            referenced_hashes = set()
            try:
                for snap in Snapshot.select(Snapshot.chat_hash, Snapshot.context_hash):
                    referenced_hashes.add(snap.chat_hash)
                    referenced_hashes.add(snap.context_hash)
                for asset in SnapshotAsset.select(SnapshotAsset.file_hash):
                    referenced_hashes.add(asset.file_hash)
            except Exception as e:
                print(f"[Error] No se pudieron leer las referencias activas: {e}")
                return 0

            deleted_count = 0
            if not self.objects_dir.exists():
                return 0

            for path in self.objects_dir.glob("**/*.z"):
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
                            print(f"[Error] No se pudo eliminar {path.name}: {e}")
            return deleted_count

    def _migrate_legacy_snapshots(self):
        """Migra de forma transparente los snapshots antiguos (basados en carpetas) al nuevo esquema DB/CAS."""
        if not self.snapshots_dir.exists():
            return

        legacy_folders = []
        for d in self.snapshots_dir.iterdir():
            if (
                d.is_dir()
                and d.name not in ("objects", "context_store")
                and (d / "info.json").exists()
            ):
                legacy_folders.append(d)

        if not legacy_folders:
            return

        print(
            f"\n[Migration] Se detectaron {len(legacy_folders)} snapshots del formato anterior. Migrando..."
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
                stored_context = self.context_store_dir / f"{context_md5}.txt"
                if stored_context.exists():
                    context_bytes = stored_context.read_bytes()
                else:
                    current_ctx_file = self.base_dir / "last_context.txt"
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
                                asset_bytes = self.api.gdm.get_file_content(file_id)
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
                print(
                    f"[Migration Warning] No se pudo migrar la carpeta legacy {folder.name}: {e}"
                )

        if self.context_store_dir.exists():
            try:
                shutil.rmtree(self.context_store_dir)
            except Exception:
                pass

        print("[Migration] Proceso de migración finalizado.")
