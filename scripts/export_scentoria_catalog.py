from __future__ import annotations

import argparse
import csv
import json
import re
import time
from datetime import date
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
from urllib.request import Request, urlopen


BASE_URL = "https://scentoria.co.in"
CONCENTRATION_TAGS = {
    "EDC",
    "EDT",
    "EDP",
    "ELIXIR",
    "EXTRAIT",
    "PARFUM",
    "EXTRAIT DE PARFUM",
}
GENDER_TAGS = {"His", "Her", "Unisex", "Him"}
FAMILY_TAGS = {
    "Amber",
    "Aquatic/Fresh",
    "Chypre",
    "Citrus",
    "Floral",
    "Fougere/Aromatic",
    "Fruity",
    "Gourmand",
    "Oriental",
    "Woody",
}


def fetch_json(url: str) -> dict[str, Any]:
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 The-Scentist-Catalog-Export/1.0",
        },
    )
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def clean_html(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def full_url(url: str | None) -> str:
    if not url:
        return ""
    if url.startswith("//"):
        return f"https:{url}"
    return urljoin(BASE_URL, url)


def money_range(variants: list[dict[str, Any]]) -> tuple[str, str]:
    prices = []
    for variant in variants:
        try:
            prices.append(float(variant.get("price") or 0))
        except (TypeError, ValueError):
            pass
    if not prices:
        return "", ""
    return f"{min(prices):.2f}", f"{max(prices):.2f}"


def first_matching(tags: list[str], allowed: set[str]) -> str:
    for tag in tags:
        if tag in allowed:
            return tag
    return ""


def export_catalog(collection: str, out_dir: Path, delay: float) -> tuple[Path, Path, int, int]:
    products: list[dict[str, Any]] = []
    page = 1

    while True:
        url = f"{BASE_URL}/collections/{collection}/products.json?limit=250&page={page}"
        payload = fetch_json(url)
        batch = payload.get("products") or []
        if not batch:
            break
        products.extend(batch)
        print(f"Fetched page {page}: {len(batch)} products ({len(products)} total)")
        page += 1
        time.sleep(delay)

    today = date.today().isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    products_path = out_dir / f"scentoria_{collection}_products_{today}.csv"
    variants_path = out_dir / f"scentoria_{collection}_variants_{today}.csv"

    product_fields = [
        "product_id",
        "brand",
        "perfume_name",
        "handle",
        "product_type",
        "concentration",
        "gender",
        "family",
        "available",
        "variant_count",
        "available_variant_count",
        "min_price_inr",
        "max_price_inr",
        "product_url",
        "image_url",
        "tags",
        "description",
    ]
    variant_fields = [
        "product_id",
        "variant_id",
        "brand",
        "perfume_name",
        "variant_title",
        "size_or_format",
        "available",
        "price_inr",
        "compare_at_price_inr",
        "sku",
        "product_url",
        "variant_image_url",
    ]

    variant_count = 0
    with products_path.open("w", newline="", encoding="utf-8") as product_file, variants_path.open(
        "w", newline="", encoding="utf-8"
    ) as variant_file:
        product_writer = csv.DictWriter(product_file, fieldnames=product_fields)
        variant_writer = csv.DictWriter(variant_file, fieldnames=variant_fields)
        product_writer.writeheader()
        variant_writer.writeheader()

        for product in products:
            variants = product.get("variants") or []
            tags = product.get("tags") or []
            min_price, max_price = money_range(variants)
            image_url = full_url((product.get("images") or [{}])[0].get("src"))
            product_url = f"{BASE_URL}/products/{product.get('handle')}"
            available_variants = [variant for variant in variants if variant.get("available")]

            product_writer.writerow(
                {
                    "product_id": product.get("id"),
                    "brand": product.get("vendor", ""),
                    "perfume_name": product.get("title", ""),
                    "handle": product.get("handle", ""),
                    "product_type": product.get("product_type", ""),
                    "concentration": first_matching(tags, CONCENTRATION_TAGS),
                    "gender": first_matching(tags, GENDER_TAGS),
                    "family": first_matching(tags, FAMILY_TAGS),
                    "available": bool(available_variants),
                    "variant_count": len(variants),
                    "available_variant_count": len(available_variants),
                    "min_price_inr": min_price,
                    "max_price_inr": max_price,
                    "product_url": product_url,
                    "image_url": image_url,
                    "tags": " | ".join(tags),
                    "description": clean_html(product.get("body_html", "")),
                }
            )

            for variant in variants:
                variant_count += 1
                variant_image = variant.get("featured_image") or {}
                variant_writer.writerow(
                    {
                        "product_id": product.get("id"),
                        "variant_id": variant.get("id"),
                        "brand": product.get("vendor", ""),
                        "perfume_name": product.get("title", ""),
                        "variant_title": variant.get("title", ""),
                        "size_or_format": variant.get("option1") or variant.get("title", ""),
                        "available": variant.get("available"),
                        "price_inr": variant.get("price", ""),
                        "compare_at_price_inr": variant.get("compare_at_price", ""),
                        "sku": variant.get("sku", ""),
                        "product_url": product_url,
                        "variant_image_url": full_url(variant_image.get("src")),
                    }
                )

    return products_path, variants_path, len(products), variant_count


def main() -> None:
    parser = argparse.ArgumentParser(description="Export Scentoria Shopify catalog CSVs.")
    parser.add_argument("--collection", default="all", help="Shopify collection handle, default: all")
    parser.add_argument("--out-dir", default="exports", help="Directory for CSV exports")
    parser.add_argument("--delay", type=float, default=0.2, help="Delay between page requests")
    args = parser.parse_args()

    products_path, variants_path, product_count, variant_count = export_catalog(
        args.collection, Path(args.out_dir), args.delay
    )
    print(f"Products: {product_count} -> {products_path}")
    print(f"Variants: {variant_count} -> {variants_path}")


if __name__ == "__main__":
    main()
