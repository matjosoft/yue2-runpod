"""Alembic environment.

``core.db.Database.migrate`` passes its own connection in ``config.attributes``. The
command line (``alembic -c server/alembic.ini upgrade head``) has none, so the database
path comes from the server settings (``STUDIO_DATA_DIR``).
"""

from alembic import context
from sqlalchemy import Connection, create_engine

from studio_server.config import ServerSettings
from studio_server.core.db import Base


def run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=Base.metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def main() -> None:
    if context.is_offline_mode():
        raise SystemExit("offline (--sql) migrations are not supported")
    connection: Connection | None = context.config.attributes.get("connection")
    if connection is not None:
        run(connection)
        return
    db_path = ServerSettings.from_env().db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            run(conn)
    finally:
        engine.dispose()


main()
