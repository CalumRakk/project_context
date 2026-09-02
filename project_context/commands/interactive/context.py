import logging
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.table import Table

from project_context.commands.interactive.register import ParsedArgs, SessionContext
from project_context.ui import UI

console_rich = Console()
logger = logging.getLogger("project_context.context_cmd")


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

        # Área de trabajo / Foco
        if config.folders == ["."] and not config.files:
            table.add_row("Modo de Trabajo", "[dim cyan]Proyecto Completo (.)[/]")
        else:
            if config.folders == ["."]:
                table.add_row("Base de Trabajo", "[dim cyan]Proyecto Completo (.)[/]")
            else:
                for folder in config.folders:
                    table.add_row("Foco de Carpeta", folder)

        # Archivos Forzados / Excepciones (Whitelist con diagnóstico de anulación)
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
                    table.add_row("Archivos en Foco", file_path)

        # Paquetes externos (link)
        external_folders = getattr(config, "external_folders", {})
        if external_folders:
            for alias, ext_path in external_folders.items():
                table.add_row(f"Paquete Externo ({alias})", ext_path)

        # Exclusiones activas / Poda de ruido (exclude)
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

    # ENFOQUE BASE: SET

    elif action == "set":
        target_raw = args.get_arg(1)
        if not target_raw:
            UI.error("Uso: context set <ruta_carpeta_o_archivo | .>")
            return

        if target_raw.strip() == ".":
            config.folders = ["."]
            config.files = []
            project_context.save_context_config(config)
            UI.success("Área de trabajo redefinida a todo el proyecto (raíz '.').")
            UI.info("Escribe [bold yellow]update[/] para sincronizar con Google Drive.")
            logger.info(
                f"CONTEXT_CONFIG_UPDATED: folders={config.folders}, files={config.files}, exclusions={config.exclusions}"
            )
            return

        rel_str = _get_relative_path(target_raw, project_path)
        if rel_str is None:
            UI.error(
                f"La ruta '{target_raw}' debe estar ubicada dentro del proyecto '{project_path}'."
            )
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

            override = project_context.check_file_override(rel_str)
            if override == "exclude":
                UI.info("Nota: Este archivo anula una regla de exclusión activa.")
            elif override == ".gitignore":
                UI.info("Nota: Este archivo anula una regla de .gitignore.")

        project_context.save_context_config(config)
        UI.info(
            "Escribe [bold yellow]update[/] para sincronizar este foco con Google Drive."
        )

    # RESCATE Y SUMA SEGURA: ADD

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
            UI.error(
                f"La ruta '{target_raw}' debe estar ubicada dentro del proyecto '{project_path}'."
            )
            return

        full_path = project_path / rel_str
        if not full_path.exists():
            UI.error(f"La ruta '{rel_str}' no existe físicamente en el repositorio.")
            return

        # ESCENARIO A: Contexto general activo (folders == ["."])
        if config.folders == ["."]:
            if full_path.is_file():
                override = project_context.check_file_override(rel_str)
                if override:
                    if rel_str not in config.files:
                        config.files.append(rel_str)
                        UI.success(
                            f"Archivo '{rel_str}' añadido como excepción forzada (anula {override})."
                        )
                        project_context.save_context_config(config)
                        UI.info("Escribe [bold yellow]update[/] para sincronizar.")
                    else:
                        UI.info(
                            f"El archivo '{rel_str}' ya está registrado en las excepciones forzadas."
                        )
                else:
                    if rel_str in config.files:
                        UI.info(
                            f"El archivo '{rel_str}' ya está registrado como excepción."
                        )
                    else:
                        UI.info(
                            f"El archivo '{rel_str}' ya forma parte del contexto por defecto del proyecto."
                        )
                        UI.tip(
                            "No necesitas añadirlo manualmente a menos que esté en .gitignore o en exclusions."
                        )
            else:
                # Es una carpeta
                UI.info(
                    f"La carpeta '{rel_str}' ya forma parte del contexto por defecto del proyecto."
                )
                UI.tip(
                    "Si deseas enfocarte únicamente en esta carpeta, usa:",
                    commands=[f"context set {rel_str}"],
                )
            return

        # ESCENARIO B: Foco restringido activo (folders != ["."])
        if full_path.is_dir():
            if rel_str not in config.folders:
                config.folders.append(rel_str)
                UI.success(f"Carpeta '{rel_str}' sumada al foco de trabajo.")
                project_context.save_context_config(config)
                UI.info("Escribe [bold yellow]update[/] para sincronizar.")
            else:
                UI.info(f"La carpeta '{rel_str}' ya está en el foco de trabajo.")
        else:
            if rel_str not in config.files:
                config.files.append(rel_str)
                override = project_context.check_file_override(rel_str)
                if override:
                    UI.success(
                        f"Archivo '{rel_str}' sumado al foco como excepción (anula {override})."
                    )
                else:
                    UI.success(f"Archivo '{rel_str}' sumado al foco de trabajo.")
                project_context.save_context_config(config)
                UI.info("Escribe [bold yellow]update[/] para sincronizar.")
            else:
                UI.info(f"El archivo '{rel_str}' ya está registrado en el foco.")

    # PODA Y FILTROS: EXCLUDE

    elif action == "exclude":
        target_raw = args.get_arg(1)
        if not target_raw:
            UI.error("Uso: context exclude <ruta_o_patrón_glob>")
            return

        # Detectar si es una ruta física del proyecto o un patrón glob
        rel_str = _get_relative_path(target_raw, project_path)
        pattern = target_raw.strip()

        if rel_str is not None:
            full_path = project_path / rel_str
            if full_path.exists():
                if full_path.is_dir():
                    pattern = f"{rel_str}/**"
                else:
                    pattern = rel_str

        if pattern in config.exclusions:
            UI.info(f"El patrón o ruta '{pattern}' ya se encuentra excluido.")
            return

        config.exclusions.append(pattern)

        # Si estaba en files o folders, limpiarlo para mantener coherencia
        if rel_str and rel_str in config.files:
            config.files.remove(rel_str)
            UI.info(f"Se removió '{rel_str}' de las excepciones forzadas.")

        if rel_str and rel_str in config.folders:
            config.folders.remove(rel_str)
            if not config.folders and not config.files:
                config.folders = ["."]
            UI.info(f"Se removió '{rel_str}' del foco de carpetas.")

        project_context.save_context_config(config)
        UI.success(f"Exclusión '{pattern}' aplicada con éxito.")
        UI.info("Escribe [bold yellow]update[/] para sincronizar con Google Drive.")

    # DESHACER INTELIGENTE: REMOVE / RM

    elif action in ("remove", "rm"):
        target_raw = args.get_arg(1)
        if not target_raw:
            UI.error("Uso: context remove <ruta_archivo | ruta_carpeta | patrón>")
            return

        rel_str = _get_relative_path(target_raw, project_path)
        target_clean = target_raw.strip()
        removed = False

        # Comprobar si está en exclusions (exacto o variante de carpeta)
        matching_exc = None
        for exc in list(config.exclusions):
            if exc == target_clean or (
                rel_str and exc in (rel_str, f"{rel_str}/**", f"{rel_str}/*")
            ):
                matching_exc = exc
                break

        if matching_exc:
            config.exclusions.remove(matching_exc)
            removed = True
            UI.success(f"Exclusión '{matching_exc}' removida del filtro.")

        # Comprobar si está en files
        if rel_str and rel_str in config.files:
            config.files.remove(rel_str)
            removed = True
            UI.success(f"Archivo '{rel_str}' removido de las excepciones.")

        # Comprobar si está en folders
        if rel_str and rel_str in config.folders:
            config.folders.remove(rel_str)
            removed = True
            UI.success(f"Carpeta '{rel_str}' removida del foco.")
            if not config.folders and not config.files:
                config.folders = ["."]
                UI.info(
                    "No quedan focos locales activos. Reestablecido al proyecto completo (raíz '.')."
                )

        # Comprobar si es un paquete externo
        external_folders = getattr(config, "external_folders", {})
        if target_clean in external_folders:
            UI.warn(
                f"'{target_clean}' es un paquete externo vinculado. Para desconectarlo usa: [bold yellow]context unlink {target_clean}[/]"
            )
            return

        if not removed:
            UI.warn(
                f"No se encontró '{target_raw}' en las exclusiones, excepciones ni foco de trabajo."
            )
            return

        project_context.save_context_config(config)
        UI.info("Escribe [bold yellow]update[/] para sincronizar con Google Drive.")

    # DES-EXCLUIR EXPLÍCITO: UNEXCLUDE

    elif action == "unexclude":
        pattern = args.get_arg(1)
        if not pattern:
            UI.error("Uso: context unexclude <patrón_glob>")
            return

        rel_str = _get_relative_path(pattern, project_path)
        matching_exc = None
        for exc in list(config.exclusions):
            if exc == pattern or (
                rel_str and exc in (rel_str, f"{rel_str}/**", f"{rel_str}/*")
            ):
                matching_exc = exc
                break

        if matching_exc:
            config.exclusions.remove(matching_exc)
            project_context.save_context_config(config)
            UI.success(f"Patrón de exclusión '{matching_exc}' removido.")
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

    # RESTABLECER Y VISTA PREVIA: RESET / TREE

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
