"""
ive made this script to sync the db schema with whatever's in models.py
it finds any column in the model that's missing from the db and adds it.
this fixes the team's migration drift problem in one shot.
run with: python migrate_sync_schema.py
"""
from app.models import Base
import sqlite3


def sql_type_for(col):
    # converts SQLAlchemy type names into sqlite-compatible ones
    t = str(col.type).upper()
    if "INT" in t or "BOOL" in t:
        return "INTEGER"
    if "FLOAT" in t or "NUMERIC" in t or "DECIMAL" in t:
        return "REAL"
    if "DATETIME" in t or "TIMESTAMP" in t:
        return "DATETIME"
    return "VARCHAR"


conn = sqlite3.connect("survey.db")
print("syncing schema with models...")

# loop through every table the model defines
for table_name, table in Base.metadata.tables.items():
    # check if the table even exists in the db
    table_check = list(conn.execute(
        f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}'"
    ))
    if not table_check:
        print(f"  skipping {table_name} (table not in db)")
        continue

    # get current columns in db
    actual_cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table_name})")}

    # find any model column that the db is missing
    missing = [c for c in table.columns if c.name not in actual_cols]

    if not missing:
        print(f"  {table_name}: already synced")
        continue

    print(f"  {table_name}: adding {len(missing)} missing columns")
    for col in missing:
        col_type = sql_type_for(col)
        try:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {col.name} {col_type}")
            print(f"    added {col.name} ({col_type})")
        except sqlite3.OperationalError as e:
            print(f"    failed {col.name}: {e}")

conn.commit()
conn.close()
print("done.")