#!/usr/bin/env python3
"""Update Paco Rabanne 1 Million fragrance images in the configured database."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path


IMAGE_URL = "/assets/paco-rabanne-1-million.png"
WHERE_SQLITE = "lower(brand) = 'paco rabanne' AND lower(name) LIKE ?"
WHERE_POSTGRES = "lower(brand) = 'paco rabanne' AND lower(name) LIKE %s"
NAME_PATTERN = "%1 million%"


def update_sqlite(path: Path) -> int:
    with sqlite3.connect(path) as conn:
        cursor = conn.execute(
            f"""
            UPDATE fragrances
               SET image_url = ?,
                   photo_icon_url = ?,
                   artwork_kind = 'photo'
             WHERE {WHERE_SQLITE}
            """,
            (IMAGE_URL, IMAGE_URL, NAME_PATTERN),
        )
        return cursor.rowcount


def update_postgres(database_url: str) -> int:
    try:
        import psycopg
    except ImportError as exc:
        raise SystemExit(
            "Install PostgreSQL dependencies first: python3 -m pip install psycopg[binary]"
        ) from exc

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE fragrances
                   SET image_url = %s,
                       photo_icon_url = %s,
                       artwork_kind = 'photo'
                 WHERE {WHERE_POSTGRES}
                """,
                (IMAGE_URL, IMAGE_URL, NAME_PATTERN),
            )
            return cursor.rowcount or 0


def main() -> None:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if database_url:
        updated = update_postgres(database_url)
        print(f"Updated {updated} Neon/PostgreSQL fragrance rows.")
        return

    sqlite_path = Path(os.getenv("SQLITE_DATABASE_PATH", "data/preprod.sqlite3"))
    if not sqlite_path.is_absolute():
        sqlite_path = Path(__file__).resolve().parents[1] / sqlite_path
    updated = update_sqlite(sqlite_path)
    print(f"Updated {updated} SQLite fragrance rows in {sqlite_path}.")


if __name__ == "__main__":
    main()
