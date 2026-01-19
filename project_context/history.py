import json
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from project_context.api_drive import AIStudioDriveManager
from project_context.utils import generate_unique_id, profile_manager


class SnapshotManager:
    def __init__(self, api: AIStudioDriveManager, project_path: Path, state: Dict):
        self.api = api
        self.project_path = project_path
        self.state = state
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.interval = 10

        self.inode = generate_unique_id(project_path)

        self.base_dir = profile_manager.get_working_dir() / self.inode

        self.snapshots_dir = self.base_dir / "snapshots"
        self.context_store_dir = self.base_dir / "context_store"

        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self.context_store_dir.mkdir(parents=True, exist_ok=True)

        self.last_known_chat_mod_time = None

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
            if self.thread:
                self.thread.join(timeout=1.0)
            print("\n[Auto-Snapshot] Detenido.")
        except Exception as e:
            print(f"\n[Error Auto-Snapshot]: {e}")

    def _loop(self):
        while self.running:
            try:
                self._check_and_snapshot()
            except Exception as e:
                print(f"\n[Error Auto-Snapshot]: {e}")

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
            self.create_snapshot(remote_mod_time)

        self.last_known_chat_mod_time = remote_mod_time

    def _ensure_context_stored(self, context_md5: str) -> bool:
        store_path = self.context_store_dir / f"{context_md5}.txt"
        if store_path.exists():
            return True

        current_context_path = self.base_dir / "project_context.txt"
        if current_context_path.exists():
            shutil.copy2(current_context_path, store_path)
            return True
        return False

    def create_snapshot(self, mod_time_str: str, message: Optional[str] = None):
        """
        Crea el snapshot físico. Acepta un mensaje opcional.
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        snap_folder = self.snapshots_dir / timestamp
        snap_folder.mkdir(parents=True, exist_ok=True)

        current_md5 = self.state.get("md5")
        if not current_md5:
            return

        if not self._ensure_context_stored(current_md5):
            print("\n[Auto-Snapshot] Error: Falta contexto fuente.")
            return

        chat_id = self.state.get("chat_id", "")
        chat_content = self.api.gdm.get_file_content(chat_id)

        if chat_content:
            (snap_folder / "chat.prompt").write_bytes(chat_content)

            info = {
                "timestamp": timestamp,
                "human_time": datetime.now().strftime("%H:%M:%S - %d/%m/%Y"),
                "drive_modified_time": mod_time_str,
                "context_md5": current_md5,
                "message": message,
            }
            (snap_folder / "info.json").write_text(json.dumps(info, indent=2))

            if message:
                print(f" Snapshot manual '{message}' creado exitosamente.")
            else:
                print(".", end="", flush=True)

    def create_named_snapshot(self, message: str):
        """
        Fuerza la creación de un snapshot con un nombre/mensaje,
        obteniendo la fecha de modificación actual de Drive.
        """
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
        snap_folder = self.snapshots_dir / timestamp
        if not snap_folder.exists():
            print("Snapshot no encontrado.")
            return False

        print(f"Restaurando snapshot {timestamp}...")
        try:
            info = json.loads((snap_folder / "info.json").read_text())
            context_md5 = info.get("context_md5")
        except Exception:
            print("Error leyendo metadata del snapshot.")
            return False

        stored_context_path = self.context_store_dir / f"{context_md5}.txt"
        if not stored_context_path.exists():
            print(f"ERROR: Contexto {context_md5} no encontrado en almacén.")
            return False

        chat_file = snap_folder / "chat.prompt"
        if not chat_file.exists():
            print("Chat no encontrado en snapshot.")
            return False

        chat_content = chat_file.read_text(encoding="utf-8")
        context_content = stored_context_path.read_text(encoding="utf-8")

        print("Restaurando archivos en Drive...")
        self.api.gdm.update_file_from_memory(
            self.state["file_id"], context_content, "text/plain"
        )
        self.api.gdm.update_file_from_memory(
            self.state["chat_id"],
            chat_content,
            "application/vnd.google-makersuite.prompt",
        )

        current_local_context = self.base_dir / "project_context.txt"
        shutil.copy2(stored_context_path, current_local_context)
        self.state["md5"] = context_md5
        print("Restauración completada.")
        return True

    def get_all_snapshot_ids(self) -> List[str]:
        """Obtiene solo los timestamps (nombres de carpeta) ordenados del más reciente al más antiguo."""
        if not self.snapshots_dir.exists():
            return []

        # Las carpetas tienen formato YYYYMMDD_HHMMSS
        folders = [
            d.name
            for d in self.snapshots_dir.iterdir()
            if d.is_dir() and (d / "info.json").exists()
        ]
        return sorted(folders, reverse=True)

    def get_snapshot_info(self, timestamp: str) -> Optional[dict]:
        """Carga el JSON de un snapshot específico."""
        info_path = self.snapshots_dir / timestamp / "info.json"
        if not info_path.exists():
            return None
        try:
            return json.loads(info_path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def list_snapshots(self) -> List[dict]:
        """Devuelve una lista de diccionarios con la información de todos los snapshots."""
        ids = self.get_all_snapshot_ids()
        snaps = []
        for tid in ids:
            info = self.get_snapshot_info(tid)
            if info:
                snaps.append(info)
        return snaps
