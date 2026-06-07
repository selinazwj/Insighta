"""
ive made this migration to add researcher-review columns to responses.
run with: python migrate_review_columns.py
"""
import sqlite3

conn = sqlite3.connect("survey.db")

# new columns ive added for researcher review feature
cols_to_add = [
    ("reviewed_at", "DATETIME"),
    ("reviewed_by", "INTEGER"),
    ("review_notes", "VARCHAR"),
]

for name, col_type in cols_to_add:
    try:
        conn.execute(f"ALTER TABLE responses ADD COLUMN {name} {col_type}")
        print(f"  added responses.{name}")
    except sqlite3.OperationalError as e:
        if "duplicate" in str(e).lower():
            print(f"  skipped responses.{name} (already exists)")
        else:
            raise

conn.commit()
conn.close()
print("done.")