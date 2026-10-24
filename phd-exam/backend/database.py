from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import QueuePool

from backend.config import DATABASE_URL

if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=QueuePool,
        pool_size=15,
        max_overflow=15
    )

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()
else:
    # Production (PostgreSQL) pool sized for ~100 concurrent candidates across
    # multiple Gunicorn/Uvicorn workers. pool_pre_ping avoids handing out a
    # connection the server already dropped (e.g. after a Postgres restart or
    # an idle-connection reaper) — without it the first query on a stale
    # connection raises and can crash the request instead of transparently
    # reconnecting. pool_recycle proactively retires connections before most
    # managed-Postgres / firewall idle-timeouts would kill them silently.
    engine = create_engine(
        DATABASE_URL,
        pool_size=30,
        max_overflow=50,
        pool_pre_ping=True,
        pool_recycle=1800,
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """FastAPI dependency: one DB session per request, always closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
