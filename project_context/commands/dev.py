import json
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import typer
from rich.panel import Panel
from rich.table import Table
from typing_extensions import Annotated

from project_context.api_drive import AIStudioDriveManager
from project_context.utils import UI, console, profile_manager

app = typer.Typer(help="Herramientas de desarrollo y depuración internas.")


class SchemaTracker:
    """Clase para acumular el conocimiento descubierto sobre el schema."""

    def __init__(self):
        self.discovered_enums: Dict[str, set] = {}
        self.discovered_types: Dict[str, str] = {}

    def track(self, path: str, value: Any):
        if "chunkedPrompt.chunks" in path:
            return

        val_type = type(value).__name__
        self.discovered_types[path] = val_type

        # Si es un string o un int, lo tratamos como un posible Enum
        if isinstance(value, (str, int, float, bool)):
            if path not in self.discovered_enums:
                self.discovered_enums[path] = set()
            self.discovered_enums[path].add(value)

    def print_summary(self):
        table = Table(title="Conocimiento del Schema Acumulado", show_lines=True)
        table.add_column("Ruta (Path)", style="cyan")
        table.add_column("Tipo", style="magenta")
        table.add_column("Valores Observados (Enums)", style="green")

        # Ordenar alfabéticamente para mejor lectura
        for path in sorted(self.discovered_types.keys()):
            v_type = self.discovered_types[path]
            values = self.discovered_enums.get(path, set())
            val_str = ", ".join(
                f'"{v}"' if isinstance(v, str) else str(v) for v in values
            )
            table.add_row(path, v_type, val_str)

        console.print("\n")
        console.print(table)


def dict_diff(d1: dict, d2: dict, tracker: SchemaTracker, path: str = "") -> list:
    """Compara dos diccionarios recursivamente y devuelve una lista de diferencias visuales."""
    changes = []

    for k in d2:
        current_path = f"{path}.{k}" if path else k
        tracker.track(current_path, d2[k])

        if k not in d1:
            changes.append(f"[bold green]+ Añadido:[/] {current_path} = {repr(d2[k])}")
        else:
            if isinstance(d2[k], dict) and isinstance(d1[k], dict):
                changes.extend(dict_diff(d1[k], d2[k], tracker, current_path))
            elif isinstance(d2[k], list) and isinstance(d1[k], list):
                if d1[k] != d2[k]:
                    changes.append(
                        f"[bold yellow]~ Lista Modificada:[/] {current_path} (Tamaño: {len(d1[k])} -> {len(d2[k])})"
                    )
            elif d1[k] != d2[k]:
                changes.append(
                    f"[bold yellow]~ Modificado:[/] {current_path}: [dim]{repr(d1[k])}[/] -> [bold]{repr(d2[k])}[/]"
                )

    for k in d1:
        current_path = f"{path}.{k}" if path else k
        if k not in d2:
            changes.append(
                f"[bold red]- Eliminado:[/] {current_path} (Valor anterior: {repr(d1[k])})"
            )

    return changes


@app.command("watch-schema")
def watch_schema(
    chat_id: Annotated[
        str,
        typer.Argument(help="El ID del archivo del chat en Google Drive."),
    ],
    use_profile: Annotated[
        Optional[str],
        typer.Option("--use", help="Usa un perfil específico para esta ejecución."),
    ] = None,
):
    """
    Observa un chat en Drive y hace ingeniería inversa del Schema de AI Studio.
    """
    if use_profile:
        available_profiles = profile_manager.list_profiles()
        if use_profile not in available_profiles:
            typer.secho(
                f"Error: El perfil de usuario '{use_profile}' no existe.",
                fg=typer.colors.RED,
            )
            typer.echo(f"Perfiles disponibles: {', '.join(available_profiles)}")
            raise typer.Exit(code=1)

        profile_manager.set_temporary_profile(use_profile)
        typer.secho(f"Usando perfil temporal: {use_profile}", fg=typer.colors.YELLOW)

    UI.info("Iniciando modo observador de Schema...")
    try:
        api = AIStudioDriveManager()
    except Exception as e:
        UI.error(f"Error de autenticación: {e}")
        raise typer.Exit(1)

    UI.info(f"Conectando al chat ID: [bold]{chat_id}[/]")

    base_metadata = api.gdm.get_file_metadata(chat_id)
    if not base_metadata:
        UI.error("No se encontró el archivo. Verifica el ID y el perfil (--use).")
        raise typer.Exit(1)

    last_mod_time = base_metadata.get("modifiedTime")
    raw_bytes = api.gdm.get_file_content(chat_id)

    if not raw_bytes:
        UI.error("No se pudo descargar el contenido inicial.")
        raise typer.Exit(1)

    try:
        state_a = json.loads(raw_bytes.decode("utf-8"))
    except json.JSONDecodeError:
        UI.error("El archivo no es un JSON válido.")
        raise typer.Exit(1)

    tracker = SchemaTracker()

    # Alimentar el tracker con el estado inicial
    dict_diff({}, state_a, tracker)

    console.print(
        Panel.fit(
            "[bold green]¡Observador activo![/]\n"
            "1. Abre este chat en Google AI Studio.\n"
            "2. Cambia opciones (Resolución, Modelo, Tools, Safety).\n"
            "3. Guarda el chat.\n"
            "4. Mira esta consola para ver qué cambió internamente.\n"
            "[dim]Presiona Ctrl+C para salir y ver el resumen final.[/]",
            title="Schema Watcher",
        )
    )

    try:
        while True:
            time.sleep(3)  # Polling cada 3 segundos
            current_metadata = api.gdm.get_file_metadata(chat_id)
            if not current_metadata:
                continue

            current_mod_time = current_metadata.get("modifiedTime")

            if current_mod_time != last_mod_time:
                # El archivo cambió en Drive
                raw_bytes = api.gdm.get_file_content(chat_id)
                if raw_bytes:
                    state_b = json.loads(raw_bytes.decode("utf-8"))

                    # Calcular e imprimir diferencias
                    changes = dict_diff(state_a, state_b, tracker)

                    if changes:
                        console.print(
                            f"\n[bold cyan]--- Cambio detectado a las {time.strftime('%H:%M:%S')} ---[/]"
                        )
                        for change in changes:
                            console.print(change)

                    # Actualizar estado base
                    state_a = state_b
                    last_mod_time = current_mod_time

    except KeyboardInterrupt:
        UI.info("\nDeteniendo observador. Generando reporte final...")
        tracker.print_summary()


def fuzzy_match_paths(
    stored_path_str: str, current_path_obj: Path, depth: int = 2
) -> bool:
    stored = stored_path_str.replace("\\", "/").strip().lower()
    current = str(current_path_obj).replace("\\", "/").strip().lower()

    stored_parts = [p for p in stored.split("/") if p]
    current_parts = [p for p in current.split("/") if p]

    if stored_parts and ":" in stored_parts[0]:
        stored_parts.pop(0)
    if current_parts and ":" in current_parts[0]:
        current_parts.pop(0)

    if not stored_parts or not current_parts:
        return False

    compare_depth = min(depth, len(stored_parts), len(current_parts))
    stored_tail = stored_parts[-compare_depth:]
    current_tail = current_parts[-compare_depth:]

    return stored_tail == current_tail


def scan_legacy_global_states(project_path: Path) -> list:
    profiles_dir = profile_manager.profiles_dir
    matches = []

    if not profiles_dir.exists():
        return matches

    for state_file in profiles_dir.rglob("project_context_state.json"):
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
            path_str = data.get("path")
            if not path_str:
                continue

            if fuzzy_match_paths(path_str, project_path, depth=2):
                rel = state_file.relative_to(profiles_dir)
                parts = rel.parts
                profile_name = parts[0] if len(parts) > 0 else "unknown"
                inode_dir = parts[1] if len(parts) > 1 else "unknown"

                snapshots_dir = state_file.parent / "snapshots"
                snapshots_count = 0
                if snapshots_dir.exists() and snapshots_dir.is_dir():
                    snapshots_count = sum(
                        1
                        for d in snapshots_dir.iterdir()
                        if d.is_dir() and (d / "info.json").exists()
                    )

                mtime = state_file.stat().st_mtime

                chat_id = data.get("chat_id")
                file_id = data.get("file_id")
                if not chat_id and "legacy_migration_data" in data:
                    chat_id = data["legacy_migration_data"].get("chat_id")
                    file_id = data["legacy_migration_data"].get("file_id")

                matches.append(
                    {
                        "profile_name": profile_name,
                        "inode_dir": inode_dir,
                        "state_file_path": state_file,
                        "last_modified": mtime,
                        "snapshots_count": snapshots_count,
                        "chat_id": chat_id or "N/A",
                        "file_id": file_id or "N/A",
                    }
                )
        except Exception:
            continue

    matches.sort(key=lambda x: x["last_modified"], reverse=True)
    return matches


def display_legacy_states(matches: list):
    table = Table(
        title="\n[bold yellow]REGISTROS PREVIOS DETECTADOS EN EL ALMACENAMIENTO GLOBAL[/]",
        show_header=True,
        header_style="bold magenta",
        box=None,
    )
    table.add_column("Índice", style="dim", justify="right")
    table.add_column("Perfil", style="green")
    table.add_column("ID Inodo (Antiguo)", style="cyan")
    table.add_column("Última Modif.", style="white")
    table.add_column("Snapshots", style="blue", justify="right")
    table.add_column("Chat ID (Drive)", style="dim")

    for idx, m in enumerate(matches, 1):
        dt_str = datetime.fromtimestamp(m["last_modified"]).strftime(
            "%d/%m/%Y %H:%M:%S"
        )
        chat_id_display = m["chat_id"]
        if chat_id_display != "N/A" and len(chat_id_display) > 20:
            chat_id_display = chat_id_display[:10] + "..." + chat_id_display[-7:]

        table.add_row(
            str(idx),
            m["profile_name"],
            m["inode_dir"],
            dt_str,
            str(m["snapshots_count"]),
            chat_id_display,
        )

    console.print(table)
    console.print("")


@app.command("migrate-legacy")
def migrate_legacy(
    project_path: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=False,
            resolve_path=True,
            help="Ruta del directorio del proyecto a migrar.",
        ),
    ] = Path.cwd(),
):
    """
    Busca snapshots y estados heredados en el almacenamiento global y los consolida en el proyecto local actual.
    """
    UI.info(
        f"Escaneando historial global para el proyecto: [bold cyan]{project_path}[/]"
    )
    matches = scan_legacy_global_states(project_path)

    if not matches:
        UI.warn(
            "No se encontraron registros previos para este proyecto en el almacenamiento global."
        )
        raise typer.Exit()

    display_legacy_states(matches)

    confirm = typer.confirm(
        "¿Deseas proceder con la migración y consolidación de un registro histórico?",
        default=True,
    )
    if not confirm:
        UI.info("Operación cancelada.")
        raise typer.Exit()

    selected_idx = 0
    if len(matches) > 1:
        val = typer.prompt(
            f"Selecciona el número de registro a importar (1-{len(matches)})", type=int
        )
        if 1 <= val <= len(matches):
            selected_idx = val - 1
        else:
            UI.error("Selección inválida.")
            raise typer.Exit(code=1)

    selected_match = matches[selected_idx]

    clean_drive = typer.confirm(
        "¿Deseas que el programa intente eliminar el chat antiguo de Google Drive para evitar duplicados?",
        default=True,
    )

    # Autenticación requerida para compilar el manager y limpiar Drive
    try:
        api = AIStudioDriveManager()
    except Exception as e:
        UI.error(f"Fallo al inicializar autenticación de Drive: {e}")
        raise typer.Exit(code=1)

    local_dir = project_path / ".project_context"
    local_dir.mkdir(parents=True, exist_ok=True)

    global_state_path = Path(selected_match["state_file_path"])
    global_snapshots_dir = global_state_path.parent / "snapshots"

    UI.info("Copiando archivos al proyecto local...")

    # 1. Copiar y sanitizar el archivo de estado
    try:
        old_state = json.loads(global_state_path.read_text(encoding="utf-8"))
    except Exception as e:
        UI.error(f"No se pudo leer el estado global: {e}")
        raise typer.Exit(code=1)

    old_chat_id = old_state.get("chat_id")
    old_state["chat_id"] = None
    old_state["file_id"] = None
    old_state["path"] = str(project_path)

    # 2. Copiar snapshots individuales
    if global_snapshots_dir.exists() and global_snapshots_dir.is_dir():
        local_snapshots_dir = local_dir / "snapshots"
        local_snapshots_dir.mkdir(parents=True, exist_ok=True)

        for folder in global_snapshots_dir.iterdir():
            if folder.is_dir() and folder.name not in ("objects", "context_store"):
                info_file = folder / "info.json"
                if info_file.exists():
                    target_snap_dir = local_snapshots_dir / folder.name
                    target_snap_dir.mkdir(parents=True, exist_ok=True)

                    try:
                        info_data = json.loads(info_file.read_text(encoding="utf-8"))
                        orig_msg = info_data.get("message") or ""
                        tag = f"[Migrado - Perfil: {selected_match['profile_name']}]"
                        info_data["message"] = f"{tag} {orig_msg}".strip()

                        (target_snap_dir / "info.json").write_text(
                            json.dumps(info_data, indent=2, ensure_ascii=False),
                            encoding="utf-8",
                        )
                    except Exception as e:
                        UI.warn(f"No se pudo etiquetar el snapshot {folder.name}: {e}")
                        shutil.copy2(info_file, target_snap_dir / "info.json")

                    chat_file = folder / "chat.prompt"
                    if chat_file.exists():
                        shutil.copy2(chat_file, target_snap_dir / "chat.prompt")

    # 3. Copiar archivo de contexto
    legacy_ctx_file = global_state_path.parent / "project_context.txt"
    if not legacy_ctx_file.exists():
        legacy_ctx_file = global_state_path.parent / "last_context.txt"

    if legacy_ctx_file.exists():
        shutil.copy2(legacy_ctx_file, local_dir / "last_context.txt")

    # 4. Guardar archivo de estado local consolidado
    from project_context.utils import save_project_context_state

    save_project_context_state(project_path, old_state)

    # 5. Compilar base de datos SQLite / CAS local
    try:
        from project_context.history import SnapshotManager

        manager = SnapshotManager(api=api, project_path=project_path, state=old_state)
        manager.stop_monitoring()
        UI.success("Snapshots migrados y base de datos SQLite compilada localmente.")
    except Exception as e:
        UI.warn(f"Fallo al compilar la base de datos de snapshots local: {e}")

    # 6. Eliminar chat antiguo de Google Drive si fue solicitado
    if clean_drive and old_chat_id:
        UI.info(f"Removiendo chat antiguo de Drive ({old_chat_id})...")
        try:
            api.gdm.service.files().delete(fileId=old_chat_id).execute()
            UI.success("Archivo antiguo eliminado de Google Drive.")
        except Exception as e:
            UI.warn(f"No se pudo remover el archivo antiguo de la nube: {e}")

    # 7. Remover el directorio de origen global de forma definitiva
    try:
        shutil.rmtree(global_state_path.parent)
        UI.success("Registro de inodo global limpiado del almacenamiento de perfiles.")
    except Exception as e:
        UI.warn(f"No se pudo limpiar el directorio global de origen: {e}")

    UI.success("\n¡Migración finalizada con éxito!")
    UI.info("Ya puedes iniciar tu sesión con: [bold cyan]project_context run .[/]")
