from logging.config import fileConfig

from alembic import context

from app.config import load_config
from app.db import create_db_engine
from app.models import Base

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _db_url() -> str:
    url = config.get_main_option("sqlalchemy.url")
    if url:
        return url
    app_config = load_config()
    app_config.ensure_dirs()
    return app_config.db_url


def run_migrations_offline() -> None:
    context.configure(
        url=_db_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_db_engine(_db_url())
    with engine.connect() as connection:
        # Batch mode rebuilds tables (copy -> drop -> rename). With foreign keys
        # on, dropping a table that other rows point to fails, so switch them
        # off for the migration (must happen outside a transaction) and verify
        # every reference afterwards before committing.
        raw = connection.connection.driver_connection
        raw.execute("PRAGMA foreign_keys=OFF")
        try:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                render_as_batch=True,  # SQLite needs batch mode for ALTER TABLE
            )
            with context.begin_transaction():
                context.run_migrations()
                broken = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
                if broken:
                    raise RuntimeError(f"foreign key check failed after migration: {broken[:5]}")
        finally:
            raw.execute("PRAGMA foreign_keys=ON")
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
