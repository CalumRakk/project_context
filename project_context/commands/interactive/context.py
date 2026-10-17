import logging
from pathlib import Path

from rich.console import Console
from rich.table import Table

from project_context.commands.interactive.register import ParsedArgs, SessionContext
from project_context.ui import UI

console_rich = Console()
logger = logging.getLogger("project_context.context_cmd")


def cmd_context(ctx: SessionContext, args: ParsedArgs):
    """Manejador interactivo para la gestión integral del contexto."""
    project_context = ctx.project_context
    _ = project_context.project_path
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

        # Archivos Forzados / Excepciones (Whitelist)
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

        # Alias guardados (Catálogo de presets)
        if config.exclusion_aliases:
            aliases_summary = ", ".join(
                f"[cyan]{name}[/] [dim]({len(pats)} reglas)[/]"
                for name, pats in sorted(config.exclusion_aliases.items())
            )
            table.add_row("Alias Guardados", aliases_summary)
        else:
            table.add_row("Alias Guardados", "[dim]Ninguno[/]")

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
            return

        resolved = project_context.resolve_context_path(target_raw)
        if resolved is None or not resolved.exists:
            UI.error(f"La ruta '{target_raw}' no existe o no pertenece al proyecto.")
            return

        if resolved.is_external:
            UI.error(
                f"'{resolved.virtual_path}' pertenece a un paquete externo vinculado ({resolved.external_alias}).\n"
                "El comando 'set' define el área local. Para incorporar código externo usa 'context add'."
            )
            return

        if resolved.was_auto_translated:
            UI.info(f"Ruta física resuelta como: [bold cyan]{resolved.virtual_path}[/]")

        if resolved.is_dir:
            config.folders = [resolved.virtual_path]
            config.files = []
            UI.success(
                f"Área de trabajo fijada exclusivamente en la carpeta '{resolved.virtual_path}'."
            )
        else:
            config.folders = []
            config.files = [resolved.virtual_path]
            UI.success(
                f"Foco fijado exclusivamente en el archivo '{resolved.virtual_path}'."
            )

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

        resolved = project_context.resolve_context_path(target_raw)
        if resolved is None or not resolved.exists:
            UI.error(
                f"La ruta '{target_raw}' no existe en el proyecto ni en paquetes externos vinculados."
            )
            return

        if resolved.was_auto_translated:
            UI.info(f"Ruta resuelta como: [bold cyan]{resolved.virtual_path}[/]")

        # Caso: Archivo individual (Local o de Paquete Externo)
        if resolved.is_file:
            if resolved.virtual_path not in config.files:
                config.files.append(resolved.virtual_path)
                override = project_context.check_file_override(resolved.virtual_path)
                if override:
                    UI.success(
                        f"Archivo '{resolved.virtual_path}' sumado como excepción (anula {override})."
                    )
                else:
                    UI.success(
                        f"Archivo '{resolved.virtual_path}' sumado al foco de trabajo."
                    )
                project_context.save_context_config(config)
                UI.info("Escribe [bold yellow]update[/] para sincronizar.")
            else:
                UI.info(
                    f"El archivo '{resolved.virtual_path}' ya está registrado en el foco."
                )
            return

        # Caso: Carpeta
        if resolved.is_dir:
            if resolved.is_external:
                UI.info(
                    f"El paquete externo '{resolved.external_alias}' ya está vinculado globalmente.\n"
                    f"Para sumar archivos específicos, usa: [bold yellow]context add {resolved.virtual_path}/archivo.py[/]"
                )
                return

            if config.folders == ["."]:
                UI.info(
                    f"La carpeta '{resolved.virtual_path}' ya forma parte del contexto por defecto."
                )
                UI.tip(
                    "Si deseas enfocarte únicamente en esta carpeta, usa:",
                    commands=[f"context set {resolved.virtual_path}"],
                )
            else:
                if resolved.virtual_path not in config.folders:
                    config.folders.append(resolved.virtual_path)
                    UI.success(
                        f"Carpeta '{resolved.virtual_path}' sumada al foco de trabajo."
                    )
                    project_context.save_context_config(config)
                    UI.info("Escribe [bold yellow]update[/] para sincronizar.")
                else:
                    UI.info(
                        f"La carpeta '{resolved.virtual_path}' ya está en el foco de trabajo."
                    )

    # PODA Y FILTROS: EXCLUDE
    elif action == "exclude":
        target_raw = args.get_arg(1)
        if not target_raw:
            UI.error("Uso: context exclude <ruta_o_patrón_glob>")
            return

        resolved = project_context.resolve_context_path(target_raw)
        if resolved is None:
            UI.error(
                f"La ruta '{target_raw}' no es válida dentro del proyecto ni paquetes vinculados."
            )
            return

        if resolved.was_auto_translated:
            UI.info(f"Ruta física resuelta como: [bold cyan]{resolved.virtual_path}[/]")

        if resolved.is_dir:
            pattern = f"{resolved.virtual_path}/**"
        else:
            pattern = resolved.virtual_path

        if pattern in config.exclusions:
            UI.info(f"El patrón o ruta '{pattern}' ya se encuentra excluido.")
            return

        config.exclusions.append(pattern)

        # Si estaba en files o folders, limpiarlo para mantener coherencia
        if resolved.virtual_path in config.files:
            config.files.remove(resolved.virtual_path)
            UI.info(
                f"Se removió '{resolved.virtual_path}' de las excepciones forzadas."
            )

        if resolved.virtual_path in config.folders:
            config.folders.remove(resolved.virtual_path)
            if not config.folders and not config.files:
                config.folders = ["."]
            UI.info(f"Se removió '{resolved.virtual_path}' del foco de carpetas.")

        project_context.save_context_config(config)
        UI.success(f"Exclusión '{pattern}' aplicada con éxito.")
        UI.info("Escribe [bold yellow]update[/] para sincronizar con Google Drive.")

    # DESHACER INTELIGENTE: REMOVE / RM
    elif action in ("remove", "rm"):
        target_raw = args.get_arg(1)
        if not target_raw:
            UI.error("Uso: context remove <ruta_archivo | ruta_carpeta | patrón>")
            return

        resolved = project_context.resolve_context_path(target_raw)
        v_path = resolved.virtual_path if resolved else target_raw.strip()
        removed = False

        # Comprobar si está en exclusions
        matching_exc = None
        for exc in list(config.exclusions):
            if exc == v_path or exc in (v_path, f"{v_path}/**", f"{v_path}/*"):
                matching_exc = exc
                break

        if matching_exc:
            config.exclusions.remove(matching_exc)
            removed = True
            UI.success(f"Exclusión '{matching_exc}' removida del filtro.")

        # Comprobar si está en files
        if v_path in config.files:
            config.files.remove(v_path)
            removed = True
            UI.success(f"Archivo '{v_path}' removido de las excepciones.")

        # Comprobar si está en folders
        if v_path in config.folders:
            config.folders.remove(v_path)
            removed = True
            UI.success(f"Carpeta '{v_path}' removida del foco.")
            if not config.folders and not config.files:
                config.folders = ["."]
                UI.info("Reestablecido al proyecto completo (raíz '.').")

        # Comprobar si es un paquete externo
        external_folders = getattr(config, "external_folders", {})
        if v_path in external_folders:
            UI.warn(
                f"'{v_path}' es un paquete externo vinculado. Para desconectarlo usa: [bold yellow]context unlink {v_path}[/]"
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

        resolved = project_context.resolve_context_path(pattern)
        target_pattern = resolved.virtual_path if resolved else pattern.strip()

        matching_exc = None
        for exc in list(config.exclusions):
            if exc == target_pattern or exc in (
                target_pattern,
                f"{target_pattern}/**",
                f"{target_pattern}/*",
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
    # CATÁLOGO DE EXCLUSIONES: ALIAS

    elif action == "alias":
        subaction = args.get_arg(1)
        if not subaction:
            subaction = "list"

        subaction = subaction.lower()

        # --- GUARDAR ALIAS ---
        if subaction == "save":
            alias_name = args.get_arg(2)
            if not alias_name:
                UI.error("Uso: context alias save <nombre_alias> [--merge]")
                return

            alias_name = alias_name.strip()
            if not config.exclusions:
                UI.warn(
                    "No hay exclusiones activas actualmente para guardar en un alias."
                )
                UI.tip(
                    "Usa 'context exclude <ruta_o_patron>' para definir exclusiones primero."
                )
                return

            is_merge = args.has_flag("--merge") or any(
                a.lower() in ("--merge", "-m") for a in args.args
            )
            already_exists = alias_name in config.exclusion_aliases

            if already_exists and is_merge:
                # Fusión: sumamos las exclusiones activas al alias existente sin duplicados
                existing_patterns = config.exclusion_aliases[alias_name]
                existing_set = set(existing_patterns)
                added = 0
                for pat in config.exclusions:
                    if pat not in existing_set:
                        existing_patterns.append(pat)
                        existing_set.add(pat)
                        added += 1

                project_context.save_context_config(config)
                UI.success(
                    f"Alias '{alias_name}' fusionado ({added} nuevos patrones añadidos al alias. Total: {len(existing_patterns)})."
                )
            else:
                config.exclusion_aliases[alias_name] = list(config.exclusions)
                project_context.save_context_config(config)

                count = len(config.exclusions)
                if already_exists:
                    UI.success(
                        f"Alias '{alias_name}' actualizado y sobrescrito con éxito ({count} patrones)."
                    )
                    UI.tip(
                        "Para fusionar con los patrones anteriores sin sobrescribir, usa:",
                        commands=[f"context alias save {alias_name} --merge"],
                    )
                else:
                    UI.success(
                        f"Alias '{alias_name}' registrado exitosamente ({count} patrones)."
                    )

        # --- APLICAR ALIAS ---
        elif subaction in ("apply", "load", "use"):
            alias_name = args.get_arg(2)
            if not alias_name:
                UI.error("Uso: context alias apply <nombre_alias>")
                if config.exclusion_aliases:
                    avail = ", ".join(sorted(config.exclusion_aliases.keys()))
                    UI.info(f"Alias disponibles: [cyan]{avail}[/]")
                return

            alias_name = alias_name.strip()
            if alias_name not in config.exclusion_aliases:
                UI.error(f"El alias '{alias_name}' no existe en el catálogo.")
                if config.exclusion_aliases:
                    avail = ", ".join(sorted(config.exclusion_aliases.keys()))
                    UI.info(f"Alias disponibles: [cyan]{avail}[/]")
                else:
                    UI.tip(
                        "Guarda tu primer alias usando:",
                        commands=["context alias save <nombre>"],
                    )
                return

            # Si el usuario intenta usar --merge en apply, le advertimos amablemente
            if args.has_flag("--merge") or any(
                a.lower() in ("--merge", "-m") for a in args.args
            ):
                UI.warn(
                    "La bandera '--merge' solo aplica al guardar: `context alias save <nombre> --merge`.\n"
                    "  Al aplicar un alias se reemplazan las exclusiones activas por defecto."
                )

            alias_patterns = config.exclusion_aliases[alias_name]
            config.exclusions = list(alias_patterns)
            project_context.save_context_config(config)

            UI.success(
                f"Alias '{alias_name}' aplicado ({len(alias_patterns)} patrones establecidos como exclusiones activas)."
            )
            UI.info(
                "Escribe [bold yellow]update[/] para sincronizar este contexto con Google Drive."
            )

        # --- LISTAR ALIAS ---
        elif subaction in ("list", "ls"):
            if not config.exclusion_aliases:
                UI.info("No hay ningún alias de exclusión guardado actualmente.")
                UI.tip(
                    "Puedes guardar las exclusiones activas en un alias con:",
                    commands=["context alias save <nombre>"],
                )
                return

            table = Table(
                title="[bold cyan]Catálogo de Alias de Exclusiones[/]",
                show_header=True,
                header_style="bold magenta",
                box=None,
            )
            table.add_column("Alias", style="bold green", width=22)
            table.add_column("Reglas", style="dim cyan", justify="right", width=8)
            table.add_column("Patrones asociados", style="white")

            for name, patterns in sorted(config.exclusion_aliases.items()):
                patterns_str = ", ".join(patterns) if patterns else "[dim](vacío)[/]"
                table.add_row(name, str(len(patterns)), patterns_str)

            UI.print("", indent=False)
            console_rich.print(table)
            UI.print("", indent=False)
            UI.tip(
                "Para aplicar un alias ejecuta:",
                commands=[
                    f"context alias apply {next(iter(sorted(config.exclusion_aliases)))}"
                ],
            )

        # --- ELIMINAR ALIAS ---
        elif subaction in ("remove", "rm", "delete"):
            alias_name = args.get_arg(2)
            if not alias_name:
                UI.error("Uso: context alias remove <nombre_alias>")
                return

            alias_name = alias_name.strip()
            if alias_name in config.exclusion_aliases:
                del config.exclusion_aliases[alias_name]
                project_context.save_context_config(config)
                UI.success(f"Alias '{alias_name}' eliminado del catálogo.")
            else:
                UI.warn(f"El alias '{alias_name}' no existe en el catálogo.")

        else:
            UI.error(
                f"Subacción '{subaction}' no reconocida.\n"
                "Opciones válidas: save, apply, list (ls), remove (rm)."
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
            "  • Alias: alias (save, apply, list, remove)\n"
            "  • Externos: link, unlink\n"
            "  • Control: status, tree, reset (clear)"
        )
