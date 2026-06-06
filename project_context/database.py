import logging

from peewee import CharField, ForeignKeyField, Model, SqliteDatabase

from project_context.workspace import ProjectContext

db = SqliteDatabase(None)
logger = logging.getLogger(__name__)


class BaseModel(Model):
    class Meta:
        database = db


class Snapshot(BaseModel):
    """Representa un punto de restauración del chat y el contexto."""

    timestamp = CharField(unique=True, primary_key=True)
    human_time = CharField()
    drive_modified_time = CharField()
    message = CharField(null=True)
    chat_hash = CharField()
    context_hash = CharField()
    category = CharField(default="user")  # 'user', 'stash', 'auto'


class SnapshotAsset(BaseModel):
    """Recursos binarios vinculados a un snapshot."""

    snapshot = ForeignKeyField(Snapshot, backref="assets", on_delete="CASCADE")
    drive_file_id = CharField()
    filename = CharField()
    mime_type = CharField()
    file_hash = CharField()


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

        # Garantiza que las tablas existan al establecer la sesión
        db.create_tables([Snapshot, SnapshotAsset], safe=True)
        return db

    def __exit__(self, exc_type, exc_val, exc_tb):
        if not db.is_closed():
            db.close()
