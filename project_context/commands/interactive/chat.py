from project_context.commands.interactive.register import SessionContext
from project_context.ops import create_or_update_chat, restore_chat_backup_if_exists
from project_context.ui import UI
from project_context.utils import get_context_tree


def cmd_clear(ctx: SessionContext, args: list[str]):
    """Limpia el historial de la conversación manteniendo el contexto inicial."""

    state = ctx.project_context.load_state()
    if state.chat_id is None:
        UI.error("No se encontró una sesión de chat activa para limpiar el historial.")
        return

    if restore_chat_backup_if_exists(ctx.api, ctx.project_context):
        UI.info("Restaurado chat original desde el respaldo local.")
    else:
        ctx.api.clear_chat(state.chat_id)
        UI.success("Historial de mensajes limpiado en Drive.")


def cmd_update(ctx: SessionContext, args: list[str]):
    """Actualiza el contenido del archivo de contexto en Drive."""

    create_or_update_chat(ctx.api, ctx.project_context)

    state = ctx.project_context.load_state()
    clean_args_list = [
        arg for arg in args if arg not in ["--force", "-f", "force", "--run", "-r"]
    ]
    has_focus = bool(state.context_items.files or state.context_items.folders)
    if "tree" in clean_args_list or has_focus:
        UI.info("Árbol de archivos enviado:")
        tree_str = get_context_tree(
            ctx.project_context.project_path, state.context_items
        )
        print(f"\n[dim cyan]{tree_str}[/]\n")
