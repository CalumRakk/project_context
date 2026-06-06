from project_context.commands.interactive.register import SessionContext
from project_context.ops import create_or_update_chat, restore_chat_backup_if_exists
from project_context.ui import UI
from project_context.utils import get_context_tree


def cmd_clear(ctx: SessionContext, args: list[str]):
    """Limpia el historial de la conversación manteniendo el contexto inicial."""

    if restore_chat_backup_if_exists(ctx.api, ctx.workspace):
        UI.info("Restaurado chat original desde el respaldo local.")
    else:
        ctx.api.clear_chat(ctx.workspace.chat_id)
        UI.success("Historial de mensajes limpiado en Drive.")


def cmd_update(ctx: SessionContext, args: list[str]):
    """Actualiza el contenido del archivo de contexto en Drive."""

    clean_args_list = [
        arg for arg in args if arg not in ["--force", "-f", "force", "--run", "-r"]
    ]

    create_or_update_chat(ctx.api, ctx.workspace)

    has_focus = bool(
        ctx.workspace.context_items.files or ctx.workspace.context_items.folders
    )
    if "tree" in clean_args_list or has_focus:
        UI.info("Árbol de archivos enviado:")
        tree_str = get_context_tree(ctx.project_path, ctx.workspace.context_items)
        print(f"\n[dim cyan]{tree_str}[/]\n")
