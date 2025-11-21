import json
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from project_context.api_drive import AIStudioDriveManager
from project_context.utils import APP_FOLDER, generate_unique_id


class SnapshotManager:
    def __init__(self, api: AIStudioDriveManager, project_path: Path, state: Dict):
        self.api = api
        self.project_path = project_path
        self.state = state
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.interval = 10

        self.inode = generate_unique_id(project_path)
        self.base_dir = APP_FOLDER / self.inode

        self.snapshots_dir = self.base_dir / "snapshots"
        self.context_store_dir = self.base_dir / "context_store"

        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self.context_store_dir.mkdir(parents=True, exist_ok=True)

        self.last_known_chat_mod_time = None

    def start_monitoring(self):
        """Inicia el hilo de monitoreo en segundo plano."""
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        print(f"\n[Auto-Snapshot] Activado. Verificando cambios cada {self.interval}s.")

    def stop_monitoring(self):
        """Detiene el hilo de monitoreo de forma segura."""
        self.running = False
        if self.thread:
            self.thread.join(timeout=1.0)
        print("\n[Auto-Snapshot] Detenido.")

    def _loop(self):
        """Bucle principal del hilo."""
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
        """Verifica si el chat en Drive ha cambiado y crea un snapshot si es necesario."""
        chat_id = self.state.get("chat_id")
        if not chat_id:
            return

        metadata = self.api.gdm.get_file_metadata(chat_id)
        if not metadata:
            return

        remote_mod_time = metadata.get("modifiedTime", "")

        # Si la fecha de modificación es diferente a la última conocida
        if (
            self.last_known_chat_mod_time
            and self.last_known_chat_mod_time != remote_mod_time
        ):
            self.create_snapshot(remote_mod_time)

        # Actualizamos la referencia (incluso la primera vez para no crear snapshot al inicio)
        self.last_known_chat_mod_time = remote_mod_time

    def _ensure_context_stored(self, context_md5: str) -> bool:
        """
        Verifica si tenemos guardada esta versión del contexto en el almacén.
        Si no, la copia del archivo actual local.
        Devuelve True si el archivo existe en el almacén al final del proceso.
        """
        store_path = self.context_store_dir / f"{context_md5}.txt"

        if store_path.exists():
            return True

        # Si no existe en el almacén, copiamos el actual
        current_context_path = self.base_dir / "project_context.txt"

        if current_context_path.exists():
            shutil.copy2(current_context_path, store_path)
            return True

        return False

    def create_snapshot(self, mod_time_str: str):
        """Crea un snapshot descargando el chat y vinculándolo al contexto actual."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        snap_folder = self.snapshots_dir / timestamp
        snap_folder.mkdir(parents=True, exist_ok=True)

        # 1. Obtener MD5 actual del contexto desde el estado
        current_md5 = self.state.get("md5")
        if not current_md5:
            # Si no hay MD5, no podemos vincular
            return

        # 2. Asegurar que el contexto esté en el almacén (Deduplicación)
        if not self._ensure_context_stored(current_md5):
            print(
                "\n[Auto-Snapshot] Error: No se encuentra el archivo de contexto fuente para respaldar."
            )
            return

        # 3. Descargar Chat completo
        chat_id = self.state.get("chat_id", "")
        chat_content = self.api.gdm.get_file_content(chat_id)

        if chat_content:
            (snap_folder / "chat.prompt").write_bytes(chat_content)
            
            info = {
                "timestamp": timestamp,
                "human_time": datetime.now().strftime("%H:%M:%S - %d/%m/%Y"),
                "drive_modified_time": mod_time_str,
                "context_md5": current_md5,  
            }
            (snap_folder / "info.json").write_text(json.dumps(info, indent=2))

            # Feedback visual sutil para no romper la CLI
            print(".", end="", flush=True)

    def list_snapshots(self) -> List[dict]:
        """Lista los snapshots disponibles ordenados por fecha."""
        snaps = []
        if not self.snapshots_dir.exists():
            return []

        for d in self.snapshots_dir.iterdir():
            if d.is_dir() and (d / "info.json").exists():
                try:
                    info = json.loads((d / "info.json").read_text())
                    snaps.append(info)
                except Exception:
                    pass

        return sorted(snaps, key=lambda x: x["timestamp"], reverse=True)

    def restore_snapshot(self, timestamp: str) -> bool:
        """Restaura un snapshot específico (Chat + Contexto) hacia Google Drive y local."""
        snap_folder = self.snapshots_dir / timestamp
        if not snap_folder.exists():
            print("Snapshot no encontrado.")
            return False

        print(f"Restaurando snapshot {timestamp}...")

        # 1. Leer Info
        try:
            info = json.loads((snap_folder / "info.json").read_text())
            context_md5 = info.get("context_md5")
        except Exception:
            print("Error leyendo metadata del snapshot.")
            return False

        # 2. Buscar el contexto en el almacén
        stored_context_path = self.context_store_dir / f"{context_md5}.txt"
        if not stored_context_path.exists():
            print(
                f"ERROR CRÍTICO: La versión del contexto {context_md5} no se encuentra en el almacén."
            )
            return False

        chat_file = snap_folder / "chat.prompt"
        if not chat_file.exists():
            print("Archivo de chat no encontrado en snapshot.")
            return False

        # 3. Leer contenidos
        chat_content = chat_file.read_text(encoding="utf-8")
        context_content = stored_context_path.read_text(encoding="utf-8")

        # 4. Subir a Drive
        print("Subiendo contexto antiguo a Drive...")
        self.api.gdm.update_file_from_memory(
            self.state["file_id"], context_content, "text/plain"
        )

        print("Subiendo chat antiguo a Drive...")
        self.api.gdm.update_file_from_memory(
            self.state["chat_id"],
            chat_content,
            "application/vnd.google-makersuite.prompt",
        )

        # 5. Actualizar estado local
        current_local_context = self.base_dir / "project_context.txt"
        shutil.copy2(stored_context_path, current_local_context)

        self.state["md5"] = context_md5
        print("Restauración completada con éxito.")
        return True
