from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional local helper
    def load_dotenv(*args, **kwargs):
        return False

from perfumery_app.config import load_settings
from perfumery_app.database import Database
from perfumery_app.note_extraction import extract_note_pyramid
from scripts.import_scentoria_catalog import slugify


def read_products(path: Path) -> dict[str, dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        products = list(csv.DictReader(handle))

    by_slug = {}
    for row in products:
        brand = row.get("brand", "").strip() or "Unknown"
        name = row.get("perfume_name", "").strip()
        handle = row.get("handle", "").strip()
        slug = f"scentoria-{slugify(handle or f'{brand}-{name}')}"
        by_slug[slug] = row
    return by_slug


def database_from_env() -> Database:
    load_dotenv(BASE_DIR / ".env")
    settings = load_settings()
    configured_sqlite_path = Path(settings.sqlite_database_path or "data/perfumery.sqlite3")
    database_path = configured_sqlite_path if configured_sqlite_path.is_absolute() else BASE_DIR / configured_sqlite_path
    database = Database(database_path, settings)
    database.initialize()
    return database


def backfill_notes(products_path: Path, *, only_empty: bool, dry_run: bool) -> dict[str, int]:
    products_by_slug = read_products(products_path)
    database = database_from_env()
    stats = {
        "available_fragrances": 0,
        "updated": 0,
        "skipped_existing": 0,
        "missing_source": 0,
    }

    with database.connect() as conn:
        rows = conn.execute(
            """
            SELECT slug, top_notes, heart_notes, base_notes
            FROM fragrances f
            WHERE slug LIKE 'scentoria-%'
              AND EXISTS (
                SELECT 1 FROM variants v
                WHERE v.fragrance_id = f.id AND v.stock_units > 0
              )
            ORDER BY slug
            """
        ).fetchall()
        stats["available_fragrances"] = len(rows)

        if not dry_run:
            conn.begin_write()

        try:
            for fragrance in rows:
                slug = fragrance["slug"]
                source_row = products_by_slug.get(slug)
                if source_row is None:
                    stats["missing_source"] += 1
                    continue

                if only_empty and not _notes_are_empty(fragrance):
                    stats["skipped_existing"] += 1
                    continue

                notes = extract_note_pyramid(source_row)
                if not dry_run:
                    conn.execute(
                        """
                        UPDATE fragrances
                        SET top_notes = ?, heart_notes = ?, base_notes = ?
                        WHERE slug = ?
                        """,
                        (
                            json.dumps(notes["top_notes"]),
                            json.dumps(notes["heart_notes"]),
                            json.dumps(notes["base_notes"]),
                            slug,
                        ),
                    )
                stats["updated"] += 1

            if dry_run:
                conn.rollback()
            else:
                conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            database.close()

    return stats


def _notes_are_empty(row) -> bool:
    return row["top_notes"] == "[]" and row["heart_notes"] == "[]" and row["base_notes"] == "[]"


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill note pyramids for imported Scentoria fragrances.")
    parser.add_argument("--products", required=True, help="Scentoria products CSV")
    parser.add_argument(
        "--only-empty",
        action="store_true",
        help="Do not overwrite fragrances that already have notes.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show counts without updating the database.")
    args = parser.parse_args()

    stats = backfill_notes(Path(args.products), only_empty=args.only_empty, dry_run=args.dry_run)
    action = "Would update" if args.dry_run else "Updated"
    print(f"{action} {stats['updated']} of {stats['available_fragrances']} available Scentoria fragrances.")
    if stats["skipped_existing"]:
        print(f"Skipped existing notes: {stats['skipped_existing']}")
    if stats["missing_source"]:
        print(f"Missing source rows: {stats['missing_source']}")


if __name__ == "__main__":
    main()
