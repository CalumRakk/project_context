from project_context.commands.interactive.register import ParsedArgs, SessionContext
from project_context.core.exceptions import InvalidCommandArgumentError
from project_context.core.story_ops import apply_story_update
from project_context.ui import UI


def cmd_story(ctx: SessionContext, args: ParsedArgs):
    """Configura o procesa las intenciones del modo historia interactivo."""
    anchor_file_path = ctx.project_context.local_dir / "story_anchor.txt"

    if not args.args:
        if anchor_file_path.exists():
            current_anchor = anchor_file_path.read_text(encoding="utf-8").strip()
            UI.info(f"Modo historia ACTIVO. Ancla actual: [cyan]{current_anchor}[/]")
        UI.warn("Uso: story <archivo.md> o story exit")
        return

    target = args.get_arg(0)
    assert target is not None

    if target.lower() in ["exit", "quit", "off"]:
        if anchor_file_path.exists():
            anchor_file_path.unlink()
            UI.success("Has salido del modo historia.")
        else:
            UI.info("El modo historia no estaba activo.")
        return

    target_file = ctx.project_context.project_path / target
    if not target_file.exists():
        raise InvalidCommandArgumentError(
            f"El archivo '{target}' no existe en el proyecto."
        )

    rel_path = str(target_file.relative_to(ctx.project_context.project_path).as_posix())

    state = ctx.project_context.load_state()
    context_items = state.context_items
    has_specific_focus = bool(context_items.files or context_items.folders)

    if has_specific_focus:
        if rel_path not in context_items.files:
            context_items.files.append(rel_path)
            state.context_items = context_items
            state.save()
            UI.info(
                f"El archivo [cyan]{rel_path}[/] fue añadido al contexto específico."
            )

    UI.info("Iniciando Modo Historia...")
    anchor_file_path.write_text(rel_path, encoding="utf-8")

    apply_story_update(ctx.api, ctx.project_context, rel_path)
