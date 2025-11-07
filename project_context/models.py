from datetime import datetime
from pathlib import Path
from typing import Union

import peewee

db_proxy = peewee.Proxy()


def generate_unique_id(path: Union[str, Path]) -> str:
    """Genera un identificador único a partir del stat del archivo.

    Formato: "<st_dev>-<st_ino>" — robusto contra renombres pero no contra
    copiar el archivo a otro FS.
    """
    p = Path(path) if isinstance(path, str) else path
    st = p.stat()
    return f"{st.st_dev}-{st.st_ino}"


def init_sqlite(
    database_path: Union[str, Path], pragmas: dict | None = None
) -> peewee.Database:
    """Inicializa la base SQLite y retorna la instancia.

    Args:
        database_path: ruta al archivo sqlite.
        pragmas: diccionario opcional con pragmas (p.ej. journal_mode).
    """
    database_path_str = str(database_path)
    db = peewee.SqliteDatabase(database_path_str)
    if pragmas:
        for name, value in pragmas.items():
            db.pragma({name: value})

    db_proxy.initialize(db)
    return db


class Folder(peewee.Model):
    """Modelo que almacena metadatos básicos de un folder indexado."""

    id: str
    inodo: str
    path_str: str
    modified_at: datetime
    total_size: int

    id = peewee.CharField(max_length=255, primary_key=True)  # type: ignore
    path_str = peewee.CharField()  # type: ignore
    modified_at = peewee.DateTimeField(null=True)  # type: ignore
    inodo = peewee.CharField(max_length=255)  # type: ignore

    class Meta:
        database = db_proxy

    @classmethod
    def create_from_path(cls, path: Union[str, Path]) -> "Folder":
        """Crea (sin guardar) una instancia de Folder a partir de una ruta."""
        p = Path(path) if isinstance(path, str) else path
        inodo = generate_unique_id(p)
        inst = cls(inodo=inodo, path_str=str(p))
        return inst
