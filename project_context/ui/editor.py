import copy
import time

import typer

from project_context.api_drive import AIStudioDriveManager
from project_context.schema import ChunksDocument, ChunksImage, ChunksText


def format_chunk_row(index: int, chunk) -> str:
    """Formatea una fila para la tabla de resumen del editor."""
    role = getattr(chunk, "role", "unknown")

    if isinstance(chunk, ChunksDocument) or hasattr(chunk, "driveDocument"):
        ctype = "FILE"
        tokens = f"{getattr(chunk, 'tokenCount', 0)}t"
        snippet = f"[ID: {chunk.driveDocument.id}] (Contexto/Archivo)"
    elif isinstance(chunk, ChunksImage) or hasattr(chunk, "driveImage"):
        ctype = "IMG "
        tokens = f"{getattr(chunk, 'tokenCount', 0)}t"
        snippet = "[Imagen adjunta]"
    elif isinstance(chunk, ChunksText) or hasattr(chunk, "text"):
        ctype = "TEXT"
        t_count = getattr(chunk, "tokenCount", None)
        tokens = f"{t_count}t" if t_count is not None else "? t"
        raw_text = chunk.text.replace("\n", " ").replace("\r", "")
        snippet = (raw_text[:60] + "...") if len(raw_text) > 60 else raw_text
    else:
        ctype = "??? "
        tokens = "-"
        snippet = str(chunk)

    return f" {index:<3} | {role:<6} | {ctype:<5} | {tokens:<8} | {snippet}"


def get_full_content_for_pager(chunk) -> str:
    """Prepara el contenido completo para el paginador (view)."""
    output = []
    output.append("=" * 80)
    output.append(f" ROL: {getattr(chunk, 'role', 'N/A').upper()}")
    output.append("-" * 80)

    if isinstance(chunk, ChunksDocument) or hasattr(chunk, "driveDocument"):
        output.append("TIPO: DOCUMENTO DRIVE")
        output.append(f"ID: {chunk.driveDocument.id}")
        output.append(f"TOKENS: {getattr(chunk, 'tokenCount', 'N/A')}")
        output.append(
            "\n(El contenido es un archivo vinculado en Drive, no texto plano editable aquí)"
        )

    elif isinstance(chunk, ChunksText) or hasattr(chunk, "text"):
        output.append("TIPO: TEXTO")
        output.append("-" * 80)
        output.append(chunk.text)

    else:
        output.append("Contenido no reconocible o imagen.")

    output.append("\n" + "=" * 80)
    output.append("(Presiona 'q' para salir de esta vista)")
    return "\n".join(output)


def run_editor_mode(api: AIStudioDriveManager, chat_id: str):
    """
    Lógica encapsulada del editor visual.
    Funciona como una 'ventana modal' sobre la consola.
    """
    typer.echo(f"Cargando chat {chat_id} para edición...")
    chat_data = api.get_chat_ia_studio(chat_id)
    if not chat_data:
        typer.secho("Error descargando chat.", fg=typer.colors.RED)
        return

    chunks = copy.deepcopy(chat_data.chunkedPrompt.chunks)
    unsaved_changes = False

    while True:
        typer.clear()
        typer.secho(
            "\n--- MODO EDICIÓN (Borrador en Memoria) ---",
            fg=typer.colors.GREEN,
            bold=True,
        )
        typer.echo(f"Chat ID: {chat_id}")
        if unsaved_changes:
            typer.secho(
                "(!) HAY CAMBIOS SIN GUARDAR. Usa 'save' para aplicar.",
                fg=typer.colors.MAGENTA,
                bold=True,
            )

        # Renderizar Tabla
        typer.echo("\n" + "-" * 100)
        typer.echo(
            f" {'ID':<3} | {'ROL':<6} | {'TIPO':<5} | {'TOKENS':<8} | {'PREVISUALIZACIÓN'}"
        )
        typer.echo("-" * 100)

        for i, chunk in enumerate(chunks):
            row_str = format_chunk_row(i, chunk)
            color = None
            if i == 0:
                color = typer.colors.BLUE  # Contexto protegido
            if i == len(chunks) - 1:
                color = typer.colors.CYAN  # Último mensaje

            if color:
                typer.secho(row_str, fg=color)
            else:
                typer.echo(row_str)
        typer.echo("-" * 100)

        try:
            cmd_input = input("edit >> ").strip()
        except (KeyboardInterrupt, EOFError):
            break

        if not cmd_input:
            continue

        parts = cmd_input.split()
        cmd = parts[0].lower()
        args = parts[1:]

        if cmd in ["exit", "back", "q"]:
            if unsaved_changes:
                confirm = input(
                    "Tienes cambios sin guardar. ¿Salir y descartar? (s/n): "
                )
                if confirm.lower() != "s":
                    continue
            break

        elif cmd == "help":
            input(
                "\nComandos:\n  view <id> : Ver contenido completo.\n  rm <id>   : Borrar mensaje.\n  pop [n]   : Borrar últimos n.\n  save      : Guardar en Drive.\n  exit      : Salir.\n\n[Enter] para continuar..."
            )

        elif cmd == "view":
            if args and args[0].isdigit():
                idx = int(args[0])
                if 0 <= idx < len(chunks):
                    # Typer wrapper para el paginador
                    typer.echo_via_pager(get_full_content_for_pager(chunks[idx]))
                else:
                    input("ID fuera de rango. [Enter]...")

        elif cmd == "rm":
            if not args:
                typer.secho("Uso: rm <id> o rm <inicio>-<fin>", fg=typer.colors.RED)
                time.sleep(1)
                continue

            arg = args[0]
            indices_to_remove = set()

            try:
                if "-" in arg:
                    start_str, end_str = arg.split("-", 1)
                    start, end = int(start_str), int(end_str)
                    if start > end:
                        start, end = end, start
                    indices_to_remove.update(range(start, end + 1))
                elif arg.isdigit():
                    indices_to_remove.add(int(arg))
                else:
                    typer.secho(
                        "Formato inválido. Use número (N) o rango (N-M).",
                        fg=typer.colors.RED,
                    )
                    time.sleep(1.5)
                    continue

            except ValueError:
                typer.secho("Error al interpretar los índices.", fg=typer.colors.RED)
                time.sleep(1)
                continue

            if 0 in indices_to_remove:
                typer.secho(
                    "(!) El índice 0 (Contexto) está protegido y no se borrará.",
                    fg=typer.colors.YELLOW,
                )
                indices_to_remove.discard(0)
                time.sleep(1.5)

            max_idx = len(chunks) - 1
            valid_indices = {i for i in indices_to_remove if 0 < i <= max_idx}
            if not valid_indices:
                typer.secho(
                    "No se seleccionaron índices válidos para eliminar.",
                    fg=typer.colors.YELLOW,
                )
                time.sleep(1)
                continue

            new_chunks = [
                chunk for i, chunk in enumerate(chunks) if i not in valid_indices
            ]
            chunks = new_chunks
            unsaved_changes = True

            count = len(valid_indices)
            typer.secho(
                f"Marcados {count} mensaje(s) para eliminar. Usa 'save' para confirmar.",
                fg=typer.colors.GREEN,
            )
            time.sleep(1)

        elif cmd == "pop":
            count = 1
            if args and args[0].isdigit():
                count = int(args[0])

            popped = 0
            for _ in range(count):
                if len(chunks) > 1:
                    chunks.pop()
                    popped += 1
                    unsaved_changes = True
            if popped > 0:
                print(f"Eliminados {popped} mensajes.")
                time.sleep(0.5)

        elif cmd == "save":
            if not unsaved_changes:
                print("No hay cambios.")
                time.sleep(1)
                continue

            print("Subiendo cambios a Google Drive...")
            chat_data.chunkedPrompt.chunks = chunks
            if api.update_chat_file(chat_id, chat_data):
                typer.secho("¡Guardado exitoso!", fg=typer.colors.GREEN)
                unsaved_changes = False
                time.sleep(1.5)
            else:
                typer.secho("Error al guardar.", fg=typer.colors.RED)
        else:
            typer.secho("Comando no reconocido.", fg=typer.colors.RED)
            input("[Enter]...")
