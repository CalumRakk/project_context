import logging
from datetime import datetime, timezone
from typing import cast

from peewee import (
    AutoField,
    CharField,
    DateTimeField,
    ForeignKeyField,
    Model,
    SqliteDatabase,
)

from project_context.core.project_context import ProjectContext

db = SqliteDatabase(None)
logger = logging.getLogger(__name__)


def utc_now():
    return datetime.now(timezone.utc)


class BaseModel(Model):
    created_at = DateTimeField(default=utc_now)
    updated_at = DateTimeField(default=utc_now)

    def save(self, *args, **kwargs):
        self.updated_at = utc_now()
        return super().save(*args, **kwargs)

    class Meta:
        database = db


class Snapshot(BaseModel):
    """Representa un punto de restauración del chat y el contexto de Google Drive."""

    id = AutoField()  # Clave primaria autoincremental
    message = CharField()
    category = CharField()  # 'user', 'stash', 'auto'
    creator_email = CharField()

    # chat_id = CharField()  # ID del recurso Chat en Drive
    # file_id = CharField()  # ID del recurso Contexto maestro en Drive

    def get_assets_from_email(self, email: str):
        return list(
            SnapshotAsset.select().where(
                SnapshotAsset.snapshot == self, SnapshotAsset.email == email
            )
        )

    def get_assets_from_creator(self) -> list["SnapshotAsset"]:
        return list(SnapshotAsset.select().where(SnapshotAsset.snapshot == self))


class SnapshotAsset(BaseModel):
    """Cualquier recurso físico (texto o binario) respaldado en el CAS local."""

    snapshot = ForeignKeyField(Snapshot, backref="assets")

    email = CharField()  # El email del propietario de este file_id en Drive

    # Campo de Google Drive

    file_id = cast(str, CharField())
    filename = cast(str, CharField())
    mime_type = cast(str, CharField())
    md5sum = cast(str, CharField())
    modified_at = cast(datetime, DateTimeField())
    role = cast(str, CharField())  # 'chat', 'context', 'attachment'


# class Snapshot(BaseModel):
#     """Representa un punto de restauración del chat y el contexto."""

#     timestamp = CharField(unique=True, primary_key=True)
#     human_time = CharField()
#     drive_modified_time = CharField()

#     message = CharField(null=True)

#     chat_hash = CharField()
#     context_hash = CharField()
#     category = CharField(default="user")  # 'user', 'stash', 'auto'


# class SnapshotAsset(BaseModel):
#     """Recursos binarios vinculados a un snapshot."""

#     snapshot = ForeignKeyField(Snapshot, backref="assets", on_delete="CASCADE")
#     drive_file_id = CharField()
#     filename = CharField()
#     mime_type = CharField()
#     file_hash = CharField()


class DatabaseSession:
    """
    Administrador de contexto encargado exclusivamente del ciclo de vida de la conexión
    a la base de datos SQLite y de la inicialización de las tablas base.
    """

    def __init__(self, project_context: ProjectContext):
        self.project_context = project_context

    def __enter__(self):
        db_path = self.project_context.local_dir / "snapshots.db"
        db.init(
            str(db_path),
            pragmas={
                "journal_mode": "wal",
                "cache_size": -1024 * 64,
                "foreign_keys": 1,
                "ignore_check_constraints": 0,
                "synchronous": 1,
            },
        )
        if db.is_closed():
            db.connect(reuse_if_open=True)

        db.create_tables([Snapshot, SnapshotAsset], safe=True)
        return db

    def __exit__(self, exc_type, exc_val, exc_tb):
        if not db.is_closed():
            db.close()
