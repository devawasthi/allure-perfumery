from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional local helper
    def load_dotenv(*args, **kwargs):
        return False

from perfumery_app.config import load_settings
from perfumery_app.database import Database


DESIGNER_BRANDS = {
    "Acqua Di Parma",
    "Ajmal",
    "Armani Prive",
    "Atelier Cologne",
    "Atelier Versace",
    "Bottega Veneta",
    "Bvlgari",
    "Byredo",
    "Carolina Herrera",
    "Chanel",
    "Chopard",
    "Christian Dior",
    "Dior",
    "Diptyque",
    "Dolce & Gabbana",
    "Dunhill",
    "Frederic Malle",
    "Giorgio Armani",
    "Givenchy",
    "Gucci",
    "Guerlain",
    "Hermes",
    "Houbigant",
    "Hugo Boss",
    "Jean Paul Gaultier",
    "Lancome",
    "Loewe",
    "Maison Margiela",
    "MFK",
    "Mugler",
    "Narciso Rodriguez",
    "Paco Rabanne",
    "Penhaligon's",
    "Prada",
    "Robert Piguet",
    "Serge Lutens",
    "Tom Ford",
    "Valentino",
    "Van Cleef & Arpels",
    "Versace",
    "Viktor & Rolf",
    "YSL",
}

CONCENTRATION_MAP = {
    "EDC": "Eau de Cologne",
    "EDT": "Eau de Toilette",
    "EDP": "Eau de Parfum",
    "ELIXIR": "Elixir",
    "EXTRAIT": "Extrait de Parfum",
    "EXTRAIT DE PARFUM": "Extrait de Parfum",
    "PARFUM": "Parfum",
}


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def parse_money(value: str) -> int:
    if not str(value).strip():
        return 0
    return int(round(float(str(value).replace(",", ""))))


def map_gender(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"his", "him", "men", "male"}:
        return "him"
    if normalized in {"her", "women", "female"}:
        return "her"
    return "unisex"


def title_concentration(value: str, name: str) -> str:
    tag = value.strip().upper()
    if tag in CONCENTRATION_MAP:
        return CONCENTRATION_MAP[tag]
    title = name.upper()
    for key, label in CONCENTRATION_MAP.items():
        if re.search(rf"\b{re.escape(key)}\b", title):
            return label
    return value.strip() or "Eau de Parfum"


def tags_for(row: dict[str, str]) -> list[str]:
    return [tag.strip() for tag in row.get("tags", "").split("|") if tag.strip()]


def family_for(row: dict[str, str]) -> str:
    if row.get("family", "").strip():
        return row["family"].strip().lower()
    ignored = {row.get("concentration", "").strip(), row.get("gender", "").strip(), "Fragrance"}
    candidates = [tag for tag in tags_for(row) if tag not in ignored]
    return (candidates[0] if candidates else "fragrance").strip().lower()


def size_ml(value: str) -> int:
    match = re.search(r"(\d+(?:\.\d+)?)\s*ML", value.upper())
    if not match:
        return 0
    return max(1, int(round(float(match.group(1)))))


def sale_type(value: str) -> str:
    label = value.lower()
    if "sample" in label:
        return "sample"
    if "miniature" in label:
        return "miniature"
    if "decant" in label or "travel" in label:
        return "decant"
    if "tester" in label:
        return "tester"
    if "partial" in label:
        return "partial"
    if "retail" in label:
        return "retail"
    return "retail"


def colors_for(brand: str, name: str) -> tuple[str, str]:
    digest = hashlib.sha256(f"{brand}:{name}".encode("utf-8")).hexdigest()
    hue = int(digest[:2], 16) % 360
    accent_from = f"hsl({hue}, 34%, 36%)"
    accent_to = f"hsl({(hue + 34) % 360}, 38%, 14%)"
    return accent_from, accent_to


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def database_from_env() -> Database:
    load_dotenv(BASE_DIR / ".env")
    settings = load_settings()
    configured_sqlite_path = Path(settings.sqlite_database_path or "data/perfumery.sqlite3")
    database_path = configured_sqlite_path if configured_sqlite_path.is_absolute() else BASE_DIR / configured_sqlite_path
    database = Database(database_path, settings)
    database.initialize()
    return database


def upsert_products(
    products_path: Path,
    variants_path: Path,
    *,
    price_markup_inr: int,
    only_available: bool,
    stock_units: int,
) -> tuple[int, int]:
    products = read_csv(products_path)
    variants = read_csv(variants_path)
    variants_by_product: dict[str, list[dict[str, str]]] = {}
    for variant in variants:
        variants_by_product.setdefault(variant["product_id"], []).append(variant)

    database = database_from_env()
    product_count = 0
    variant_count = 0

    with database.connect() as conn:
        conn.begin_write()
        try:
            current_max_rank_row = conn.execute("SELECT COALESCE(MAX(rank), 0) AS rank FROM fragrances").fetchone()
            next_rank = int(current_max_rank_row["rank"]) + 1

            for row in products:
                product_variants = variants_by_product.get(row["product_id"], [])
                brand = row["brand"].strip() or "Unknown"
                name = row["perfume_name"].strip()
                slug = f"scentoria-{slugify(row['handle'] or f'{brand}-{name}')}"
                if only_available:
                    product_variants = [variant for variant in product_variants if variant["available"] == "True"]
                if not product_variants:
                    if only_available:
                        conn.execute("DELETE FROM fragrances WHERE slug = ?", (slug,))
                    continue

                family = family_for(row)
                accent_from, accent_to = colors_for(brand, name)
                collection_type = "designer" if brand in DESIGNER_BRANDS else "niche"
                concentration = title_concentration(row.get("concentration", ""), name)
                bottle_size = max((size_ml(variant["size_or_format"]) for variant in product_variants), default=0)
                if bottle_size <= 0:
                    bottle_size = 100

                conn.execute(
                    """
                    INSERT INTO fragrances (
                        slug, brand, name, collection_type, gender, family, concentration,
                        origin, description, signature, top_notes, heart_notes, base_notes,
                        accent_from, accent_to, image_url, photo_icon_url, artwork_kind,
                        bottle_size_ml, featured, rank
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(slug) DO UPDATE SET
                        brand = excluded.brand,
                        name = excluded.name,
                        collection_type = excluded.collection_type,
                        gender = excluded.gender,
                        family = excluded.family,
                        concentration = excluded.concentration,
                        origin = excluded.origin,
                        description = excluded.description,
                        signature = excluded.signature,
                        top_notes = excluded.top_notes,
                        heart_notes = excluded.heart_notes,
                        base_notes = excluded.base_notes,
                        accent_from = excluded.accent_from,
                        accent_to = excluded.accent_to,
                        image_url = excluded.image_url,
                        photo_icon_url = excluded.photo_icon_url,
                        artwork_kind = excluded.artwork_kind,
                        bottle_size_ml = excluded.bottle_size_ml
                    """,
                    (
                        slug,
                        brand,
                        name,
                        collection_type,
                        map_gender(row.get("gender", "")),
                        family,
                        concentration,
                        "Imported",
                        row.get("description", "").strip() or f"{brand} {name}.",
                        family.title(),
                        json.dumps([]),
                        json.dumps([]),
                        json.dumps([]),
                        accent_from,
                        accent_to,
                        row.get("image_url", "").strip() or f"/artwork/{slug}.svg",
                        "",
                        "photo" if row.get("image_url", "").strip() else "generated",
                        bottle_size,
                        0,
                        next_rank,
                    ),
                )
                fragrance_row = conn.execute("SELECT id FROM fragrances WHERE slug = ?", (slug,)).fetchone()
                fragrance_id = int(fragrance_row["id"])
                product_count += 1
                next_rank += 1

                keep_skus = []
                for variant in product_variants:
                    variant_title = variant["size_or_format"].strip() or variant["variant_title"].strip() or "Retail"
                    sku = f"scentoria-{variant['variant_id']}"
                    keep_skus.append(sku)
                    price = parse_money(variant["price_inr"]) + price_markup_inr
                    compare_at = parse_money(variant.get("compare_at_price_inr", ""))
                    if compare_at:
                        compare_at += price_markup_inr
                    conn.execute(
                        """
                        INSERT INTO variants (
                            fragrance_id, sku, sale_type, size_label, size_ml, price_inr,
                            stock_units, badge, statement
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(sku) DO UPDATE SET
                            fragrance_id = excluded.fragrance_id,
                            sale_type = excluded.sale_type,
                            size_label = excluded.size_label,
                            size_ml = excluded.size_ml,
                            price_inr = excluded.price_inr,
                            stock_units = excluded.stock_units,
                            badge = excluded.badge,
                            statement = excluded.statement
                        """,
                        (
                            fragrance_id,
                            sku,
                            sale_type(variant_title),
                            variant_title,
                            size_ml(variant_title),
                            price,
                            stock_units,
                            "Imported",
                            f"Imported Scentoria listing. Original listed price plus INR {price_markup_inr:,}.",
                        ),
                    )
                    variant_count += 1

                if keep_skus:
                    placeholders = ", ".join("?" for _ in keep_skus)
                    conn.execute(
                        f"DELETE FROM variants WHERE fragrance_id = ? AND sku LIKE 'scentoria-%' AND sku NOT IN ({placeholders})",
                        [fragrance_id, *keep_skus],
                    )

            conn.commit()
        except Exception:
            conn.rollback()
            raise

    database.close()
    return product_count, variant_count


def main() -> None:
    parser = argparse.ArgumentParser(description="Import Scentoria CSV rows into The Scentist database.")
    parser.add_argument("--products", required=True, help="Scentoria products CSV")
    parser.add_argument("--variants", required=True, help="Scentoria variants CSV")
    parser.add_argument("--price-markup-inr", type=int, default=1000)
    parser.add_argument("--stock-units", type=int, default=1, help="Stock units assigned to imported available variants")
    parser.add_argument(
        "--include-unavailable",
        action="store_true",
        help="Import unavailable source variants too. By default only available variants are imported.",
    )
    args = parser.parse_args()

    products, variants = upsert_products(
        Path(args.products),
        Path(args.variants),
        price_markup_inr=args.price_markup_inr,
        only_available=not args.include_unavailable,
        stock_units=args.stock_units,
    )
    print(f"Imported/updated {products} products and {variants} variants.")
    print(f"Applied price markup: INR {args.price_markup_inr:,}")


if __name__ == "__main__":
    main()
