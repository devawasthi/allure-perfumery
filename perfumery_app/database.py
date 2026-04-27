from __future__ import annotations

import json
import random
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from perfumery_app.catalog_seed import build_catalog_seed
from perfumery_app.config import Settings


class ValidationError(Exception):
    """Raised when client data fails validation."""


class DatabaseConnection:
    dialect = "unknown"

    def execute(self, sql: str, params: tuple[Any, ...] | list[Any] | None = None):
        raise NotImplementedError

    def commit(self) -> None:
        raise NotImplementedError

    def rollback(self) -> None:
        raise NotImplementedError

    def begin_write(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    def __enter__(self) -> "DatabaseConnection":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is not None:
            self.rollback()
        self.close()
        return False


class SQLiteConnection(DatabaseConnection):
    dialect = "sqlite"

    def __init__(self, raw: sqlite3.Connection):
        self.raw = raw

    def execute(self, sql: str, params: tuple[Any, ...] | list[Any] | None = None):
        return self.raw.execute(sql, tuple(params or ()))

    def commit(self) -> None:
        self.raw.commit()

    def rollback(self) -> None:
        self.raw.rollback()

    def begin_write(self) -> None:
        self.raw.execute("BEGIN IMMEDIATE")

    def close(self) -> None:
        self.raw.close()


class PostgresConnection(DatabaseConnection):
    dialect = "postgres"

    def __init__(self, raw: Any, *, pool: Any | None = None):
        self.raw = raw
        self.pool = pool

    def execute(self, sql: str, params: tuple[Any, ...] | list[Any] | None = None):
        return self.raw.execute(self._convert_sql(sql), tuple(params or ()))

    def commit(self) -> None:
        self.raw.commit()

    def rollback(self) -> None:
        self.raw.rollback()

    def begin_write(self) -> None:
        self.raw.execute("BEGIN")

    def close(self) -> None:
        try:
            self.raw.rollback()
        except Exception:
            pass
        if self.pool is not None:
            self.pool.putconn(self.raw)
        else:
            self.raw.close()

    @staticmethod
    def _convert_sql(sql: str) -> str:
        return sql.replace("?", "%s")


class SQLiteBackend:
    dialect = "sqlite"

    def __init__(self, db_path: Path, settings: Settings):
        self.db_path = Path(db_path)
        self.settings = settings
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> DatabaseConnection:
        connection = sqlite3.connect(
            self.db_path,
            timeout=self.settings.sqlite_busy_timeout_ms / 1000,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute(f"PRAGMA busy_timeout = {self.settings.sqlite_busy_timeout_ms}")
        connection.execute("PRAGMA synchronous = NORMAL")
        return SQLiteConnection(connection)


class PostgresBackend:
    dialect = "postgres"

    def __init__(self, settings: Settings):
        self.settings = settings
        self._pool = None
        self._pool_class = None
        self._psycopg = None
        self._dict_row = None

        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover - requires deployment dependency
            raise RuntimeError(
                "psycopg and psycopg-pool are required when DATABASE_URL points to PostgreSQL."
            ) from exc

        self._psycopg = psycopg
        self._dict_row = dict_row

        try:
            from psycopg_pool import ConnectionPool
        except ImportError:  # pragma: no cover - direct connections remain available
            self._pool_class = None
        else:
            self._pool_class = ConnectionPool

    def connect(self) -> DatabaseConnection:
        pool = self._get_pool()
        if pool is not None:
            raw = pool.getconn()
        else:
            raw = self._psycopg.connect(
                self.settings.resolved_database_url,
                connect_timeout=self.settings.database_connect_timeout_seconds,
                autocommit=False,
                row_factory=self._dict_row,
            )

        raw.execute(
            "SELECT set_config('statement_timeout', %s, false)",
            (str(self.settings.database_statement_timeout_ms),),
        )
        return PostgresConnection(raw, pool=pool)

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()

    def _get_pool(self):
        if self._pool_class is None:
            return None
        if self._pool is None:
            self._pool = self._pool_class(
                conninfo=self.settings.resolved_database_url,
                min_size=self.settings.database_pool_min_size,
                max_size=self.settings.database_pool_max_size,
                timeout=max(30, self.settings.database_connect_timeout_seconds),
                kwargs={
                    "autocommit": False,
                    "connect_timeout": self.settings.database_connect_timeout_seconds,
                    "row_factory": self._dict_row,
                },
            )
        return self._pool


class Database:
    def __init__(self, db_path: Path, settings: Settings):
        self.db_path = Path(db_path)
        self.settings = settings
        self.backend = self._build_backend()

    def _build_backend(self):
        if self.settings.database_engine == "postgres":
            return PostgresBackend(self.settings)
        return SQLiteBackend(self.db_path, self.settings)

    def connect(self) -> DatabaseConnection:
        return self.backend.connect()

    def initialize(self) -> None:
        with self.connect() as conn:
            for statement in self._schema_statements():
                conn.execute(statement)
            self._ensure_schema_upgrades(conn)
            if self.settings.auto_seed_catalog:
                self._sync_catalog(conn)
            conn.commit()

    def close(self) -> None:
        close = getattr(self.backend, "close", None)
        if callable(close):
            close()

    def ping(self) -> bool:
        with self.connect() as conn:
            row = conn.execute("SELECT 1 AS ok").fetchone()
            return bool(row and row["ok"] == 1)

    def _schema_statements(self) -> list[str]:
        if self.backend.dialect == "postgres":
            return self._postgres_schema_statements()
        return self._sqlite_schema_statements()

    def _sqlite_schema_statements(self) -> list[str]:
        return [
            """
            CREATE TABLE IF NOT EXISTS fragrances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT NOT NULL UNIQUE,
                brand TEXT NOT NULL,
                name TEXT NOT NULL,
                collection_type TEXT NOT NULL,
                gender TEXT NOT NULL,
                family TEXT NOT NULL,
                concentration TEXT NOT NULL,
                origin TEXT NOT NULL,
                description TEXT NOT NULL,
                signature TEXT NOT NULL,
                top_notes TEXT NOT NULL,
                heart_notes TEXT NOT NULL,
                base_notes TEXT NOT NULL,
                accent_from TEXT NOT NULL,
                accent_to TEXT NOT NULL,
                image_url TEXT NOT NULL DEFAULT '',
                artwork_kind TEXT NOT NULL DEFAULT 'generated',
                bottle_size_ml INTEGER NOT NULL,
                featured INTEGER NOT NULL DEFAULT 0,
                rank INTEGER NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS variants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fragrance_id INTEGER NOT NULL,
                sku TEXT NOT NULL UNIQUE,
                sale_type TEXT NOT NULL,
                size_label TEXT NOT NULL,
                size_ml INTEGER NOT NULL,
                price_inr INTEGER NOT NULL,
                stock_units INTEGER NOT NULL,
                badge TEXT NOT NULL,
                statement TEXT NOT NULL,
                FOREIGN KEY(fragrance_id) REFERENCES fragrances(id) ON DELETE CASCADE
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_number TEXT NOT NULL UNIQUE,
                customer_name TEXT NOT NULL,
                email TEXT NOT NULL,
                phone TEXT NOT NULL,
                shipping_line1 TEXT NOT NULL,
                shipping_line2 TEXT NOT NULL,
                city TEXT NOT NULL,
                state TEXT NOT NULL,
                postal_code TEXT NOT NULL,
                country TEXT NOT NULL,
                payment_method TEXT NOT NULL,
                payment_gateway TEXT NOT NULL DEFAULT '',
                payment_status TEXT NOT NULL DEFAULT '',
                gateway_order_id TEXT NOT NULL DEFAULT '',
                gateway_payment_id TEXT NOT NULL DEFAULT '',
                gateway_signature TEXT NOT NULL DEFAULT '',
                payment_amount_inr INTEGER NOT NULL DEFAULT 0,
                delivery_notes TEXT NOT NULL,
                subtotal_inr INTEGER NOT NULL,
                shipping_inr INTEGER NOT NULL,
                total_inr INTEGER NOT NULL,
                item_count INTEGER NOT NULL,
                status TEXT NOT NULL,
                initiated_at TEXT NOT NULL DEFAULT '',
                paid_at TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS order_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL,
                variant_id INTEGER NOT NULL,
                fragrance_slug TEXT NOT NULL,
                fragrance_name TEXT NOT NULL,
                brand TEXT NOT NULL,
                sale_type TEXT NOT NULL,
                size_label TEXT NOT NULL,
                price_inr INTEGER NOT NULL,
                quantity INTEGER NOT NULL,
                line_total_inr INTEGER NOT NULL,
                FOREIGN KEY(order_id) REFERENCES orders(id) ON DELETE CASCADE,
                FOREIGN KEY(variant_id) REFERENCES variants(id)
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_fragrances_brand ON fragrances (brand)",
            "CREATE INDEX IF NOT EXISTS idx_fragrances_collection_type ON fragrances (collection_type)",
            "CREATE INDEX IF NOT EXISTS idx_fragrances_gender ON fragrances (gender)",
            "CREATE INDEX IF NOT EXISTS idx_variants_fragrance_id ON variants (fragrance_id)",
            "CREATE INDEX IF NOT EXISTS idx_variants_sale_type ON variants (sale_type)",
            "CREATE INDEX IF NOT EXISTS idx_orders_created_at ON orders (created_at)",
            "CREATE INDEX IF NOT EXISTS idx_orders_payment_status ON orders (payment_status)",
            "CREATE INDEX IF NOT EXISTS idx_orders_gateway_order_id ON orders (gateway_order_id)",
            "CREATE INDEX IF NOT EXISTS idx_order_items_order_id ON order_items (order_id)",
        ]

    def _postgres_schema_statements(self) -> list[str]:
        return [
            """
            CREATE TABLE IF NOT EXISTS fragrances (
                id BIGSERIAL PRIMARY KEY,
                slug TEXT NOT NULL UNIQUE,
                brand TEXT NOT NULL,
                name TEXT NOT NULL,
                collection_type TEXT NOT NULL,
                gender TEXT NOT NULL,
                family TEXT NOT NULL,
                concentration TEXT NOT NULL,
                origin TEXT NOT NULL,
                description TEXT NOT NULL,
                signature TEXT NOT NULL,
                top_notes TEXT NOT NULL,
                heart_notes TEXT NOT NULL,
                base_notes TEXT NOT NULL,
                accent_from TEXT NOT NULL,
                accent_to TEXT NOT NULL,
                image_url TEXT NOT NULL DEFAULT '',
                artwork_kind TEXT NOT NULL DEFAULT 'generated',
                bottle_size_ml INTEGER NOT NULL,
                featured SMALLINT NOT NULL DEFAULT 0,
                rank INTEGER NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS variants (
                id BIGSERIAL PRIMARY KEY,
                fragrance_id BIGINT NOT NULL REFERENCES fragrances(id) ON DELETE CASCADE,
                sku TEXT NOT NULL UNIQUE,
                sale_type TEXT NOT NULL,
                size_label TEXT NOT NULL,
                size_ml INTEGER NOT NULL,
                price_inr INTEGER NOT NULL,
                stock_units INTEGER NOT NULL,
                badge TEXT NOT NULL,
                statement TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS orders (
                id BIGSERIAL PRIMARY KEY,
                order_number TEXT NOT NULL UNIQUE,
                customer_name TEXT NOT NULL,
                email TEXT NOT NULL,
                phone TEXT NOT NULL,
                shipping_line1 TEXT NOT NULL,
                shipping_line2 TEXT NOT NULL,
                city TEXT NOT NULL,
                state TEXT NOT NULL,
                postal_code TEXT NOT NULL,
                country TEXT NOT NULL,
                payment_method TEXT NOT NULL,
                payment_gateway TEXT NOT NULL DEFAULT '',
                payment_status TEXT NOT NULL DEFAULT '',
                gateway_order_id TEXT NOT NULL DEFAULT '',
                gateway_payment_id TEXT NOT NULL DEFAULT '',
                gateway_signature TEXT NOT NULL DEFAULT '',
                payment_amount_inr INTEGER NOT NULL DEFAULT 0,
                delivery_notes TEXT NOT NULL,
                subtotal_inr INTEGER NOT NULL,
                shipping_inr INTEGER NOT NULL,
                total_inr INTEGER NOT NULL,
                item_count INTEGER NOT NULL,
                status TEXT NOT NULL,
                initiated_at TEXT NOT NULL DEFAULT '',
                paid_at TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS order_items (
                id BIGSERIAL PRIMARY KEY,
                order_id BIGINT NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
                variant_id BIGINT NOT NULL REFERENCES variants(id),
                fragrance_slug TEXT NOT NULL,
                fragrance_name TEXT NOT NULL,
                brand TEXT NOT NULL,
                sale_type TEXT NOT NULL,
                size_label TEXT NOT NULL,
                price_inr INTEGER NOT NULL,
                quantity INTEGER NOT NULL,
                line_total_inr INTEGER NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_fragrances_brand ON fragrances (brand)",
            "CREATE INDEX IF NOT EXISTS idx_fragrances_collection_type ON fragrances (collection_type)",
            "CREATE INDEX IF NOT EXISTS idx_fragrances_gender ON fragrances (gender)",
            "CREATE INDEX IF NOT EXISTS idx_variants_fragrance_id ON variants (fragrance_id)",
            "CREATE INDEX IF NOT EXISTS idx_variants_sale_type ON variants (sale_type)",
            "CREATE INDEX IF NOT EXISTS idx_orders_created_at ON orders (created_at)",
            "CREATE INDEX IF NOT EXISTS idx_orders_payment_status ON orders (payment_status)",
            "CREATE INDEX IF NOT EXISTS idx_orders_gateway_order_id ON orders (gateway_order_id)",
            "CREATE INDEX IF NOT EXISTS idx_order_items_order_id ON order_items (order_id)",
        ]

    def _ensure_schema_upgrades(self, conn: DatabaseConnection) -> None:
        self._ensure_columns(
            conn,
            "fragrances",
            {
                "image_url": "TEXT NOT NULL DEFAULT ''",
                "artwork_kind": "TEXT NOT NULL DEFAULT 'generated'",
            },
        )
        self._ensure_columns(
            conn,
            "orders",
            {
                "payment_gateway": "TEXT NOT NULL DEFAULT ''",
                "payment_status": "TEXT NOT NULL DEFAULT ''",
                "gateway_order_id": "TEXT NOT NULL DEFAULT ''",
                "gateway_payment_id": "TEXT NOT NULL DEFAULT ''",
                "gateway_signature": "TEXT NOT NULL DEFAULT ''",
                "payment_amount_inr": "INTEGER NOT NULL DEFAULT 0",
                "initiated_at": "TEXT NOT NULL DEFAULT ''",
                "paid_at": "TEXT NOT NULL DEFAULT ''",
                "last_error": "TEXT NOT NULL DEFAULT ''",
            },
        )

    def _ensure_columns(
        self,
        conn: DatabaseConnection,
        table_name: str,
        expected_columns: dict[str, str],
    ) -> None:
        if conn.dialect == "postgres":
            for column, ddl in expected_columns.items():
                conn.execute(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS {column} {ddl}")
            return

        existing = {
            row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        for column, ddl in expected_columns.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column} {ddl}")

    def _sync_catalog(self, conn: DatabaseConnection) -> None:
        for item in build_catalog_seed():
            fragrance_id = self._upsert_catalog_item(conn, item)
            self._sync_variants(conn, fragrance_id, item["variants"])

    def _upsert_catalog_item(self, conn: DatabaseConnection, item: dict[str, Any]) -> int:
        conn.execute(
            """
            INSERT INTO fragrances (
                slug, brand, name, collection_type, gender, family, concentration,
                origin, description, signature, top_notes, heart_notes, base_notes,
                accent_from, accent_to, image_url, artwork_kind, bottle_size_ml, featured, rank
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                artwork_kind = excluded.artwork_kind,
                bottle_size_ml = excluded.bottle_size_ml,
                featured = excluded.featured,
                rank = excluded.rank
            """,
            (
                item["slug"],
                item["brand"],
                item["name"],
                item["collection_type"],
                item["gender"],
                item["family"],
                item["concentration"],
                item["origin"],
                self._build_description(item),
                item["signature"],
                json.dumps(item["top_notes"]),
                json.dumps(item["heart_notes"]),
                json.dumps(item["base_notes"]),
                item["colors"][0],
                item["colors"][1],
                item["image_url"],
                item["artwork_kind"],
                item["bottle_size_ml"],
                1 if item["featured"] else 0,
                item["rank"],
            ),
        )
        row = conn.execute("SELECT id FROM fragrances WHERE slug = ?", (item["slug"],)).fetchone()
        if row is None:
            raise ValidationError(f"Catalog item could not be loaded after sync: {item['slug']}")
        return int(row["id"])

    def _sync_variants(
        self,
        conn: DatabaseConnection,
        fragrance_id: int,
        variants: list[dict[str, Any]],
    ) -> None:
        keep_skus = [variant["sku"] for variant in variants]
        for variant in variants:
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
                    variant["sku"],
                    variant["sale_type"],
                    variant["size_label"],
                    variant["size_ml"],
                    variant["price_inr"],
                    variant["stock_units"],
                    variant["badge"],
                    variant["statement"],
                ),
            )

        if keep_skus:
            placeholders = ", ".join("?" for _ in keep_skus)
            conn.execute(
                f"DELETE FROM variants WHERE fragrance_id = ? AND sku NOT IN ({placeholders})",
                [fragrance_id, *keep_skus],
            )

    def _build_description(self, item: dict[str, Any]) -> str:
        top = ", ".join(item["top_notes"][:2])
        base = ", ".join(item["base_notes"][:2])
        return (
            f"{item['brand']} {item['name']} opens with {top} and settles into {base}. "
            f"{item['signature']}"
        )

    def list_filters(self) -> dict[str, list[str]]:
        with self.connect() as conn:
            brands = [row["brand"] for row in conn.execute("SELECT DISTINCT brand FROM fragrances ORDER BY brand")]
            families = [row["family"] for row in conn.execute("SELECT DISTINCT family FROM fragrances ORDER BY family")]
            collections = [
                row["collection_type"]
                for row in conn.execute("SELECT DISTINCT collection_type FROM fragrances ORDER BY collection_type")
            ]
            sale_types = [row["sale_type"] for row in conn.execute("SELECT DISTINCT sale_type FROM variants ORDER BY sale_type")]

        return {
            "brands": brands,
            "families": families,
            "collections": collections,
            "sale_types": sale_types,
            "genders": ["him", "her", "unisex"],
        }

    def list_fragrances(
        self,
        filters: dict[str, str] | None = None,
        *,
        limit: int | None = None,
        featured_only: bool = False,
    ) -> list[dict[str, Any]]:
        filters = {key: value for key, value in (filters or {}).items() if value}
        clauses = ["1 = 1"]
        params: list[Any] = []

        if featured_only:
            clauses.append("featured = 1")

        search = filters.get("search")
        if search:
            like = f"%{search.lower()}%"
            clauses.append("(LOWER(name) LIKE ? OR LOWER(brand) LIKE ? OR LOWER(description) LIKE ?)")
            params.extend([like, like, like])

        if filters.get("gender"):
            clauses.append("gender = ?")
            params.append(filters["gender"])

        if filters.get("brand"):
            clauses.append("brand = ?")
            params.append(filters["brand"])

        if filters.get("collection_type"):
            clauses.append("collection_type = ?")
            params.append(filters["collection_type"])

        if filters.get("family"):
            clauses.append("family = ?")
            params.append(filters["family"])

        if filters.get("sale_type"):
            clauses.append(
                "EXISTS (SELECT 1 FROM variants v WHERE v.fragrance_id = fragrances.id AND v.sale_type = ? AND v.stock_units > 0)"
            )
            params.append(filters["sale_type"])

        query = f"""
            SELECT *
            FROM fragrances
            WHERE {' AND '.join(clauses)}
            ORDER BY featured DESC, rank ASC, brand ASC, name ASC
        """

        if limit:
            query += " LIMIT ?"
            params.append(limit)

        with self.connect() as conn:
            fragrance_rows = conn.execute(query, params).fetchall()
            fragrance_ids = [row["id"] for row in fragrance_rows]
            variants_by_fragrance = self._variants_by_fragrance(conn, fragrance_ids)

        return [self._serialize_fragrance(row, variants_by_fragrance.get(row["id"], [])) for row in fragrance_rows]

    def get_featured(self, limit: int = 8) -> list[dict[str, Any]]:
        return self.list_fragrances(limit=limit, featured_only=True)

    def get_fragrance(self, slug: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM fragrances WHERE slug = ?", (slug,)).fetchone()
            if row is None:
                return None
            variants = self._variants_by_fragrance(conn, [row["id"]]).get(row["id"], [])
            return self._serialize_fragrance(row, variants)

    def get_related_fragrances(self, fragrance: dict[str, Any], limit: int = 3) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM fragrances
                WHERE slug != ?
                  AND (brand = ? OR gender = ? OR family = ?)
                ORDER BY featured DESC, rank ASC
                LIMIT ?
                """,
                (
                    fragrance["slug"],
                    fragrance["brand"],
                    fragrance["gender"],
                    fragrance["family"],
                    limit,
                ),
            ).fetchall()
            ids = [row["id"] for row in rows]
            variants = self._variants_by_fragrance(conn, ids)
            return [self._serialize_fragrance(row, variants.get(row["id"], [])) for row in rows]

    def get_metrics(self) -> dict[str, int]:
        with self.connect() as conn:
            fragrances = conn.execute("SELECT COUNT(*) AS count FROM fragrances").fetchone()["count"]
            brands = conn.execute("SELECT COUNT(DISTINCT brand) AS count FROM fragrances").fetchone()["count"]
            variants = conn.execute("SELECT COUNT(*) AS count FROM variants").fetchone()["count"]
            orders = conn.execute("SELECT COUNT(*) AS count FROM orders").fetchone()["count"]
        return {
            "fragrances": fragrances,
            "brands": brands,
            "variants": variants,
            "orders": orders,
        }

    def get_brand_showcase(self, limit: int = 12) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT brand, collection_type, COUNT(*) AS fragrance_count
                FROM fragrances
                GROUP BY brand, collection_type
                ORDER BY collection_type ASC, fragrance_count DESC, brand ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_cart_items(self, variant_ids: list[int]) -> list[dict[str, Any]]:
        if not variant_ids:
            return []

        placeholders = ", ".join("?" for _ in variant_ids)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    v.id AS variant_id,
                    v.sale_type,
                    v.size_label,
                    v.size_ml,
                    v.price_inr,
                    v.stock_units,
                    v.badge,
                    f.slug,
                    f.brand,
                    f.name,
                    f.collection_type,
                    f.gender,
                    f.family,
                    f.accent_from,
                    f.accent_to,
                    f.image_url,
                    f.artwork_kind
                FROM variants v
                JOIN fragrances f ON f.id = v.fragrance_id
                WHERE v.id IN ({placeholders})
                ORDER BY f.rank ASC, v.price_inr ASC
                """,
                variant_ids,
            ).fetchall()
        return [dict(row) for row in rows]

    def preview_order(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        return self._build_checkout_snapshot(items)

    def create_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        customer = payload.get("customer") or {}
        items = payload.get("items") or []
        self._validate_customer(customer)
        snapshot = self._build_checkout_snapshot(items)

        now = self._now()
        payment_method = customer.get("payment_method", "Cash on Delivery")
        payment_status = "pending" if payment_method == "Bank Transfer" else "offline"
        status = "Awaiting Transfer" if payment_method == "Bank Transfer" else "Confirmed"

        with self.connect() as conn:
            try:
                conn.begin_write()
                self._deduct_stock(conn, snapshot["items"])
                order_number = self._insert_order(
                    conn,
                    customer=customer,
                    snapshot=snapshot,
                    payment_method=payment_method,
                    payment_gateway="manual",
                    payment_status=payment_status,
                    status=status,
                    created_at=now,
                    initiated_at=now,
                    paid_at="",
                    gateway_order_id="",
                    gateway_payment_id="",
                    gateway_signature="",
                    payment_amount_inr=0,
                    last_error="",
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

        order = self.get_order(order_number)
        if order is None:
            raise ValidationError("Order could not be loaded after creation.")
        return order

    def create_pending_razorpay_order(
        self,
        *,
        customer: dict[str, Any],
        items: list[dict[str, Any]],
        gateway_order_id: str,
    ) -> dict[str, Any]:
        self._validate_customer(customer)
        snapshot = self._build_checkout_snapshot(items)
        now = self._now()

        with self.connect() as conn:
            try:
                conn.begin_write()
                cursor_order_number = self._insert_order(
                    conn,
                    customer=customer,
                    snapshot=snapshot,
                    payment_method="Razorpay",
                    payment_gateway="razorpay",
                    payment_status="created",
                    status="Pending Payment",
                    created_at=now,
                    initiated_at=now,
                    paid_at="",
                    gateway_order_id=gateway_order_id,
                    gateway_payment_id="",
                    gateway_signature="",
                    payment_amount_inr=0,
                    last_error="",
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

        order = self.get_order(cursor_order_number)
        if order is None:
            raise ValidationError("Payment order could not be loaded after creation.")
        return order

    def finalize_razorpay_order(
        self,
        *,
        order_number: str,
        gateway_order_id: str,
        gateway_payment_id: str,
        gateway_signature: str,
    ) -> dict[str, Any]:
        with self.connect() as conn:
            try:
                conn.begin_write()
                order_query = "SELECT * FROM orders WHERE order_number = ?"
                if conn.dialect == "postgres":
                    order_query += " FOR UPDATE"

                order_row = conn.execute(order_query, (order_number,)).fetchone()
                if order_row is None:
                    raise ValidationError("Pending order not found.")

                if order_row["gateway_order_id"] != gateway_order_id:
                    raise ValidationError("Razorpay order mismatch detected.")

                if order_row["payment_status"] == "paid":
                    conn.commit()
                    order = self.get_order(order_number)
                    if order is None:
                        raise ValidationError("Order is missing after payment confirmation.")
                    return order

                item_rows = conn.execute(
                    """
                    SELECT variant_id, quantity
                    FROM order_items
                    WHERE order_id = ?
                    ORDER BY id ASC
                    """,
                    (order_row["id"],),
                ).fetchall()
                self._deduct_stock(conn, [dict(row) for row in item_rows], compact=True)

                conn.execute(
                    """
                    UPDATE orders
                    SET payment_status = 'paid',
                        status = 'Confirmed',
                        gateway_payment_id = ?,
                        gateway_signature = ?,
                        payment_amount_inr = total_inr,
                        paid_at = ?,
                        last_error = ''
                    WHERE id = ?
                    """,
                    (
                        gateway_payment_id,
                        gateway_signature,
                        self._now(),
                        order_row["id"],
                    ),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

        order = self.get_order(order_number)
        if order is None:
            raise ValidationError("Order could not be loaded after payment.")
        return order

    def finalize_razorpay_order_from_webhook(
        self,
        *,
        gateway_order_id: str,
        gateway_payment_id: str,
        paid_amount_subunits: int,
    ) -> dict[str, Any] | None:
        with self.connect() as conn:
            try:
                conn.begin_write()
                order_query = "SELECT * FROM orders WHERE gateway_order_id = ?"
                if conn.dialect == "postgres":
                    order_query += " FOR UPDATE"

                order_row = conn.execute(order_query, (gateway_order_id,)).fetchone()
                if order_row is None:
                    conn.rollback()
                    return None

                if order_row["payment_status"] == "paid":
                    conn.commit()
                    return self.get_order(order_row["order_number"])

                if paid_amount_subunits and paid_amount_subunits != order_row["total_inr"] * 100:
                    raise ValidationError("Webhook payment amount does not match the local order total.")

                item_rows = conn.execute(
                    "SELECT variant_id, quantity FROM order_items WHERE order_id = ? ORDER BY id ASC",
                    (order_row["id"],),
                ).fetchall()
                self._deduct_stock(conn, [dict(row) for row in item_rows], compact=True)
                conn.execute(
                    """
                    UPDATE orders
                    SET payment_status = 'paid',
                        status = 'Confirmed',
                        gateway_payment_id = ?,
                        payment_amount_inr = total_inr,
                        paid_at = ?,
                        last_error = ''
                    WHERE id = ?
                    """,
                    (
                        gateway_payment_id,
                        self._now(),
                        order_row["id"],
                    ),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

        return self.get_order(order_row["order_number"])

    def mark_payment_failure(self, order_number: str, reason: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE orders
                SET payment_status = 'failed',
                    status = 'Payment Failed',
                    last_error = ?
                WHERE order_number = ? AND payment_status != 'paid'
                """,
                (reason[:400], order_number),
            )
            conn.commit()

    def get_order(self, order_number: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            order_row = conn.execute("SELECT * FROM orders WHERE order_number = ?", (order_number,)).fetchone()
            if order_row is None:
                return None
            item_rows = conn.execute(
                """
                SELECT fragrance_slug, fragrance_name, brand, sale_type, size_label,
                       price_inr, quantity, line_total_inr
                FROM order_items
                WHERE order_id = ?
                ORDER BY id ASC
                """,
                (order_row["id"],),
            ).fetchall()

        order = dict(order_row)
        order["items"] = [dict(row) for row in item_rows]
        order["can_retry_payment"] = order["payment_gateway"] == "razorpay" and order["payment_status"] != "paid"
        return order

    def _build_checkout_snapshot(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        if not items:
            raise ValidationError("Your cart is empty.")

        with self.connect() as conn:
            normalized_items: list[dict[str, Any]] = []
            subtotal = 0

            for item in items:
                variant_id = int(item.get("variant_id", 0))
                quantity = int(item.get("quantity", 0))
                if quantity < 1:
                    raise ValidationError("Every cart item must have quantity of at least 1.")

                row = conn.execute(
                    """
                    SELECT
                        v.id AS variant_id,
                        v.sale_type,
                        v.size_label,
                        v.price_inr,
                        v.stock_units,
                        f.slug,
                        f.brand,
                        f.name
                    FROM variants v
                    JOIN fragrances f ON f.id = v.fragrance_id
                    WHERE v.id = ?
                    """,
                    (variant_id,),
                ).fetchone()

                if row is None:
                    raise ValidationError("A selected fragrance option no longer exists.")

                if row["stock_units"] < quantity:
                    raise ValidationError(
                        f"Only {row['stock_units']} unit(s) left for {row['name']} {row['size_label']}."
                    )

                line_total = row["price_inr"] * quantity
                subtotal += line_total
                normalized_items.append(
                    {
                        "variant_id": row["variant_id"],
                        "fragrance_slug": row["slug"],
                        "fragrance_name": row["name"],
                        "brand": row["brand"],
                        "sale_type": row["sale_type"],
                        "size_label": row["size_label"],
                        "price_inr": row["price_inr"],
                        "quantity": quantity,
                        "line_total_inr": line_total,
                    }
                )

        shipping = 0 if subtotal >= self.settings.free_shipping_threshold_inr else self.settings.shipping_fee_inr
        total = subtotal + shipping
        return {
            "items": normalized_items,
            "subtotal_inr": subtotal,
            "shipping_inr": shipping,
            "total_inr": total,
            "item_count": sum(item["quantity"] for item in normalized_items),
        }

    def _insert_order(
        self,
        conn: DatabaseConnection,
        *,
        customer: dict[str, Any],
        snapshot: dict[str, Any],
        payment_method: str,
        payment_gateway: str,
        payment_status: str,
        status: str,
        created_at: str,
        initiated_at: str,
        paid_at: str,
        gateway_order_id: str,
        gateway_payment_id: str,
        gateway_signature: str,
        payment_amount_inr: int,
        last_error: str,
    ) -> str:
        order_number = self._generate_order_number(conn)
        conn.execute(
            """
            INSERT INTO orders (
                order_number, customer_name, email, phone, shipping_line1, shipping_line2,
                city, state, postal_code, country, payment_method, payment_gateway, payment_status,
                gateway_order_id, gateway_payment_id, gateway_signature, payment_amount_inr,
                delivery_notes, subtotal_inr, shipping_inr, total_inr, item_count, status,
                initiated_at, paid_at, last_error, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order_number,
                customer["customer_name"],
                customer["email"],
                customer["phone"],
                customer["shipping_line1"],
                customer.get("shipping_line2", ""),
                customer["city"],
                customer["state"],
                customer["postal_code"],
                customer.get("country", self.settings.default_country),
                payment_method,
                payment_gateway,
                payment_status,
                gateway_order_id,
                gateway_payment_id,
                gateway_signature,
                payment_amount_inr,
                customer.get("delivery_notes", ""),
                snapshot["subtotal_inr"],
                snapshot["shipping_inr"],
                snapshot["total_inr"],
                snapshot["item_count"],
                status,
                initiated_at,
                paid_at,
                last_error,
                created_at,
            ),
        )
        order_row = conn.execute("SELECT id FROM orders WHERE order_number = ?", (order_number,)).fetchone()
        if order_row is None:
            raise ValidationError("Order header could not be loaded after creation.")
        order_id = order_row["id"]

        for item in snapshot["items"]:
            conn.execute(
                """
                INSERT INTO order_items (
                    order_id, variant_id, fragrance_slug, fragrance_name, brand, sale_type,
                    size_label, price_inr, quantity, line_total_inr
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_id,
                    item["variant_id"],
                    item["fragrance_slug"],
                    item["fragrance_name"],
                    item["brand"],
                    item["sale_type"],
                    item["size_label"],
                    item["price_inr"],
                    item["quantity"],
                    item["line_total_inr"],
                ),
            )
        return order_number

    def _deduct_stock(
        self,
        conn: DatabaseConnection,
        items: list[dict[str, Any]],
        *,
        compact: bool = False,
    ) -> None:
        for item in items:
            variant_id = item["variant_id"]
            quantity = item["quantity"]
            lookup_query = """
                SELECT v.stock_units, f.name, v.size_label
                FROM variants v
                JOIN fragrances f ON f.id = v.fragrance_id
                WHERE v.id = ?
            """
            if conn.dialect == "postgres":
                lookup_query += " FOR UPDATE"

            row = conn.execute(lookup_query, (variant_id,)).fetchone()
            if row is None:
                raise ValidationError("A selected fragrance option no longer exists.")
            if row["stock_units"] < quantity:
                raise ValidationError(
                    f"Only {row['stock_units']} unit(s) left for {row['name']} {row['size_label']}."
                )

            update_cursor = conn.execute(
                "UPDATE variants SET stock_units = stock_units - ? WHERE id = ? AND stock_units >= ?",
                (quantity, variant_id, quantity),
            )
            if getattr(update_cursor, "rowcount", 0) != 1:
                raise ValidationError(
                    f"Stock changed while confirming {row['name']} {row['size_label']}. Please try again."
                )

    def _variants_by_fragrance(
        self, conn: DatabaseConnection, fragrance_ids: list[int]
    ) -> dict[int, list[dict[str, Any]]]:
        if not fragrance_ids:
            return {}

        placeholders = ", ".join("?" for _ in fragrance_ids)
        rows = conn.execute(
            f"""
            SELECT *
            FROM variants
            WHERE fragrance_id IN ({placeholders})
            ORDER BY
                CASE sale_type
                    WHEN 'retail' THEN 1
                    WHEN 'tester' THEN 2
                    WHEN 'decant' THEN 3
                    WHEN 'partial' THEN 4
                    ELSE 5
                END,
                size_ml ASC,
                price_inr ASC
            """,
            fragrance_ids,
        ).fetchall()

        grouped: dict[int, list[dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(row["fragrance_id"], []).append(dict(row))
        return grouped

    def _serialize_fragrance(self, row: Any, variants: list[dict[str, Any]]) -> dict[str, Any]:
        top_notes = json.loads(row["top_notes"])
        heart_notes = json.loads(row["heart_notes"])
        base_notes = json.loads(row["base_notes"])
        available_variants = [variant for variant in variants if variant["stock_units"] > 0]
        starting_price = min((variant["price_inr"] for variant in available_variants), default=0)
        quick_add = next(
            (
                variant
                for variant in available_variants
                if variant["sale_type"] == "decant" and variant["size_ml"] == 10
            ),
            available_variants[0] if available_variants else None,
        )

        return {
            "id": row["id"],
            "slug": row["slug"],
            "brand": row["brand"],
            "name": row["name"],
            "collection_type": row["collection_type"],
            "gender": row["gender"],
            "family": row["family"],
            "concentration": row["concentration"],
            "origin": row["origin"],
            "description": row["description"],
            "signature": row["signature"],
            "top_notes": top_notes,
            "heart_notes": heart_notes,
            "base_notes": base_notes,
            "accent_from": row["accent_from"],
            "accent_to": row["accent_to"],
            "image_url": row["image_url"],
            "image_alt": f"{row['brand']} {row['name']} artwork",
            "artwork_kind": row["artwork_kind"],
            "bottle_size_ml": row["bottle_size_ml"],
            "featured": bool(row["featured"]),
            "variants": available_variants,
            "starting_price": starting_price,
            "quick_add_variant": quick_add,
            "sale_types": sorted({variant["sale_type"] for variant in available_variants}),
        }

    def _validate_customer(self, customer: dict[str, Any]) -> None:
        required_fields = [
            "customer_name",
            "email",
            "phone",
            "shipping_line1",
            "city",
            "state",
            "postal_code",
            "payment_method",
        ]
        for field in required_fields:
            if not str(customer.get(field, "")).strip():
                raise ValidationError(f"{field.replace('_', ' ').title()} is required.")

        email = customer["email"].strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise ValidationError("Please enter a valid email address.")

        phone = re.sub(r"\D+", "", customer["phone"])
        if len(phone) < 10:
            raise ValidationError("Please enter a valid phone number.")
        customer["phone"] = phone

        postal = re.sub(r"\s+", "", customer["postal_code"])
        if len(postal) < 5:
            raise ValidationError("Please enter a valid postal code.")
        customer["postal_code"] = postal

        customer["customer_name"] = customer["customer_name"].strip()
        customer["email"] = email
        customer["shipping_line1"] = customer["shipping_line1"].strip()
        customer["shipping_line2"] = str(customer.get("shipping_line2", "")).strip()
        customer["city"] = customer["city"].strip()
        customer["state"] = customer["state"].strip()
        customer["country"] = (
            str(customer.get("country", self.settings.default_country)).strip() or self.settings.default_country
        )
        customer["delivery_notes"] = str(customer.get("delivery_notes", "")).strip()
        customer["payment_method"] = customer["payment_method"].strip()

    def _generate_order_number(self, conn: DatabaseConnection) -> str:
        while True:
            stamp = datetime.utcnow().strftime("%Y%m%d")
            suffix = random.randint(1000, 9999)
            order_number = f"{self.settings.order_prefix}-{stamp}-{suffix}"
            exists = conn.execute(
                "SELECT 1 FROM orders WHERE order_number = ? LIMIT 1",
                (order_number,),
            ).fetchone()
            if not exists:
                return order_number

    def _now(self) -> str:
        return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
