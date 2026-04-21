import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    # Local development: use SQLite, database file in project root
    DATABASE_URL = "sqlite:///./survey.db"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Import Base only (not User/Survey/Response) to avoid circular import
from app.models import Base


def _sqlite_default_sql(default_value):
    if default_value is None:
        return None
    if isinstance(default_value, bool):
        return "1" if default_value else "0"
    if isinstance(default_value, (int, float)):
        return str(default_value)
    if isinstance(default_value, str):
        return "'" + default_value.replace("'", "''") + "'"
    return None


def _sqlite_column_sql(column, dialect):
    col_type = column.type.compile(dialect=dialect) or "TEXT"
    sql = f'"{column.name}" {col_type}'
    default = getattr(column, "default", None)
    if default is not None and getattr(default, "is_scalar", False):
        default_sql = _sqlite_default_sql(default.arg)
        if default_sql is not None:
            sql += f" DEFAULT {default_sql}"
    return sql


def _ensure_sqlite_schema(bind, metadata):
    if bind.url.get_backend_name() != "sqlite":
        metadata.create_all(bind=bind)
        return

    metadata.create_all(bind=bind)
    with bind.begin() as conn:
        for table in metadata.sorted_tables:
            existing_cols = {row[1] for row in conn.exec_driver_sql(f'PRAGMA table_info("{table.name}")')}
            for column in table.columns:
                if column.name in existing_cols:
                    continue
                alter_sql = f'ALTER TABLE "{table.name}" ADD COLUMN {_sqlite_column_sql(column, bind.dialect)}'
                conn.exec_driver_sql(alter_sql)


_ensure_sqlite_schema(engine, Base.metadata)


def get_db():
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
