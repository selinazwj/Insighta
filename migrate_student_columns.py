"""
ive made this migration to add the student segmentation columns
that were missing from the team's earlier migration script.
covers both users and surveys tables.
run with: python migrate_student_columns.py
"""
import sqlite3

conn = sqlite3.connect("survey.db")


def add_col(table, col, col_type="VARCHAR"):
    # tries to add a column, skips if its already there
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
        print(f"  added {table}.{col}")
    except sqlite3.OperationalError as e:
        if "duplicate" in str(e).lower():
            print(f"  skipped {table}.{col} (already exists)")
        else:
            raise


# columns missing on users table
user_cols = [
    "student_status",
    "year_in_school",
    "international_domestic",
    "experience_tags",
    "participation_format",
    "device_type",
]

# columns missing on surveys table (target_ versions of the same idea)
survey_cols = [
    "target_student_status",
    "target_year_in_school",
    "target_international_domestic",
    "target_experience_tags",
    "target_participation_format",
    "target_device",
]

print("users table:")
for col in user_cols:
    add_col("users", col)

print("surveys table:")
for col in survey_cols:
    add_col("surveys", col)

conn.commit()
conn.close()
print("done.")