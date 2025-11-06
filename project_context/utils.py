import json
import os
import sys
from pathlib import Path
from typing import cast


def get_user_config_dir(app_name: str) -> Path:
    if sys.platform.startswith("win"):
        # En Windows se usa APPDATA → Roaming
        return Path(cast(str, os.getenv("APPDATA"))) / app_name
    elif sys.platform == "darwin":
        # En macOS
        return Path.home() / "Library" / "Application Support" / app_name
    else:
        # En Linux / Unix
        return Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config")) / app_name


project_path = get_user_config_dir("project_context")
chat_path_cache = project_path / "chat_cache.json"


def save_chats(chats: list[dict], chat_path_cache=chat_path_cache):
    chat_path_cache.parent.mkdir(parents=True, exist_ok=True)
    with open(chat_path_cache, "w", encoding="utf-8") as f:
        json.dump(chats, f, ensure_ascii=False, indent=4)


def load_chats(chat_path_cache=chat_path_cache) -> list[dict]:
    if not chat_path_cache.exists():
        return []
    with open(chat_path_cache, "r", encoding="utf-8") as f:
        chats = json.load(f)
    return chats
