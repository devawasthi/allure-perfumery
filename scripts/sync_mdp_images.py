#!/usr/bin/env python3
"""Sync fragrance images from MDP India's public Shopify catalog.

The script matches MDP products to existing fragrances by normalized brand and
fragrance name, then updates image_url/photo_icon_url for confident matches.
Local /assets images are preserved by default so hand-curated artwork is not
overwritten accidentally.
"""

from __future__ import annotations

import argparse
from collections import Counter
import difflib
import json
import os
import re
import sqlite3
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_SQLITE_PATH = BASE_DIR / "data" / "preprod.sqlite3"
MDP_PRODUCTS_URL = "https://mdpindia.com/products.json"
USER_AGENT = "Mozilla/5.0 (compatible; TheScentistImageSync/1.0)"

BRAND_ALIASES = {
    "a lab on fire": "a lab on fire",
    "acqua di parma": "acqua di parma",
    "amouage": "amouage",
    "armani prive": "giorgio armani",
    "bdk parfums": "bdk",
    "boadicea the victorious": "boadicea the victorious",
    "by kilian": "kilian",
    "chloe": "chloe",
    "christian dior": "christian dior",
    "clive christian": "clive christian",
    "creed": "creed",
    "dolce and gabbana": "dolce and gabbana",
    "dolce gabbana": "dolce and gabbana",
    "ex nihilo": "ex nihilo",
    "frederic malle": "frederic malle",
    "goldfield and banks": "goldfield and banks",
    "goldfield banks": "goldfield and banks",
    "giorgio armani": "giorgio armani",
    "houbigant paris": "houbigant",
    "initio parfums prives": "initio",
    "jean paul gaultier": "jean paul gaultier",
    "kilian": "kilian",
    "laurent mazzone": "laurent mazzone",
    "maison francis kurkdjian": "mfk",
    "maison kurkdjian": "mfk",
    "mfk": "mfk",
    "memo": "memo",
    "memo paris": "memo",
    "narciso rodriguez": "narciso rodriguez",
    "paco rabanne": "paco rabanne",
    "parfums de marly": "parfums de marly",
    "perris monte carlo": "perris monte carlo",
    "roja": "roja",
    "roja london": "roja",
    "the spirit of dubai": "spirit of dubai",
    "tiziana terenzi": "tiziana terenzi",
    "tom ford": "tom ford",
    "van cleef and arpels": "van cleef and arpels",
    "viktor and rolf": "viktor and rolf",
    "xerjoff": "xerjoff",
    "yves saint laurent": "ysl",
    "ysl": "ysl",
}

BRAND_WORDS_TO_STRIP = sorted(
    {
        "acqua di parma",
        "armani prive",
        "bdk parfums",
        "christian dior",
        "dolce and gabbana",
        "dolce gabbana",
        "giorgio armani",
        "goldfield and banks",
        "jean paul gaultier",
        "maison francis kurkdjian",
        "maison crivelli",
        "memo paris",
        "paco rabanne",
        "parfums de marly",
        "roja london",
        "tiziana terenzi",
        "tom ford",
        "van cleef and arpels",
        "yves saint laurent",
    },
    key=len,
    reverse=True,
)

NOISE_WORDS = {
    "authentic",
    "bottle",
    "box",
    "case",
    "gift",
    "imported",
    "mini",
    "miniature",
    "partial",
    "refill",
    "retail",
    "set",
    "size",
    "spray",
    "tester",
    "travel",
    "with",
}

GENERIC_CONCENTRATION_PHRASES = {
    "cologne",
    "eau de cologne",
    "eau de parfum",
    "eau de perfume",
    "eau de toilette",
    "edc",
    "edp",
    "edt",
    "extrait de parfum",
    "extrait de perfume",
}


@dataclass(frozen=True)
class CatalogProduct:
    brand: str
    name: str
    image_url: str
    source_url: str
    key: str
    loose_key: str


@dataclass(frozen=True)
class FragranceRow:
    slug: str
    brand: str
    name: str
    image_url: str
    key: str
    loose_key: str


@dataclass(frozen=True)
class Match:
    row: FragranceRow
    product: CatalogProduct
    method: str
    score: float


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii")
    value = value.lower()
    value = value.replace("&", " and ")
    value = value.replace("+", " plus ")
    value = re.sub(r"\bperfume\b", "parfum", value)
    value = re.sub(r"\bextrait\s+de\s+perfume\b", "extrait de parfum", value)
    value = re.sub(r"\beau\s+de\s+perfume\b", "eau de parfum", value)
    value = re.sub(r"\bedp\b", "eau de parfum", value)
    value = re.sub(r"\bedt\b", "eau de toilette", value)
    value = re.sub(r"\bedc\b", "eau de cologne", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def normalize_brand(value: str) -> str:
    brand = normalize_text(value)
    return BRAND_ALIASES.get(brand, brand)


def strip_brand_prefix(name: str, brand: str) -> str:
    normalized = normalize_text(name)
    candidates = {normalize_text(brand), normalize_brand(brand), *BRAND_WORDS_TO_STRIP}
    for candidate in sorted(candidates, key=len, reverse=True):
        if candidate and normalized.startswith(f"{candidate} "):
            normalized = normalized[len(candidate) + 1 :]
    return normalized


def remove_noise_tokens(value: str, *, loose: bool) -> str:
    value = normalize_text(value)
    value = re.sub(r"\b\d+(?:\.\d+)?\s*(ml|m l|oz|g)\b", " ", value)
    value = re.sub(r"\b\d+(?:\.\d+)?\s*fl\s*oz\b", " ", value)
    value = re.sub(r"\b\d+\s*x\s*\d+(?:\.\d+)?\s*ml\b", " ", value)
    tokens = []
    skip_phrases = set(NOISE_WORDS)
    if loose:
        skip_phrases |= GENERIC_CONCENTRATION_PHRASES
    phrase_scrubbed = value
    for phrase in sorted(skip_phrases, key=len, reverse=True):
        phrase_scrubbed = re.sub(rf"\b{re.escape(phrase)}\b", " ", phrase_scrubbed)
    for token in phrase_scrubbed.split():
        if token.isdigit():
            continue
        tokens.append(token)
    return re.sub(r"\s+", " ", " ".join(tokens)).strip()


def product_keys(brand: str, name: str) -> tuple[str, str]:
    brand_key = normalize_brand(brand)
    stripped = strip_brand_prefix(name, brand)
    strong = remove_noise_tokens(stripped, loose=False)
    loose = remove_noise_tokens(stripped, loose=True)
    return f"{brand_key}::{strong}", f"{brand_key}::{loose}"


def fetch_json(url: str, retries: int = 3) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # pragma: no cover - network dependent
            last_error = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Could not fetch {url}: {last_error}") from last_error


def iter_mdp_products(max_pages: int) -> Iterable[CatalogProduct]:
    seen_ids: set[int] = set()
    for page in range(1, max_pages + 1):
        query = urllib.parse.urlencode({"limit": 250, "page": page})
        payload = fetch_json(f"{MDP_PRODUCTS_URL}?{query}")
        products = payload.get("products") or []
        if not products:
            break
        for product in products:
            product_id = int(product.get("id") or 0)
            if product_id in seen_ids:
                continue
            seen_ids.add(product_id)
            images = product.get("images") or []
            image_url = ""
            if images:
                image_url = (images[0].get("src") or "").strip()
            if not image_url:
                continue
            brand = (product.get("vendor") or "").strip()
            name = (product.get("title") or "").strip()
            key, loose_key = product_keys(brand, name)
            handle = product.get("handle") or ""
            yield CatalogProduct(
                brand=brand,
                name=name,
                image_url=image_url,
                source_url=f"https://mdpindia.com/products/{handle}",
                key=key,
                loose_key=loose_key,
            )


def connect_sqlite(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def connect_database(database_url: str, sqlite_path: Path):
    if database_url.lower().startswith(("postgres://", "postgresql://")):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise SystemExit(
                "Install PostgreSQL dependencies first: python3 -m pip install psycopg[binary]"
            ) from exc
        return "postgres", psycopg.connect(database_url, row_factory=dict_row)
    return "sqlite", connect_sqlite(sqlite_path)


def fetch_fragrances(conn: Any) -> list[FragranceRow]:
    rows = conn.execute("SELECT slug, brand, name, image_url FROM fragrances ORDER BY brand, name").fetchall()
    fragrances: list[FragranceRow] = []
    for row in rows:
        row_dict = dict(row)
        key, loose_key = product_keys(row_dict["brand"], row_dict["name"])
        fragrances.append(
            FragranceRow(
                slug=row_dict["slug"],
                brand=row_dict["brand"],
                name=row_dict["name"],
                image_url=row_dict["image_url"],
                key=key,
                loose_key=loose_key,
            )
        )
    return fragrances


def index_unique(products: list[CatalogProduct], attr: str) -> dict[str, CatalogProduct]:
    grouped: dict[str, list[CatalogProduct]] = {}
    for product in products:
        grouped.setdefault(getattr(product, attr), []).append(product)
    return {key: values[0] for key, values in grouped.items() if key and len(values) == 1}


def match_products(
    fragrances: list[FragranceRow],
    products: list[CatalogProduct],
    *,
    min_fuzzy_score: float,
    preserve_local_assets: bool,
) -> tuple[list[Match], list[FragranceRow]]:
    exact_index = index_unique(products, "key")
    loose_index = index_unique(products, "loose_key")
    products_by_brand: dict[str, list[CatalogProduct]] = {}
    for product in products:
        brand_key = product.key.split("::", 1)[0]
        products_by_brand.setdefault(brand_key, []).append(product)

    matches: list[Match] = []
    skipped_local_assets: list[FragranceRow] = []
    matched_slugs: set[str] = set()

    for row in fragrances:
        if preserve_local_assets and row.image_url.startswith("/assets/"):
            skipped_local_assets.append(row)
            continue

        product = exact_index.get(row.key)
        if product:
            matches.append(Match(row, product, "exact", 1.0))
            matched_slugs.add(row.slug)
            continue

        product = loose_index.get(row.loose_key)
        if product:
            matches.append(Match(row, product, "loose", 0.98))
            matched_slugs.add(row.slug)
            continue

        brand_key, row_name_key = row.loose_key.split("::", 1)
        if not row_name_key:
            continue

        candidates = products_by_brand.get(brand_key, [])
        scored: list[tuple[float, CatalogProduct]] = []
        for candidate in candidates:
            candidate_name_key = candidate.loose_key.split("::", 1)[1]
            if not candidate_name_key:
                continue
            score = difflib.SequenceMatcher(None, row_name_key, candidate_name_key).ratio()
            if score >= min_fuzzy_score:
                scored.append((score, candidate))
        scored.sort(key=lambda item: item[0], reverse=True)
        if not scored:
            continue
        if len(scored) > 1 and scored[0][0] - scored[1][0] < 0.035:
            continue
        matches.append(Match(row, scored[0][1], "fuzzy", scored[0][0]))
        matched_slugs.add(row.slug)

    return matches, skipped_local_assets


def update_images(conn: Any, engine: str, matches: list[Match]) -> int:
    if engine == "postgres":
        placeholders = ("%s", "%s", "%s")
    else:
        placeholders = ("?", "?", "?")
    sql = f"""
        UPDATE fragrances
           SET image_url = {placeholders[0]},
               photo_icon_url = {placeholders[1]},
               artwork_kind = 'photo'
         WHERE slug = {placeholders[2]}
    """
    updated = 0
    for match in matches:
        cursor = conn.execute(sql, (match.product.image_url, match.product.image_url, match.row.slug))
        updated += cursor.rowcount or 0
    conn.commit()
    return updated


def print_match_report(matches: list[Match], limit: int) -> None:
    print(f"Matched {len(matches)} fragrances to MDP images.")
    if not matches:
        return
    methods = Counter(match.method for match in matches)
    print(
        "Match methods: "
        + ", ".join(f"{method}={count}" for method, count in sorted(methods.items()))
    )
    print()
    print("Sample matches:")
    for match in matches[:limit]:
        print(
            f"- [{match.method} {match.score:.2f}] "
            f"{match.row.brand} - {match.row.name} -> "
            f"{match.product.brand} - {match.product.name}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replace matching fragrance images with MDP India image URLs.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually update the database. Without this, only a dry-run report is printed.",
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL", "").strip(),
        help="PostgreSQL DATABASE_URL. Defaults to DATABASE_URL env var. If omitted, SQLite is used.",
    )
    parser.add_argument(
        "--sqlite-path",
        default=os.getenv("SQLITE_DATABASE_PATH", str(DEFAULT_SQLITE_PATH)),
        help="SQLite DB path when DATABASE_URL is not set.",
    )
    parser.add_argument(
        "--include-local-assets",
        action="store_true",
        help="Allow replacing current /assets/... images. Default preserves local custom images.",
    )
    parser.add_argument(
        "--min-fuzzy-score",
        type=float,
        default=0.94,
        help="Minimum fuzzy match score within the same brand.",
    )
    parser.add_argument("--max-pages", type=int, default=100, help="Maximum Shopify product pages to crawl.")
    parser.add_argument("--report-limit", type=int, default=40, help="Number of sample matches to print.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    sqlite_path = Path(args.sqlite_path)
    if not sqlite_path.is_absolute():
        sqlite_path = BASE_DIR / sqlite_path

    print("Fetching MDP catalog...")
    products = list(iter_mdp_products(args.max_pages))
    print(f"Fetched {len(products)} MDP products with images.")

    engine, conn = connect_database(args.database_url, sqlite_path)
    try:
        fragrances = fetch_fragrances(conn)
        print(f"Loaded {len(fragrances)} local fragrances from {engine}.")
        matches, skipped_local_assets = match_products(
            fragrances,
            products,
            min_fuzzy_score=args.min_fuzzy_score,
            preserve_local_assets=not args.include_local_assets,
        )
        print_match_report(matches, args.report_limit)
        if skipped_local_assets:
            print(f"\nPreserved {len(skipped_local_assets)} local /assets images.")
        if not args.apply:
            print("\nDry run only. Re-run with --apply to update the database.")
            return
        updated = update_images(conn, engine, matches)
        print(f"\nUpdated {updated} fragrance image rows.")
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
