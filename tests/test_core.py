import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from project_context.api_drive import AIStudioDriveManager
from project_context.ops import update_context
from project_context.utils import ProfileManager, compute_md5, has_files_modified_since


class TestProjectContextCore(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.root_path = Path(self.test_dir)

        self.project_path = self.root_path / "my_project"
        self.project_path.mkdir()

        self.file1 = self.project_path / "main.py"
        self.file1.write_text("print('hello')", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_compute_md5(self):
        """Verifica que el hash MD5 sea consistente."""
        md5_a = compute_md5(self.file1)
        self.file1.write_text("print('changed')", encoding="utf-8")
        md5_b = compute_md5(self.file1)

        self.assertNotEqual(
            md5_a, md5_b, "El MD5 debería cambiar si el contenido cambia"
        )

    def test_has_files_modified_since(self):
        """Verifica la detección de fecha de modificación."""

        check_time = self.file1.stat().st_mtime

        # Caso A: No hay cambios posteriores a check_time
        self.assertFalse(
            has_files_modified_since(
                check_time + 10, self.project_path, gitignore=False
            ),
            "No debería detectar cambios futuros inexistentes",
        )

        # Caso B: Modificamos el archivo AHORA
        import time

        time.sleep(0.1)
        self.file1.touch()

        self.assertTrue(
            has_files_modified_since(check_time, self.project_path, gitignore=False),
            "Debería detectar que el archivo fue modificado recientemente",
        )

    def test_profile_manager_paths(self):
        """Verifica que el ProfileManager resuelva rutas sin explotar."""

        # Mockeamos la ruta raíz para no tocar tu configuración real en ~/.config
        with patch("project_context.utils.get_app_root_dir") as mock_root:
            mock_root.return_value = self.root_path / "config_fake"

            pm = ProfileManager()
            pm.set_active_profile("test_user")

            wd = pm.get_working_dir()
            self.assertTrue(
                str(wd).endswith("test_user"),
                "El working dir debe terminar en el nombre del perfil",
            )
            self.assertTrue(
                wd.exists(), "El directorio del perfil debe crearse automáticamente"
            )

    @patch("project_context.ops.generate_context")
    @patch("project_context.ops.save_context")
    def test_update_context_logic(self, mock_save, mock_generate):
        """
        update_context debe decidir si llamar a la API o no.
        """
        mock_api = MagicMock(spec=AIStudioDriveManager)
        mock_api.gdm = MagicMock()

        # Simulamos que generate_context devuelve contenido nuevo
        mock_generate.return_value = ("contenido nuevo", 100)

        # Simulamos guardar el contexto en disco temporal
        context_file = self.root_path / "temp_context.txt"
        context_file.write_text("contenido nuevo")
        mock_save.return_value = context_file

        # ESTADO INICIAL DEL PROYECTO (Simulado)
        state = {
            "path": str(self.project_path),
            "last_modified": 0,
            "md5": "hash_viejo",
            "chat_id": "chat_123",
            "file_id": "file_123",
        }

        new_state = update_context(mock_api, self.project_path, state)

        # ASERCIONES

        # 1. ¿Se llamó a la API de Drive para actualizar?
        mock_api.gdm.update_file_from_memory.assert_called_once()
        args, _ = mock_api.gdm.update_file_from_memory.call_args
        self.assertEqual(
            args[0], "file_123", "Debe actualizar el ID de archivo correcto"
        )

        # 2. ¿Se actualizó el estado?
        self.assertNotEqual(
            new_state["md5"], "hash_viejo", "El MD5 en el estado debe actualizarse"
        )
        self.assertGreater(
            new_state["last_modified"], 0, "El timestamp debe actualizarse"
        )

    @patch("project_context.ops.generate_context")
    @patch("project_context.ops.save_context")
    def test_update_context_no_changes(self, mock_save, mock_generate):
        """
        Prueba crítica: Si el MD5 es igual, NO debe llamar a la API.
        """
        mock_api = MagicMock(spec=AIStudioDriveManager)
        mock_api.gdm = MagicMock()

        # El contenido generado es idéntico al "hash_viejo" simulado abajo
        content = "contenido igual"
        mock_generate.return_value = (content, 100)

        context_file = self.root_path / "temp_context.txt"
        context_file.write_text(content)
        mock_save.return_value = context_file

        # Calculamos hash real para simular que ya lo tenemos
        real_hash = compute_md5(context_file)

        state = {
            "path": str(self.project_path),
            "last_modified": 0,
            "md5": real_hash,
            "chat_id": "chat_123",
            "file_id": "file_123",
        }

        update_context(mock_api, self.project_path, state)

        # ASERCIÓN: La API NO debe ser llamada
        mock_api.gdm.update_file_from_memory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
