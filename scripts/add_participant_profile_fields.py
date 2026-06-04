from sqlalchemy import inspect, text

from app.database import engine


PROFILE_COLUMNS = {
    "phone_number": "VARCHAR",
    "birth_year": "VARCHAR",
    "birth_month": "VARCHAR",
    "profile_description": "VARCHAR",
    "current_country": "VARCHAR",
    "current_province": "VARCHAR",
    "current_city": "VARCHAR",
    "origin_country": "VARCHAR",
    "origin_province": "VARCHAR",
    "origin_city": "VARCHAR",
    "race": "VARCHAR",
    "income_level": "VARCHAR",
    "lifestyle_tags": "VARCHAR",
}


def main():
    existing = {col["name"] for col in inspect(engine).get_columns("users")}
    added = []
    with engine.begin() as conn:
        for name, column_type in PROFILE_COLUMNS.items():
            if name in existing:
                continue
            conn.execute(text(f"ALTER TABLE users ADD COLUMN {name} {column_type}"))
            added.append(name)

    if added:
        print(f"Added participant profile columns: {', '.join(added)}")
    else:
        print("Participant profile columns already exist.")


if __name__ == "__main__":
    main()
