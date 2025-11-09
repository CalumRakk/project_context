import json
import os
import sys
from pathlib import Path
from typing import Union, cast

from gitingest import ingest

from project_context.models import generate_unique_id


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


def generate_context(proyect_path: Union[str, Path]) -> str:
    proyect_path = Path(proyect_path) if isinstance(proyect_path, str) else proyect_path
    summary, tree, content = ingest(str(proyect_path))
    context = tree + "\n\n" + content
    return context


def save_context(project_path: Union[str, Path], context: str) -> Path:
    project_path = Path(project_path) if isinstance(project_path, str) else project_path
    inodo = generate_unique_id(project_path)
    output = project_path / "context" / f"{inodo}.txt"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(context, encoding="utf-8")
    return output
