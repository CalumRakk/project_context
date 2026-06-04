from pathlib import Path
from typing import Optional

import typer

from project_context.exceptions import ChatSessionError, InvalidCommandArgumentError
from project_context.git_ops import (
    has_unstaged_changes,
    stage_all_changes,
)
from project_context.ops import (
    apply_story_update,
    generate_commit_prompt_text,
    rebuild_project_context,
    resolve_image_paths,
    sync_images,
)
from project_context.schema import ChunksText
from project_context.ui.editor import run_editor_mode
from project_context.ui.registry import SessionContext, registry
from project_context.utils import (
    COMMIT_TASK_MARKER,
    IMAGE_INSERTION_PROMPT,
    IMAGE_INSERTION_RESPONSE,
    UI,
    console,
    get_potential_media_folders,
)


def prompt_for_media_folder(project_path: Path) -> Optional[Path]:
    typer.secho(
        "\n[?] Se detectaron referencias tipo WikiLink (Obsidian).",
        fg=typer.colors.CYAN,
    )
    candidates = get_potential_media_folders(project_path)

    typer.echo("¿En qué carpeta debería buscar los archivos adjuntos?")
    for i, folder in enumerate(candidates, 1):
        typer.echo(f" {i}) {folder.relative_to(project_path)}")
    typer.echo(" n) Escribir ruta manualmente")
    typer.echo(" s) Saltar estas imágenes")

    choice = input("Selección: ").strip().lower()

    if choice == "s":
        return None
    if choice == "n":
        manual = input("Ruta desde la raíz del proyecto: ").strip()
        return project_path / manual

    try:
        idx = int(choice) - 1
        if 0 <= idx < len(candidates):
            return candidates[idx]
    except ValueError:
        pass
    return None


@registry.register("edit", require_chat=True)
def cmd_edit(ctx: SessionContext, args: list[str]):
    """Abre el editor visual de bloques para depurar el prompt en Drive."""
    run_editor_mode(ctx.api, ctx.chat_id)


@registry.register("reset", require_chat=True)
def cmd_reset(ctx: SessionContext, args: list[str]):
    """Reconstruye por completo el chat y el contexto utilizando los mismos archivos."""
    confirm = console.input(
        "[bold red]¿Reconstruir chat y contexto por completo? (s/n): [/]"
    )
    if confirm.lower() == "s":
        new_state = rebuild_project_context(ctx.api, ctx.workspace, ctx.state)
        ctx.update_state(new_state)


def is_chat_remotely_in_commit_mode(ctx: SessionContext) -> bool:
    """Verifica si el chat en Google Drive ya se encuentra estructurado en modo commit."""
    chat_data = ctx.api.get_chat_ia_studio(ctx.chat_id)
    if not chat_data:
        return False
    for chunk in chat_data.chunkedPrompt.chunks:
        if isinstance(chunk, ChunksText) and COMMIT_TASK_MARKER in chunk.text:
            return True
    return False


@registry.register(
    "commit:done", "commit:restore", "commit:clear", "commit:rm", require_chat=True
)
def cmd_commit_restore(ctx: SessionContext, args: list[str]):
    """Restaura el chat original desactivando el modo de commit rápido."""
    UI.info("Buscando último snapshot de respaldo tipo 'stash'...")

    latest_stash = ctx.monitor.get_latest_snapshot_by_category("stash")

    if not latest_stash:
        snapshots = ctx.monitor.list_snapshots()
        if snapshots:
            latest_stash = snapshots[0]
            UI.warn(
                "No se encontró ningún snapshot tipo 'stash'. Se usará el último snapshot general."
            )
        else:
            raise ChatSessionError(
                "No se encontraron snapshots en el sistema para proceder."
            )

    UI.info(
        f"Punto de restauración seleccionado: [{latest_stash['timestamp']}] '{latest_stash.get('message') or '-'}'"
    )
    confirm = console.input("[bold red]¿Confirmas la restauración del chat? (s/n): [/]")
    if confirm.lower() != "s":
        UI.info("Operación cancelada.")
        return

    ctx.stop_monitor()
    try:
        if ctx.monitor.restore_snapshot(latest_stash["timestamp"]):
            ctx.state.commit_mode = False
            ctx.update_state(ctx.state)
            UI.success("¡Chat original restaurado con éxito!")
            UI.info("Ve a AI Studio y REFRESCA LA PÁGINA (F5).")
        else:
            raise ChatSessionError("La restauración del archivo en Drive falló.")
    finally:
        ctx.start_monitor()


@registry.register("commit:all", require_chat=True)
def cmd_commit_all(ctx: SessionContext, args: list[str]):
    """Añade todas las modificaciones al stage de git y genera la sugerencia."""
    UI.info("Añadiendo todos los cambios al stage (git add -A)...")
    stage_all_changes(ctx.project_path)
    return cmd_commit(ctx, [])


@registry.register("commit", require_chat=True)
def cmd_commit(ctx: SessionContext, args: list[str]):
    """Genera una sugerencia de commit con base en el diff de Git actual."""

    is_commit = is_chat_remotely_in_commit_mode(ctx)
    if args and not is_commit:
        sub = args[0].lower()
        if sub in ["clear", "done", "restore", "rm"]:
            return cmd_commit_restore(ctx, args[1:])

        elif sub in ["-a", "--all", "all"]:
            return cmd_commit_all(ctx, args[1:])

    UI.info("Obteniendo cambios de Git...")
    prompt_text = generate_commit_prompt_text(ctx.project_path)

    if not prompt_text:
        if has_unstaged_changes(ctx.project_path):
            UI.warn(
                "No hay archivos en stage (git add), pero hay modificaciones locales."
            )
            confirm = console.input(
                "[bold yellow]¿Quieres añadirlos todos al stage ahora? (s/n): [/]"
            )
            if confirm.lower() == "s":
                stage_all_changes(ctx.project_path)
                prompt_text = generate_commit_prompt_text(ctx.project_path)
                if not prompt_text:
                    raise ChatSessionError(
                        "No se pudo generar el diff de Git después del stage."
                    )
            else:
                UI.info("Operación cancelada.")
                return
        else:
            UI.warn("El repositorio está limpio. No hay cambios pendientes.")
            return

    # 1. Crear snapshot atómico tipo STASH
    UI.info("Generando snapshot de respaldo en base de datos local...")
    try:
        ctx.monitor.create_named_snapshot(
            message="Antes de entrar en modo commit", category="stash"
        )
    except Exception as e:
        raise ChatSessionError(f"No se pudo crear el snapshot del sistema: {e}")

    UI.info("Descargando configuración del chat actual...")
    chat_data = ctx.api.get_chat_ia_studio(ctx.chat_id)
    if not chat_data:
        raise ChatSessionError("No se pudo descargar el chat de Drive.")

    context_chunk = None
    for chunk in chat_data.chunkedPrompt.chunks:
        if getattr(chunk, "role", "") == "user" and hasattr(chunk, "driveDocument"):
            if chunk.file_id == ctx.file_id:
                context_chunk = chunk
                break

    UI.info("Configurando chat minimalista con modelo rápido...")
    fast_chunks = []
    if context_chunk:
        fast_chunks.append(context_chunk)

    fast_chunks.append(ChunksText(text=prompt_text, role="user"))

    chat_data.runSettings.model = "models/gemini-flash-lite-latest"
    chat_data.runSettings.sanitize()

    chat_data.chunkedPrompt.chunks = fast_chunks
    chat_data.chunkedPrompt.pendingInputs = []

    if ctx.api.update_chat_file(ctx.chat_id, chat_data):
        ctx.state.commit_mode = True
        ctx.update_state(ctx.state)
        UI.success("¡Modo commit activado!")
        UI.info("Ve a AI Studio, REFRESCA LA PÁGINA (F5) y presiona RUN.")
    else:
        raise ChatSessionError("Error al subir el chat de commit temporal a Drive.")


@registry.register("images", require_chat=True)
def cmd_images(ctx: SessionContext, args: list[str]):
    """Detecta, sincroniza e inyecta las imágenes referenciadas en un archivo markdown."""
    if not args:
        UI.warn("Uso: images <archivo.md>")
        return

    filename = args[0]
    try:
        found_paths, missing = resolve_image_paths(
            ctx.project_path, filename, ctx.session_media_root
        )
    except FileNotFoundError:
        raise InvalidCommandArgumentError(
            f"El archivo '{filename}' no existe en el proyecto."
        )

    if missing and not ctx.session_media_root:
        UI.warn(f"No se encontraron: {', '.join(missing[:3])}...")
        ctx.session_media_root = prompt_for_media_folder(ctx.project_path)

        if ctx.session_media_root:
            found_paths_2, missing_2 = resolve_image_paths(
                ctx.project_path, filename, ctx.session_media_root
            )
            found_paths = list(set(found_paths + found_paths_2))

    if not found_paths:
        raise InvalidCommandArgumentError(
            "No se pudo resolver ninguna ruta de imagen válida en el disco."
        )

    chunks_to_add = []
    chunks_to_add.append(
        ChunksText(
            text=IMAGE_INSERTION_PROMPT.format(filename=filename),
            role="user",
        )
    )

    image_chunks = sync_images(ctx.api, ctx.project_path, specific_files=found_paths)
    chunks_to_add.extend(image_chunks)

    chunks_to_add.append(
        ChunksText(
            text=IMAGE_INSERTION_RESPONSE.format(filename=filename),
            role="model",
        )
    )

    if ctx.api.append_chunks(ctx.chat_id, chunks_to_add):
        UI.success(f"¡{len(found_paths)} imágenes inyectadas!")
    else:
        raise ChatSessionError("Error al inyectar imágenes en el chat de Drive.")


@registry.register("story", require_chat=True)
def cmd_story(ctx: SessionContext, args: list[str]):
    """Configura o procesa las intenciones del modo historia interactivo."""
    if not args:
        if ctx.state.story_mode:
            UI.info(
                f"Modo historia ACTIVO. Ancla actual: [cyan]{ctx.state.story_anchor}[/]"
            )
        UI.warn("Uso: story <archivo.md> o story exit")
        return

    target = args[0]

    if target.lower() in ["exit", "quit", "off"]:
        ctx.state.story_mode = False
        ctx.state.story_anchor = None
        ctx.update_state(ctx.state)
        UI.success("Has salido del modo historia.")
        return

    target_file = ctx.project_path / target
    if not target_file.exists():
        raise InvalidCommandArgumentError(
            f"El archivo '{target}' no existe en el proyecto."
        )

    rel_path = str(target_file.relative_to(ctx.project_path).as_posix())
    context_items = ctx.context_items
    has_specific_focus = bool(context_items.files or context_items.folders)

    if has_specific_focus:
        if rel_path not in context_items.files:
            context_items.files.append(rel_path)
            ctx.state.context_items = context_items
            ctx.update_state(ctx.state)
            UI.info(
                f"El archivo [cyan]{rel_path}[/] fue añadido al contexto específico."
            )

    UI.info("Iniciando Modo Historia...")
    ctx.state.story_mode = True
    ctx.state.story_anchor = rel_path
    ctx.update_state(ctx.state)

    new_state = apply_story_update(
        ctx.api, ctx.workspace, ctx.state, media_root_hint=ctx.session_media_root
    )
    ctx.update_state(new_state)


@registry.register("transfer", require_chat=True)
def cmd_transfer(ctx: SessionContext, args: list[str]):
    """Migra el chat y sus dependencias de forma segura a otra cuenta de Google."""
    if not args:
        UI.warn("Uso: transfer <perfil_destino>")
        return

    target_profile = args[0]
    current_profile = profile_manager.get_active_profile_name()

    if target_profile == current_profile:
        raise InvalidCommandArgumentError(
            "El perfil destino no puede ser el mismo que el actual."
        )

    if target_profile not in profile_manager.list_profiles():
        raise InvalidCommandArgumentError(
            f"El perfil '{target_profile}' no existe.\n"
            f"Perfiles disponibles: {', '.join(profile_manager.list_profiles())}"
        )

    confirm = console.input(
        f"[bold red]¿Migrar sesión de '{current_profile}' hacia '{target_profile}'? (s/n): [/]"
    )
    if confirm.lower() != "s":
        UI.info("Transferencia cancelada.")
        return

    try:
        from project_context.ops import transfer_chat_to_profile

        new_api, new_state = transfer_chat_to_profile(
            ctx.api, ctx.state, ctx.project_path, target_profile
        )

        ctx.api = new_api
        ctx.state = new_state

        from project_context.history import SnapshotManager

        ctx.monitor = SnapshotManager(new_api, ctx.project_path, new_state)
        ctx.update_state(new_state)

        UI.success(
            f"¡Migración completada! Ahora estás operando nativamente como [bold]{target_profile}[]."
        )
        UI.warn(
            "RECUERDA: Ve a Google AI Studio, cambia a la cuenta correspondiente y abre el nuevo chat."
        )

    except Exception as e:
        profile_manager.set_active_profile(current_profile)  # type: ignore
        raise ChatSessionError(f"Error crítico durante la transferencia: {e}")


@registry.register("fix", require_chat=True)
def cmd_fix(ctx: SessionContext, args: list[str]):
    """Analiza y repara inconsistencias en el esquema del chat de Drive."""
    UI.info("Analizando chat...")
    fixed_count = ctx.api.repair_chat_structure(ctx.chat_id)

    if fixed_count > 0:
        UI.success(f"¡Estructura saneada! {fixed_count} bloques corregidos.")
    else:
        UI.success("El chat está saludable.")
