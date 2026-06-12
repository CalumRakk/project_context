import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

import typer

from project_context.core.profile_mg import ProfileManager
from project_context.ui import UI

app = typer.Typer(
    help="Herramienta de migración para bases de datos de project-context."
)


def parse_old_timestamp(timestamp_str: str) -> datetime:
    """Intenta parsear el timestamp antiguo (ej: 20250101_123456) a un objeto datetime."""
    try:
        return datetime.strptime(timestamp_str, "%Y%m%d_%H%M%S")
    except Exception:
        return datetime.now()


@app.command()
def migrate(
    project_path: Path = typer.Option(
        Path.cwd(),
        "--project-path",
        help="Ruta del proyecto que contiene la base de datos a migrar.",
    ),
):
    """
    Migra la base de datos local de snapshots al nuevo formato multiusuario de la versión 0.4.2+.
    """
    db_path = project_path / ".project_context" / "snapshots.db"

    if not db_path.exists():
        UI.error(f"No se encontró ninguna base de datos en: {db_path}")
        raise typer.Exit(code=1)

    # Resolver el correo del propietario de forma resiliente
    profile_manager = ProfileManager()
    active_email = None

    active_profile_name = profile_manager.get_active_profile_name()
    if active_profile_name:
        try:
            profile_data = profile_manager.load_profile_data(active_profile_name)
            active_email = profile_data.email
        except Exception:
            pass

    if not active_email:
        # Intentar buscar perfiles guardados si no hay uno activo
        available_profiles = profile_manager.list_profiles()
        if available_profiles:
            UI.info(
                "No hay un perfil activo, pero se encontraron los siguientes perfiles locales:"
            )
            for idx, prof in enumerate(available_profiles):
                print(f"  [{idx}] {prof}")

            choice = typer.prompt(
                "Selecciona el número de tu perfil o presiona Enter para ingresar un correo manualmente",
                default="",
                show_default=False,
            ).strip()

            if choice.isdigit() and int(choice) < len(available_profiles):
                selected_prof = available_profiles[int(choice)]
                try:
                    profile_data = profile_manager.load_profile_data(selected_prof)
                    active_email = profile_data.email
                    profile_manager.set_active_profile(selected_prof)
                    UI.success(f"Perfil '{selected_prof}' establecido como activo.")
                except Exception as e:
                    UI.warn(f"No se pudo cargar el perfil seleccionado: {e}")

        # Si aún no tenemos correo, lo solicitamos de forma manual
        if not active_email:
            UI.warn("No se detectó ningún perfil configurado en el sistema.")
            email_input = typer.prompt(
                "Por favor, ingresa el correo de Google Drive que deseas asociar como propietario de estos snapshots"
            ).strip()

            if not email_input:
                UI.error(
                    "Operación cancelada. Se requiere un correo electrónico para migrar los snapshots."
                )
                raise typer.Exit(code=1)
            active_email = email_input

    UI.info(
        f"Iniciando migración. Se asignará la propiedad al correo: [bold cyan]{active_email}[/]"
    )

    # Copia de seguridad física preventiva de la base de datos
    backup_path = db_path.with_suffix(".db.bak")
    try:
        shutil.copy2(db_path, backup_path)
        UI.success(f"Copia de seguridad preventiva creada en: [dim]{backup_path}[/]")
    except Exception as e:
        UI.error(f"No se pudo crear la copia de seguridad: {e}")
        raise typer.Exit(code=1)

    # Lectura de datos crudos (Raw SQL)
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    try:
        cursor.execute(
            "SELECT timestamp, human_time, drive_modified_time, message, chat_hash, context_hash, category FROM snapshot"
        )
        old_snapshots = cursor.fetchall()
    except sqlite3.OperationalError as e:
        UI.error(
            f"La tabla 'snapshot' no tiene la estructura esperada o ya fue migrada: {e}"
        )
        conn.close()
        raise typer.Exit(code=1)

    old_assets = []
    try:
        cursor.execute(
            "SELECT snapshot_id, drive_file_id, filename, mime_type, file_hash FROM snapshotasset"
        )
        old_assets = cursor.fetchall()
    except sqlite3.OperationalError:
        pass

    # Limpieza del esquema antiguo
    UI.info("Reestructurando tablas de la base de datos...")
    cursor.execute("DROP TABLE IF EXISTS snapshotasset")
    cursor.execute("DROP TABLE IF EXISTS snapshot")
    conn.commit()
    conn.close()

    # Inicializar nuevas tablas
    from project_context.core.database import Snapshot, SnapshotAsset
    from project_context.core.database import db as peewee_db

    peewee_db.init(str(db_path))
    peewee_db.connect(reuse_if_open=True)
    peewee_db.create_tables([Snapshot, SnapshotAsset], safe=True)

    # Migrar registros lógicos
    id_mapping = {}

    with peewee_db.atomic():
        for row in old_snapshots:
            (
                timestamp,
                human_time,
                drive_modified_time,
                message,
                chat_hash,
                context_hash,
                category,
            ) = row
            parsed_date = parse_old_timestamp(timestamp)

            new_snap = Snapshot.create(
                message=message or f"Migrado (Historial: {human_time})",
                category=category or "user",
                creator_email=active_email,
                created_at=parsed_date,
                updated_at=parsed_date,
            )
            id_mapping[timestamp] = new_snap.id

            SnapshotAsset.create(
                snapshot=new_snap,
                email=active_email,
                file_id=f"PLACEHOLDER_MIGRADO_CONTEXT_{new_snap.id}",
                filename="context.txt",
                mime_type="text/plain",
                md5sum=context_hash,
                role="context",
                modified_at=parsed_date,
            )

            SnapshotAsset.create(
                snapshot=new_snap,
                email=active_email,
                file_id=f"PLACEHOLDER_MIGRADO_CHAT_{new_snap.id}",
                filename="chat.prompt",
                mime_type="application/vnd.google-makersuite.prompt",
                md5sum=chat_hash,
                role="chat",
                modified_at=parsed_date,
            )

    # Migrar adjuntos adicionales si existían
    if old_assets:
        UI.info(f"Migrando {len(old_assets)} archivos adjuntos secundarios...")
        with peewee_db.atomic():
            for asset_row in old_assets:
                snapshot_id, drive_file_id, filename, mime_type, file_hash = asset_row

                nuevo_snap_id = id_mapping.get(snapshot_id)
                if nuevo_snap_id:
                    SnapshotAsset.create(
                        snapshot=nuevo_snap_id,
                        email=active_email,
                        file_id=drive_file_id,
                        filename=filename,
                        mime_type=mime_type,
                        md5sum=file_hash,
                        role="attachment",
                        modified_at=datetime.now(),
                    )

    peewee_db.close()
    UI.success("¡Base de datos migrada exitosamente al nuevo formato!")


if __name__ == "__main__":
    app()
