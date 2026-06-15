from pathlib import Path

from rich.console import Console
from rich.table import Table

from project_context.commands.interactive.register import ParsedArgs, SessionContext
from project_context.core.schemas import ContextConfig
from project_context.ui import UI

console_rich = Console()


def _get_relative_path(path_str: str, project_path: Path) -> str:
    """Resuelve la entrada del usuario a una ruta relativa válida dentro del proyecto."""
    target = Path(path_str)
    if target.is_absolute():
        try:
            return target.relative_to(project_path).as_posix()
        except ValueError:
            UI.error(
                f"La ruta '{path_str}' debe estar ubicada dentro del proyecto '{project_path}'."
            )
            return ""
    else:
        abs_target = (Path.cwd() / target).resolve()
        try:
            return abs_target.relative_to(project_path.resolve()).as_posix()
        except ValueError:
            UI.error(
                f"La ruta '{path_str}' debe estar ubicada dentro del proyecto '{project_path}'."
            )
            return ""


def cmd_context(ctx: SessionContext, args: ParsedArgs):
    """Manejador interactivo para la gestión de inclusiones y exclusiones del contexto."""
    project_context = ctx.project_context
    project_path = project_context.project_path

    config = project_context.load_context_config()

    action = args.get_arg(0)
    if not action:
        action = "status"

    action = action.lower()

    if action in ("status"):
        table = Table(
            title="[bold cyan]Configuración de Contexto Activo[/]",
            show_header=True,
            header_style="bold magenta",
            box=None,
        )
        table.add_column("Categoría", style="bold green", width=25)
        table.add_column("Ruta / Patrón / Destino", style="white")

        if config.folders == ["."]:
            table.add_row(
                "Inclusión Local Global", "[dim cyan]. (Proyecto Completo)[/]"
            )
        else:
            for folder in config.folders:
                table.add_row("Carpeta Enfocada (Local)", folder)
            for file in config.files:
                table.add_row("Archivo Enfocado (Local)", file)

        # Listar paquetes externos mapeados
        external_folders = getattr(config, "external_folders", {})
        if external_folders:
            for alias, ext_path in external_folders.items():
                table.add_row(f"Paquete Externo ({alias})", ext_path)

        if not config.exclusions:
            table.add_row("Exclusiones", "[dim]Ninguna (solo aplica .gitignore)[/]")
        else:
            for exc in config.exclusions:
                table.add_row("Exclusión", exc)

        UI.print("", indent=False)
        console_rich.print(table)
        UI.print("", indent=False)
        return

    elif action == "add":
        target_raw = args.get_arg(1)
        if not target_raw:
            UI.error("Uso: context add <ruta_archivo_o_carpeta>")
            return

        rel_str = _get_relative_path(target_raw, project_path)
        if not rel_str:
            return

        if rel_str == ".":
            config.folders = ["."]
            config.files = []
            UI.success("Restablecido contexto completo del proyecto (raíz '.').")
            project_context.save_context_config(config)
            return

        full_path = project_path / rel_str
        if not full_path.exists():
            UI.error(f"La ruta '{rel_str}' no existe físicamente en el repositorio.")
            return

        # Aplicar Regla de Transición: Eliminar '.' si existía
        if config.folders == ["."]:
            config.folders = []

        if full_path.is_dir():
            if rel_str not in config.folders:
                config.folders.append(rel_str)
                UI.success(f"Carpeta '{rel_str}' añadida al contexto interactivo.")
            else:
                UI.info(f"La carpeta '{rel_str}' ya está registrada.")
        else:
            if rel_str not in config.files:
                config.files.append(rel_str)
                UI.success(f"Archivo '{rel_str}' añadido al contexto interactivo.")
            else:
                UI.info(f"El archivo '{rel_str}' ya está registrado.")

        project_context.save_context_config(config)
        UI.info(
            "Escribe [bold yellow]update[/] para sincronizar este nuevo foco con Google Drive."
        )

    elif action == "remove":
        target_raw = args.get_arg(1)
        if not target_raw:
            UI.error("Uso: context remove <ruta_local_o_alias_externo_o_exclusion>")
            return

        removed = False
        external_folders = getattr(config, "external_folders", {})

        # 1. Comprobar si coincide con el alias de un paquete externo
        if target_raw in external_folders:
            del external_folders[target_raw]
            removed = True
            UI.success(f"Paquete externo con alias '{target_raw}' removido.")
        # 2. Comprobar si coincide con un patrón de exclusión
        elif target_raw in config.exclusions:
            config.exclusions.remove(target_raw)
            removed = True
            UI.success(f"Patrón de exclusión '{target_raw}' removido.")
        # 3. Comprobar si coincide con un elemento del foco local
        else:
            rel_str = _get_relative_path(target_raw, project_path)
            if rel_str:
                if rel_str in config.folders:
                    config.folders.remove(rel_str)
                    removed = True
                    UI.success(f"Carpeta local '{rel_str}' removida.")
                elif rel_str in config.files:
                    config.files.remove(rel_str)
                    removed = True
                    UI.success(f"Archivo local '{rel_str}' removido.")

        if not removed:
            UI.warn(
                f"No se encontró ningún elemento local, paquete externo o exclusión que coincida con '{target_raw}'."
            )
            return

        # Aplicar Regla de Transición: restablecer '.' en focos locales si queda vacío
        if not config.folders and not config.files:
            config.folders = ["."]
            UI.info(
                "No quedan inclusiones locales activas. Reestablecido al proyecto completo (raíz '.')."
            )

        project_context.save_context_config(config)
        UI.info(
            "Escribe [bold yellow]update[/] para sincronizar los cambios en Google Drive."
        )

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
            UI.warn(
                f"El patrón '{pattern}' no se encuentra registrado en las exclusiones."
            )

    elif action == "include":
        alias = args.get_arg(1)
        target_raw = args.get_arg(2)

        if not alias or not target_raw:
            UI.error("Uso: context include <alias> <ruta_carpeta_externa>")
            return

        # Resolver la ruta absoluta física de la carpeta externa
        target_path = Path(target_raw)
        if not target_path.is_absolute():
            target_path = (Path.cwd() / target_path).resolve()

        if not target_path.exists() or not target_path.is_dir():
            UI.error(f"La ruta '{target_raw}' no existe o no es un directorio válido.")
            return

        try:
            # Ejecutar el conjunto de validaciones lógicas
            project_context.validate_external_folder(alias, target_path)
        except ValueError as e:
            UI.error(str(e))
            return

        # Registrar el paquete externo en el diccionario
        if not hasattr(config, "external_folders"):
            config.external_folders = {}
        config.external_folders[alias] = target_path.as_posix()

        project_context.save_context_config(config)
        UI.success(
            f"Paquete externo '{alias}' mapeado con éxito desde '{target_path.as_posix()}'."
        )
        UI.info(
            "Escribe [bold yellow]update[/] para sincronizar este paquete externo con Google Drive."
        )

    elif action == "clear":
        config = ContextConfig(
            folders=["."], files=[], exclusions=[], external_folders={}
        )
        project_context.save_context_config(config)
        UI.success(
            "Configuración de contexto restablecida por completo (raíz local '.' y sin paquetes externos)."
        )

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
            f"Acción '{action}' no reconocida. Opciones válidas: add, remove, exclude, unexclude, include, clear, status, tree"
        )
