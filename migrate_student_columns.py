"""
ive made this migration to add the student segmentation columns
that were missing from the team's earlier migration script.
run with: python migrate_student_columns.py
"""
import sqlite3

conn = sqlite3.connect("survey.db")

# these columns exist in models.py but never got migrated to the db
cols_to_add = [
    "student_status",
    "year_in_school",
    "international_domestic",
    "experience_tags",
    "participation_format",
    "device_type",
]

for col in cols_to_add:
    try:
        conn.execute(f"ALTER TABLE users ADD COLUMN {col} VARCHAR")
        print(f"  added users.{col}")
    except sqlite3.OperationalError as e:
        if "duplicate" in str(e).lower():
            print(f"  skipped users.{col} (already exists)")
        else:
            raise

conn.commit()
conn.close()
print("done.")