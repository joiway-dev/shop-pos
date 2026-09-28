"""SQLite engine and session setup.

Every connection runs with WAL, foreign keys and a busy timeout. Transactions
are controlled explicitly (pysqlite's own implicit BEGIN handling is disabled)
so that later phases can use `BEGIN IMMEDIATE` for document numbering.
"""

import json

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker


def _json_serializer(obj) -> str:
    # Keep Thai text readable in the database instead of \uXXXX escapes.
    return json.dumps(obj, ensure_ascii=False)


def create_db_engine(db_url: str) -> Engine:
    engine = create_engine(
        db_url,
        json_serializer=_json_serializer,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn, _record):
        # Let SQLAlchemy emit BEGIN itself (see _on_begin).
        dbapi_conn.isolation_level = None
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()

    @event.listens_for(engine, "begin")
    def _on_begin(conn):
        conn.exec_driver_sql("BEGIN")

    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
