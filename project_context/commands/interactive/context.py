from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.table import Table

from project_context.commands.interactive.register import ParsedArgs, SessionContext
from project_context.ui import UI

console_rich = Console()


def _get_relative_path(path_str: str, project_path: Path) -> Optional[str]:
    """Resuelve la entrada del usuario a una ruta relativa válida dentro del proyecto."""
    target = Path(path_str)
    try:
        if target.is_absolute():
            return target.relative_to(project_path).as_posix()
        else:
            abs_target = (Path.cwd() / target).resolve()
            return abs_target.relative_to(project_path.resolve()).as_posix()
    except ValueError:
        UI.error(
            f"La ruta '{path_str}' debe estar ubicada dentro del proyecto '{project_path}'."
        )
        return None


def cmd_context(ctx: SessionContext, args: ParsedArgs):
    """Manejador interactivo para la gestión integral del contexto."""
    project_context = ctx.project_context
    project_path = project_context.project_path
    config = project_context.load_context_config()

    action = args.get_arg(0)
    if not action:
        action = "status"

    action = action.lower()

    # CONTROL E INSPECCIÓN: STATUS

    if action == "status":
        table = Table(
            title="[bold cyan]Configuración de Contexto Activo[/]",
            show_header=True,
            header_style="bold magenta",
            box=None,
        )
        table.add_column("Categoría", style="bold green", width=32)
        table.add_column("Ruta / Patrón / Destino", style="white")

        # Área de trabajo local
        if config.folders == ["."]:
            table.add_row("Área de Trabajo", "[dim cyan]. (Proyecto Completo)[/]")
        else:
            for folder in config.folders:
                table.add_row("Área de Trabajo", folder)

        # Archivos forzados / Excepciones (Whitelist con diagnóstico de anulación)
        if config.files:
            for file_path in config.files:
                override = project_context.check_file_override(file_path)
                if override == "exclude":
                    table.add_row(
                        "Archivos Forzados (Excepciones)",
                        f"{file_path} [bold yellow](Anula exclude)[/]",
                    )
                elif override == ".gitignore":
                    table.add_row(
                        "Archivos Forzados (Excepciones)",
                        f"{file_path} [bold yellow](Anula .gitignore)[/]",
                    )
                else:
                    table.add_row("Archivos Forzados (Excepciones)", file_path)

        # Paquetes externos (link)
        external_folders = getattr(config, "external_folders", {})
        if external_folders:
            for alias, ext_path in external_folders.items():
                table.add_row(f"Paquete Externo ({alias})", ext_path)

        # Exclusiones de ruido (exclude)
        if not config.exclusions:
            table.add_row(
                "Exclusiones Activas", "[dim]Ninguna (solo aplica .gitignore)[/]"
            )
        else:
            for exc in config.exclusions:
                table.add_row("Exclusiones Activas", exc)

        UI.print("", indent=False)
        console_rich.print(table)
        UI.print("", indent=False)
        return

    # ENFOQUE LOCAL: SET

    elif action == "set":
        target_raw = args.get_arg(1)
        if not target_raw:
            UI.error("Uso: context set <ruta_carpeta_o_archivo>")
            return

        if target_raw.strip() == ".":
            config.folders = ["."]
            config.files = []
            project_context.save_context_config(config)
            UI.success("Área de trabajo redefinida a todo el proyecto (raíz '.').")
            UI.info("Escribe [bold yellow]update[/] para sincronizar con Google Drive.")
            return

        rel_str = _get_relative_path(target_raw, project_path)
        if rel_str is None:
            return

        full_path = project_path / rel_str
        if not full_path.exists():
            UI.error(f"La ruta '{rel_str}' no existe físicamente en el repositorio.")
            return

        if full_path.is_dir():
            config.folders = [rel_str]
            config.files = []
            UI.success(
                f"Área de trabajo fijada exclusivamente en la carpeta '{rel_str}'."
            )
        else:
            config.folders = []
            config.files = [rel_str]
            UI.success(f"Foco fijado exclusivamente en el archivo '{rel_str}'.")

            # Feedback proactivo si anula reglas
            override = project_context.check_file_override(rel_str)
            if override == "exclude":
                UI.info("Nota: Este archivo anula una regla de exclusión activa.")
            elif override == ".gitignore":
                UI.info("Nota: Este archivo anula una regla de .gitignore.")

        project_context.save_context_config(config)
        UI.info(
            "Escribe [bold yellow]update[/] para sincronizar este foco con Google Drive."
        )

    # ENFOQUE LOCAL: ADD

    elif action == "add":
        target_raw = args.get_arg(1)
        if not target_raw:
            UI.error("Uso: context add <ruta_carpeta_o_archivo>")
            return

        if target_raw.strip() == ".":
            config.folders = ["."]
            config.files = []
            project_context.save_context_config(config)
            UI.success("Restablecido contexto completo del proyecto (raíz '.').")
            UI.info("Escribe [bold yellow]update[/] para sincronizar.")
            return

        rel_str = _get_relative_path(target_raw, project_path)
        if rel_str is None:
            return

        full_path = project_path / rel_str
        if not full_path.exists():
            UI.error(f"La ruta '{rel_str}' no existe físicamente en el repositorio.")
            return

        # Regla de transición: al añadir un elemento específico, quitamos la raíz general
        if config.folders == ["."]:
            config.folders = []

        if full_path.is_dir():
            if rel_str not in config.folders:
                config.folders.append(rel_str)
                UI.success(f"Carpeta '{rel_str}' añadida al área de trabajo.")
            else:
                UI.info(f"La carpeta '{rel_str}' ya está registrada en el foco.")
        else:
            if rel_str not in config.files:
                config.files.append(rel_str)
                UI.success(f"Archivo '{rel_str}' añadido como excepción prioritaria.")

                # Feedback proactivo de anulación
                override = project_context.check_file_override(rel_str)
                if override == "exclude":
                    UI.info("Nota: Este archivo anula una regla de exclusión activa.")
                elif override == ".gitignore":
                    UI.info("Nota: Este archivo anula una regla de .gitignore.")
            else:
                UI.info(f"El archivo '{rel_str}' ya está registrado en el foco.")

        project_context.save_context_config(config)
        UI.info("Escribe [bold yellow]update[/] para sincronizar con Google Drive.")

    # ENFOQUE LOCAL: REMOVE / RM

    elif action in ("remove", "rm"):
        target_raw = args.get_arg(1)
        if not target_raw:
            UI.error("Uso: context remove <ruta_carpeta_o_archivo_local>")
            return

        rel_str = _get_relative_path(target_raw, project_path)
        if rel_str is None:
            return

        removed = False
        if rel_str in config.folders:
            config.folders.remove(rel_str)
            removed = True
            UI.success(f"Carpeta local '{rel_str}' removida del foco.")
        elif rel_str in config.files:
            config.files.remove(rel_str)
            removed = True
            UI.success(f"Archivo local '{rel_str}' removido de las excepciones.")

        if not removed:
            # Si no era local, orientamos al usuario al comando correcto
            if (
                hasattr(config, "external_folders")
                and target_raw in config.external_folders
            ):
                UI.warn(
                    f"'{target_raw}' es un paquete externo vinculado. Para desconectarlo usa: [bold yellow]context unlink {target_raw}[/]"
                )
            elif target_raw in config.exclusions:
                UI.warn(
                    f"'{target_raw}' es un patrón de exclusión. Para quitarlo usa: [bold yellow]context unexclude {target_raw}[/]"
                )
            else:
                UI.warn(
                    f"No se encontró '{rel_str}' en el área de trabajo ni en los archivos enfocados."
                )
            return

        # Si el foco queda vacío, volvemos al proyecto raíz
        if not config.folders and not config.files:
            config.folders = ["."]
            UI.info(
                "No quedan focos locales activos. Reestablecido al proyecto completo (raíz '.')."
            )

        project_context.save_context_config(config)
        UI.info("Escribe [bold yellow]update[/] para sincronizar con Google Drive.")

    # FILTROS DE RUIDO: EXCLUDE / UNEXCLUDE

    elif action == "exclude":
        pattern = args.get_arg(1)
        if not pattern:
            UI.error("Uso: context exclude <patrón_glob>")
            return

        if pattern not in config.exclusions:
            config.exclusions.append(pattern)
            UI.success(f"Patrón de exclusión '{pattern}' añadido.")
            project_context.save_context_config(config)
            UI.info("Escribe [bold yellow]update[/] para sincronizar con Google Drive.")
        else:
            UI.info(f"El patrón '{pattern}' ya se encuentra excluido.")

    elif action == "unexclude":
        pattern = args.get_arg(1)
        if not pattern:
            UI.error("Uso: context unexclude <patrón_glob>")
            return

        if pattern in config.exclusions:
            config.exclusions.remove(pattern)
            UI.success(f"Patrón de exclusión '{pattern}' removido.")
            project_context.save_context_config(config)
            UI.info("Escribe [bold yellow]update[/] para sincronizar con Google Drive.")
        else:
            UI.warn(f"El patrón '{pattern}' no está registrado en las exclusiones.")

    # PAQUETES EXTERNOS: LINK / UNLINK

    elif action == "link":
        alias = args.get_arg(1)
        target_raw = args.get_arg(2)

        if not alias or not target_raw:
            UI.error("Uso: context link <alias> <ruta_carpeta_externa>")
            return

        target_path = Path(target_raw)
        if not target_path.is_absolute():
            target_path = (Path.cwd() / target_path).resolve()

        try:
            project_context.validate_external_folder(alias, target_path)
        except ValueError as e:
            UI.error(str(e))
            return

        if not hasattr(config, "external_folders"):
            config.external_folders = {}

        config.external_folders[alias] = target_path.as_posix()
        project_context.save_context_config(config)

        UI.success(
            f"Paquete externo '{alias}' vinculado desde '{target_path.as_posix()}'."
        )
        UI.info("Escribe [bold yellow]update[/] para sincronizar con Google Drive.")

    elif action == "unlink":
        alias = args.get_arg(1)
        if not alias:
            UI.error("Uso: context unlink <alias>")
            return

        external_folders = getattr(config, "external_folders", {})
        if alias in external_folders:
            del external_folders[alias]
            project_context.save_context_config(config)
            UI.success(f"Paquete externo con alias '{alias}' desvinculado.")
            UI.info("Escribe [bold yellow]update[/] para sincronizar con Google Drive.")
        else:
            UI.warn(
                f"No existe ningún paquete externo vinculado con el alias '{alias}'."
            )

    # CONTROL: RESET / CLEAR / TREE

    elif action in ("reset", "clear"):
        config.reset_to_default()
        project_context.save_context_config(config)
        UI.success(
            "Configuración de contexto restablecida por completo (raíz local '.' y sin filtros ni paquetes externos)."
        )
        UI.info("Escribe [bold yellow]update[/] para sincronizar con Google Drive.")

    elif action == "tree":
        context_data = project_context.generate_context()
        parts = context_data.text.split(
            "================================================\n"
        )
        tree_structure = parts[0].strip()

        UI.print(
            "\n[dim cyan]Estructura jerárquica activa del contexto (incluyendo externos):[/]",
            indent=False,
        )
        print(tree_structure)
        UI.print("", indent=False)

    else:
        UI.error(
            f"Acción '{action}' no reconocida. Opciones válidas:\n"
            "  • Enfoque: set, add, remove (rm)\n"
            "  • Filtros: exclude, unexclude\n"
            "  • Externos: link, unlink\n"
            "  • Control: status, tree, reset (clear)"
        )
