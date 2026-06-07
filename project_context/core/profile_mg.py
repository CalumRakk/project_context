import json
import logging
import shutil
from pathlib import Path
from typing import Optional

from project_context.core.exceptions import (
    ProfileActiveNotFoundError,
    ProfileConfigCorruptError,
    ProfileConfigNotFoundError,
    ProfileNotFoundError,
)
from project_context.core.schemas import ProfileConfig
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

    def load_profile_data(self, profile_name: str) -> ProfileConfig:
        profile_file = self.profiles_dir / f"{profile_name}.json"
        if not profile_file.exists():
            raise ProfileConfigNotFoundError(
                f"El perfil '{profile_name}' no existe en el sistema."
            )
        try:
            data = json.loads(profile_file.read_text(encoding="utf-8"))
            email = data.get("email")
            associated_secret = data.get("associated_secret")
            token_path = self.tokens_dir / f"{email}__{associated_secret}"
            secret_path = self.secrets_dir / data["associated_secret"]
            return ProfileConfig(**data, token_path=token_path, secret_path=secret_path)
        except json.JSONDecodeError:
            raise ProfileConfigCorruptError(
                f"El perfil '{profile_name}' no contiene una estructura JSON legible."
            )

    def save_profile_data(self, profile_name: str, data: ProfileConfig):
        profile_file = self.profiles_dir / f"{profile_name}.json"
        profile_file.write_text(
            json.dumps(data.model_dump_json(), indent=2), encoding="utf-8"
        )

    def resolve_profile_name(self, override: Optional[str]) -> str:
        if override:
            available = self.list_profiles()
            if override not in available:
                raise ProfileNotFoundError(
                    f"El perfil de usuario '{override}' no existe en este equipo."
                )
            return override

        active = self.get_active_profile_name()
        if active:
            return active

        raise ProfileActiveNotFoundError(
            "No hay ningún perfil de usuario activo configurado actualmente."
        )
