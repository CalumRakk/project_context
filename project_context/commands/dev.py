import json
import time
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
            val_str = ", ".join(f'"{v}"' if isinstance(v, str) else str(v) for v in values)
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
                    changes.append(f"[bold yellow]~ Lista Modificada:[/] {current_path} (Tamaño: {len(d1[k])} -> {len(d2[k])})")
            elif d1[k] != d2[k]:
                changes.append(f"[bold yellow]~ Modificado:[/] {current_path}: [dim]{repr(d1[k])}[/] -> [bold]{repr(d2[k])}[/]")

    for k in d1:
        current_path = f"{path}.{k}" if path else k
        if k not in d2:
            changes.append(f"[bold red]- Eliminado:[/] {current_path} (Valor anterior: {repr(d1[k])})")

    return changes


@app.command("watch-schema")
def watch_schema(
    chat_id: Annotated[
        str,
        typer.Argument(help="El ID del archivo del chat en Google Drive."),
    ],
    use_profile: Annotated[
        Optional[str],
        typer.Option(
            "--use", help="Usa un perfil específico para esta ejecución."
        ),
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

    console.print(Panel.fit(
        "[bold green]¡Observador activo![/]\n"
        "1. Abre este chat en Google AI Studio.\n"
        "2. Cambia opciones (Resolución, Modelo, Tools, Safety).\n"
        "3. Guarda el chat.\n"
        "4. Mira esta consola para ver qué cambió internamente.\n"
        "[dim]Presiona Ctrl+C para salir y ver el resumen final.[/]",
        title="Schema Watcher"
    ))

    try:
        while True:
            time.sleep(3) # Polling cada 3 segundos
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
                        console.print(f"\n[bold cyan]--- Cambio detectado a las {time.strftime('%H:%M:%S')} ---[/]")
                        for change in changes:
                            console.print(change)

                    # Actualizar estado base
                    state_a = state_b
                    last_mod_time = current_mod_time

    except KeyboardInterrupt:
        UI.info("\nDeteniendo observador. Generando reporte final...")
        tracker.print_summary()
