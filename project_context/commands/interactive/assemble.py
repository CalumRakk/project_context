from project_context.commands.interactive.register import ParsedArgs, SessionContext
from project_context.core.story_ops import assemble_chat_to_markdown
from project_context.ui import UI


def cmd_assemble(ctx: SessionContext, args: ParsedArgs):
    """
    (Experimental) Ensambla los turnos del chat activo en un archivo Markdown local,
    descargando las imágenes vinculadas a la carpeta images/.
    """
    target = args.get_arg(0)
    if not target:
        UI.warn("Uso: assemble <ruta/archivo.md> (ej: assemble capitulo_1.md)")
        return

    if not target.endswith(".md"):
        target = f"{target}.md"

    try:
        saved_path = assemble_chat_to_markdown(ctx.api, ctx.project_context, target)
        rel_saved = saved_path.relative_to(ctx.project_context.project_path)
        UI.success(f"Conversación ensamblada con éxito en: [bold green]{rel_saved}[/]")
        UI.tip(
            "Recuerda que puedes ejecutar 'update' para sincronizar esta nueva historia al contexto.",
            commands=["update"],
        )
    except Exception as e:
        UI.error(f"Fallo al ensamblar la conversación: {e}")
