import json
import logging
import shutil
from pathlib import Path
from typing import Optional, Tuple

from project_context.ui.ui import UI
from project_context.utils import get_app_root_dir

logger = logging.getLogger(__name__)


class ProfileManager:
    def __init__(self):
        self.root_dir = get_app_root_dir()
        self.profiles_dir = self.root_dir / "profiles"
        self.secrets_dir = self.root_dir / "secrets"
        self.tokens_dir = self.root_dir / "tokens"
        self.active_profile_file = self.root_dir / "active_profile"
        self.config_file = self.root_dir / "global_config.json"
        self._temp_profile: Optional[str] = None
        self._ensure_structure()

    def _ensure_structure(self):
        """Crea la estructura base y migra datos antiguos si existen."""
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.profiles_dir.mkdir(exist_ok=True)
        self.secrets_dir.mkdir(exist_ok=True)
        self.tokens_dir.mkdir(exist_ok=True)

        legacy_secret = self.root_dir / "client_secrets.json"
        target_secret = self.secrets_dir / "client_secrets.json"
        if legacy_secret.exists() and not target_secret.exists():
            try:
                shutil.copy2(str(legacy_secret), str(target_secret))
            except Exception as e:
                logger.warning(f"No se pudo migrar el secreto legacy: {e}")

        if self.config_file.exists():
            try:
                config = json.loads(self.config_file.read_text(encoding="utf-8"))
                old_profile = config.get("current_profile")
                if old_profile and old_profile != "default":
                    profile_file = self.profiles_dir / f"{old_profile}.json"
                    if profile_file.exists():
                        self.active_profile_file.write_text(
                            old_profile, encoding="utf-8"
                        )
                self.config_file.unlink()
            except Exception:
                pass

    def set_temporary_profile(self, profile_name: str):
        """Establece un perfil activo solo para la ejecución actual en memoria."""
        self._temp_profile = profile_name

    def get_active_profile_name(self) -> Optional[str]:
        if self._temp_profile:
            return self._temp_profile

        if not self.active_profile_file.exists():
            return None

        try:
            name = self.active_profile_file.read_text(encoding="utf-8").strip()
            if name:
                profile_file = self.profiles_dir / f"{name}.json"
                if profile_file.exists():
                    return name
                else:
                    try:
                        self.active_profile_file.unlink()
                    except Exception:
                        pass
        except Exception:
            pass
        return None

    def set_active_profile(self, profile_name: str):
        self._temp_profile = None
        profile_file = self.profiles_dir / f"{profile_name}.json"
        if not profile_file.exists():
            raise FileNotFoundError(
                f"El perfil '{profile_name}' no existe en el sistema."
            )
        self.active_profile_file.write_text(profile_name, encoding="utf-8")

    def get_working_dir(self) -> Path:
        return self.root_dir

    def list_profiles(self) -> list[str]:
        return [f.stem for f in self.profiles_dir.glob("*.json")]

    def load_profile_data(self, profile_name: str) -> dict:
        profile_file = self.profiles_dir / f"{profile_name}.json"
        if not profile_file.exists():
            return {}
        try:
            return json.loads(profile_file.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def save_profile_data(self, profile_name: str, data: dict):
        profile_file = self.profiles_dir / f"{profile_name}.json"
        profile_file.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def get_active_profile_data(self) -> dict:
        name = self.get_active_profile_name()
        if not name:
            return {}
        return self.load_profile_data(name)

    def save_active_profile_data(self, data: dict):
        name = self.get_active_profile_name()
        if not name:
            raise ValueError("No hay ningún perfil activo configurado.")
        self.save_profile_data(name, data)

    def resolve_secrets_file(self) -> Tuple[Path, str]:
        profile_name = self.get_active_profile_name()
        if not profile_name:
            raise ValueError("No hay ningún perfil activo configurado.")

        profile_data = self.get_active_profile_data()
        secret_name = profile_data.get("associated_secret")

        available_secrets = sorted(
            [f for f in self.secrets_dir.glob("*.json") if f.is_file()]
        )

        if secret_name:
            if not secret_name.endswith(".json"):
                secret_name += ".json"
            specific_path = self.secrets_dir / secret_name
            if specific_path.exists():
                return specific_path, f"Asociado al perfil ({secret_name})"

        if len(available_secrets) == 1:
            auto_secret = available_secrets[0]
            profile_data["associated_secret"] = auto_secret.name
            self.save_profile_data(profile_name, profile_data)
            UI.info(
                f"Auto-asociando el único secreto disponible: [bold]{auto_secret.name}[/]"
            )
            return auto_secret, f"Auto-detectado ({auto_secret.name})"

        elif len(available_secrets) > 1:
            raise ValueError(
                f"Conflicto de credenciales: Se detectaron {len(available_secrets)} secretos y "
                f"el perfil '{profile_name}' no tiene un secreto asociado.\n"
                f"Especifique uno usando: set-secrets o cambie de perfil."
            )

        else:
            fallback_name = secret_name if secret_name else f"{profile_name}.json"
            if not fallback_name.endswith(".json"):
                fallback_name += ".json"
            return self.secrets_dir / fallback_name, "Predeterminado (Faltante)"

    def get_secrets_association_map(self) -> dict:
        association_map = {}
        if self.secrets_dir.exists():
            for file in self.secrets_dir.glob("*.json"):
                association_map[file.name] = {
                    "path": file,
                    "associated_profiles": [],
                    "exists_on_disk": True,
                }

        profiles = self.list_profiles()
        for profile_name in profiles:
            profile_data = self.load_profile_data(profile_name)
            secret_name = profile_data.get("associated_secret")

            if secret_name:
                if not secret_name.endswith(".json"):
                    secret_name += ".json"
                if secret_name not in association_map:
                    association_map[secret_name] = {
                        "path": self.secrets_dir / secret_name,
                        "associated_profiles": [],
                        "exists_on_disk": False,
                    }
                association_map[secret_name]["associated_profiles"].append(profile_name)

        return association_map

    def remove_tokens_for_secret(self, secret_name: str) -> int:
        if not secret_name.endswith(".json"):
            secret_name += ".json"

        removed_count = 0
        if self.tokens_dir.exists():
            for token_file in self.tokens_dir.iterdir():
                if token_file.is_file() and token_file.name.endswith(
                    f"__{secret_name}"
                ):
                    try:
                        token_file.unlink()
                        removed_count += 1
                    except Exception as e:
                        logger.warning(
                            f"No se pudo limpiar el token residual '{token_file.name}': {e}"
                        )
        return removed_count


profile_manager = ProfileManager()
