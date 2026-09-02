from __future__ import annotations

from difflib import SequenceMatcher
import json
import re
import secrets
import sqlite3
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

from perfumery_app.catalog_seed import build_catalog_seed, slugify
from perfumery_app.config import Settings


class ValidationError(Exception):
    """Raised when client data fails validation."""


MIN_PUBLIC_VARIANT_SIZE_ML = 5


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
        return sql.replace("%", "%%").replace("?", "%s")


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
        self._search_vocabulary: list[tuple[str, str, str]] | None = None

    def _build_backend(self):
        if self.settings.database_engine == "postgres":
            return PostgresBackend(self.settings)
        return SQLiteBackend(self.db_path, self.settings)

    def connect(self) -> DatabaseConnection:
        return self.backend.connect()

    def initialize(self) -> None:
        with self.connect() as conn:
            self._prepare_initialization_connection(conn)
            for statement in self._schema_statements():
                conn.execute(statement)
            self._ensure_schema_upgrades(conn)
            self._upgrade_asset_urls(conn)
            if self.settings.auto_seed_catalog:
                self._sync_catalog(conn)
            conn.commit()

    def _upgrade_asset_urls(self, conn: DatabaseConnection) -> None:
        optimized_assets = {
            "/assets/creed-aventus.png": "/assets/creed-aventus.webp",
            "/assets/bleu-de-chanel-parfum.png": "/assets/bleu-de-chanel-parfum.webp",
            "/assets/paco-rabanne-1-million-elixir.png": "/assets/paco-rabanne-1-million-elixir.webp",
            "/assets/paco-rabanne-1-million.png": "/assets/paco-rabanne-1-million.webp",
            "/assets/paco-rabanne-1-million-lucky.png": "/assets/paco-rabanne-1-million-lucky.webp",
            "/assets/paco-rabanne-1-million-golden-oud.png": "/assets/paco-rabanne-1-million-golden-oud.webp",
        }
        for old_url, new_url in optimized_assets.items():
            conn.execute("UPDATE fragrances SET image_url = ? WHERE image_url = ?", (new_url, old_url))
            conn.execute("UPDATE fragrances SET photo_icon_url = ? WHERE photo_icon_url = ?", (new_url, old_url))

    def _prepare_initialization_connection(self, conn: DatabaseConnection) -> None:
        if conn.dialect != "postgres":
            return

        conn.execute("SELECT set_config('statement_timeout', ?, true)", ("0",))
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtext(?))",
            (f"{self.settings.site_name}:database_initialize",),
        )

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
                photo_icon_url TEXT NOT NULL DEFAULT '',
                artwork_kind TEXT NOT NULL DEFAULT 'generated',
                bottle_size_ml INTEGER NOT NULL,
                featured INTEGER NOT NULL DEFAULT 0,
                rank INTEGER NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1
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
                compare_at_price_inr INTEGER NOT NULL DEFAULT 0,
                stock_units INTEGER NOT NULL,
                badge TEXT NOT NULL,
                statement TEXT NOT NULL,
                FOREIGN KEY(fragrance_id) REFERENCES fragrances(id) ON DELETE CASCADE
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS customers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                email_verified_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                last_login_at TEXT NOT NULL DEFAULT ''
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS customer_email_verifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id INTEGER NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                used_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(customer_id) REFERENCES customers(id) ON DELETE CASCADE
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS customer_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id INTEGER NOT NULL,
                session_token_hash TEXT NOT NULL UNIQUE,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                FOREIGN KEY(customer_id) REFERENCES customers(id) ON DELETE CASCADE
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id INTEGER DEFAULT NULL,
                order_number TEXT NOT NULL UNIQUE,
                public_token TEXT NOT NULL UNIQUE,
                idempotency_key TEXT NOT NULL DEFAULT '',
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
                stock_reserved INTEGER NOT NULL DEFAULT 0,
                reservation_expires_at TEXT NOT NULL DEFAULT '',
                initiated_at TEXT NOT NULL DEFAULT '',
                paid_at TEXT NOT NULL DEFAULT '',
                notification_sent_at TEXT NOT NULL DEFAULT '',
                status_updated_at TEXT NOT NULL DEFAULT '',
                status_notification_sent_at TEXT NOT NULL DEFAULT '',
                courier_name TEXT NOT NULL DEFAULT '',
                tracking_number TEXT NOT NULL DEFAULT '',
                tracking_url TEXT NOT NULL DEFAULT '',
                admin_notes TEXT NOT NULL DEFAULT '',
                shipped_at TEXT NOT NULL DEFAULT '',
                delivered_at TEXT NOT NULL DEFAULT '',
                cancelled_at TEXT NOT NULL DEFAULT '',
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
            """
            CREATE TABLE IF NOT EXISTS notification_outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dedupe_key TEXT NOT NULL UNIQUE,
                event_type TEXT NOT NULL,
                order_number TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                available_at TEXT NOT NULL,
                locked_at TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                sent_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(order_number) REFERENCES orders(order_number) ON DELETE CASCADE
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS account_email_outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dedupe_key TEXT NOT NULL UNIQUE,
                customer_id INTEGER NOT NULL,
                recipient TEXT NOT NULL,
                full_name TEXT NOT NULL,
                verification_url TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                available_at TEXT NOT NULL,
                locked_at TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                sent_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(customer_id) REFERENCES customers(id) ON DELETE CASCADE
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_fragrances_brand ON fragrances (brand)",
            "CREATE INDEX IF NOT EXISTS idx_fragrances_collection_type ON fragrances (collection_type)",
            "CREATE INDEX IF NOT EXISTS idx_fragrances_gender ON fragrances (gender)",
            "CREATE INDEX IF NOT EXISTS idx_variants_fragrance_id ON variants (fragrance_id)",
            "CREATE INDEX IF NOT EXISTS idx_variants_sale_type ON variants (sale_type)",
            "CREATE INDEX IF NOT EXISTS idx_customers_email ON customers (email)",
            "CREATE INDEX IF NOT EXISTS idx_customer_sessions_customer_id ON customer_sessions (customer_id)",
            "CREATE INDEX IF NOT EXISTS idx_customer_sessions_expires_at ON customer_sessions (expires_at)",
            "CREATE INDEX IF NOT EXISTS idx_customer_verifications_customer_id ON customer_email_verifications (customer_id)",
            "CREATE INDEX IF NOT EXISTS idx_customer_verifications_expires_at ON customer_email_verifications (expires_at)",
            "CREATE INDEX IF NOT EXISTS idx_orders_email ON orders (email)",
            "CREATE INDEX IF NOT EXISTS idx_orders_created_at ON orders (created_at)",
            "CREATE INDEX IF NOT EXISTS idx_orders_payment_status ON orders (payment_status)",
            "CREATE INDEX IF NOT EXISTS idx_orders_gateway_order_id ON orders (gateway_order_id)",
            "CREATE INDEX IF NOT EXISTS idx_order_items_order_id ON order_items (order_id)",
            "CREATE INDEX IF NOT EXISTS idx_notification_outbox_ready ON notification_outbox (status, available_at)",
            "CREATE INDEX IF NOT EXISTS idx_account_email_outbox_ready ON account_email_outbox (status, available_at)",
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
                photo_icon_url TEXT NOT NULL DEFAULT '',
                artwork_kind TEXT NOT NULL DEFAULT 'generated',
                bottle_size_ml INTEGER NOT NULL,
                featured SMALLINT NOT NULL DEFAULT 0,
                rank INTEGER NOT NULL,
                is_active SMALLINT NOT NULL DEFAULT 1
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
                compare_at_price_inr INTEGER NOT NULL DEFAULT 0,
                stock_units INTEGER NOT NULL,
                badge TEXT NOT NULL,
                statement TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS customers (
                id BIGSERIAL PRIMARY KEY,
                full_name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                email_verified_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                last_login_at TEXT NOT NULL DEFAULT ''
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS customer_email_verifications (
                id BIGSERIAL PRIMARY KEY,
                customer_id BIGINT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                token_hash TEXT NOT NULL UNIQUE,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                used_at TEXT NOT NULL DEFAULT ''
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS customer_sessions (
                id BIGSERIAL PRIMARY KEY,
                customer_id BIGINT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                session_token_hash TEXT NOT NULL UNIQUE,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS orders (
                id BIGSERIAL PRIMARY KEY,
                customer_id BIGINT DEFAULT NULL REFERENCES customers(id) ON DELETE SET NULL,
                order_number TEXT NOT NULL UNIQUE,
                public_token TEXT NOT NULL UNIQUE,
                idempotency_key TEXT NOT NULL DEFAULT '',
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
                stock_reserved SMALLINT NOT NULL DEFAULT 0,
                reservation_expires_at TEXT NOT NULL DEFAULT '',
                initiated_at TEXT NOT NULL DEFAULT '',
                paid_at TEXT NOT NULL DEFAULT '',
                notification_sent_at TEXT NOT NULL DEFAULT '',
                status_updated_at TEXT NOT NULL DEFAULT '',
                status_notification_sent_at TEXT NOT NULL DEFAULT '',
                courier_name TEXT NOT NULL DEFAULT '',
                tracking_number TEXT NOT NULL DEFAULT '',
                tracking_url TEXT NOT NULL DEFAULT '',
                admin_notes TEXT NOT NULL DEFAULT '',
                shipped_at TEXT NOT NULL DEFAULT '',
                delivered_at TEXT NOT NULL DEFAULT '',
                cancelled_at TEXT NOT NULL DEFAULT '',
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
            """
            CREATE TABLE IF NOT EXISTS notification_outbox (
                id BIGSERIAL PRIMARY KEY,
                dedupe_key TEXT NOT NULL UNIQUE,
                event_type TEXT NOT NULL,
                order_number TEXT NOT NULL REFERENCES orders(order_number) ON DELETE CASCADE,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                available_at TEXT NOT NULL,
                locked_at TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                sent_at TEXT NOT NULL DEFAULT ''
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS account_email_outbox (
                id BIGSERIAL PRIMARY KEY,
                dedupe_key TEXT NOT NULL UNIQUE,
                customer_id BIGINT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                recipient TEXT NOT NULL,
                full_name TEXT NOT NULL,
                verification_url TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                available_at TEXT NOT NULL,
                locked_at TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                sent_at TEXT NOT NULL DEFAULT ''
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_fragrances_brand ON fragrances (brand)",
            "CREATE INDEX IF NOT EXISTS idx_fragrances_collection_type ON fragrances (collection_type)",
            "CREATE INDEX IF NOT EXISTS idx_fragrances_gender ON fragrances (gender)",
            "CREATE INDEX IF NOT EXISTS idx_variants_fragrance_id ON variants (fragrance_id)",
            "CREATE INDEX IF NOT EXISTS idx_variants_sale_type ON variants (sale_type)",
            "CREATE INDEX IF NOT EXISTS idx_customers_email ON customers (email)",
            "CREATE INDEX IF NOT EXISTS idx_customer_sessions_customer_id ON customer_sessions (customer_id)",
            "CREATE INDEX IF NOT EXISTS idx_customer_sessions_expires_at ON customer_sessions (expires_at)",
            "CREATE INDEX IF NOT EXISTS idx_customer_verifications_customer_id ON customer_email_verifications (customer_id)",
            "CREATE INDEX IF NOT EXISTS idx_customer_verifications_expires_at ON customer_email_verifications (expires_at)",
            "CREATE INDEX IF NOT EXISTS idx_orders_email ON orders (email)",
            "CREATE INDEX IF NOT EXISTS idx_orders_created_at ON orders (created_at)",
            "CREATE INDEX IF NOT EXISTS idx_orders_payment_status ON orders (payment_status)",
            "CREATE INDEX IF NOT EXISTS idx_orders_gateway_order_id ON orders (gateway_order_id)",
            "CREATE INDEX IF NOT EXISTS idx_order_items_order_id ON order_items (order_id)",
            "CREATE INDEX IF NOT EXISTS idx_notification_outbox_ready ON notification_outbox (status, available_at)",
            "CREATE INDEX IF NOT EXISTS idx_account_email_outbox_ready ON account_email_outbox (status, available_at)",
        ]

    def _ensure_schema_upgrades(self, conn: DatabaseConnection) -> None:
        self._ensure_columns(
            conn,
            "customers",
            {
                "email_verified_at": "TEXT NOT NULL DEFAULT ''",
            },
        )
        self._ensure_columns(
            conn,
            "fragrances",
            {
                "top_notes": "TEXT NOT NULL DEFAULT '[]'",
                "heart_notes": "TEXT NOT NULL DEFAULT '[]'",
                "base_notes": "TEXT NOT NULL DEFAULT '[]'",
                "accent_from": "TEXT NOT NULL DEFAULT '#9b8067'",
                "accent_to": "TEXT NOT NULL DEFAULT '#1f1b18'",
                "image_url": "TEXT NOT NULL DEFAULT ''",
                "photo_icon_url": "TEXT NOT NULL DEFAULT ''",
                "artwork_kind": "TEXT NOT NULL DEFAULT 'generated'",
                "bottle_size_ml": "INTEGER NOT NULL DEFAULT 100",
                "featured": "INTEGER NOT NULL DEFAULT 0",
                "rank": "INTEGER NOT NULL DEFAULT 999",
                "is_active": "INTEGER NOT NULL DEFAULT 1",
            },
        )
        self._ensure_columns(
            conn,
            "variants",
            {
                "compare_at_price_inr": "INTEGER NOT NULL DEFAULT 0",
            },
        )
        self._ensure_columns(
            conn,
            "orders",
            {
                "customer_id": "INTEGER DEFAULT NULL",
                "payment_gateway": "TEXT NOT NULL DEFAULT ''",
                "payment_status": "TEXT NOT NULL DEFAULT ''",
                "public_token": "TEXT NOT NULL DEFAULT ''",
                "idempotency_key": "TEXT NOT NULL DEFAULT ''",
                "gateway_order_id": "TEXT NOT NULL DEFAULT ''",
                "gateway_payment_id": "TEXT NOT NULL DEFAULT ''",
                "gateway_signature": "TEXT NOT NULL DEFAULT ''",
                "payment_amount_inr": "INTEGER NOT NULL DEFAULT 0",
                "stock_reserved": "INTEGER NOT NULL DEFAULT 0",
                "reservation_expires_at": "TEXT NOT NULL DEFAULT ''",
                "initiated_at": "TEXT NOT NULL DEFAULT ''",
                "paid_at": "TEXT NOT NULL DEFAULT ''",
                "notification_sent_at": "TEXT NOT NULL DEFAULT ''",
                "status_updated_at": "TEXT NOT NULL DEFAULT ''",
                "status_notification_sent_at": "TEXT NOT NULL DEFAULT ''",
                "courier_name": "TEXT NOT NULL DEFAULT ''",
                "tracking_number": "TEXT NOT NULL DEFAULT ''",
                "tracking_url": "TEXT NOT NULL DEFAULT ''",
                "admin_notes": "TEXT NOT NULL DEFAULT ''",
                "shipped_at": "TEXT NOT NULL DEFAULT ''",
                "delivered_at": "TEXT NOT NULL DEFAULT ''",
                "cancelled_at": "TEXT NOT NULL DEFAULT ''",
                "last_error": "TEXT NOT NULL DEFAULT ''",
            },
        )
        self._backfill_order_tokens(conn)
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_public_token ON orders (public_token)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_customer_id ON orders (customer_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_email ON orders (email)")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_idempotency_key "
            "ON orders (idempotency_key) WHERE idempotency_key != ''"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_orders_reservation_expiry "
            "ON orders (stock_reserved, reservation_expires_at)"
        )

    def _backfill_order_tokens(self, conn: DatabaseConnection) -> None:
        rows = conn.execute(
            "SELECT id FROM orders WHERE public_token = '' OR public_token IS NULL"
        ).fetchall()
        for row in rows:
            conn.execute(
                "UPDATE orders SET public_token = ? WHERE id = ?",
                (self._generate_public_token(conn), row["id"]),
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
                accent_from, accent_to, image_url, photo_icon_url, artwork_kind, bottle_size_ml, featured, rank, is_active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                item["photo_icon_url"],
                item["artwork_kind"],
                item["bottle_size_ml"],
                1 if item["featured"] else 0,
                item["rank"],
                1,
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
                    fragrance_id, sku, sale_type, size_label, size_ml, price_inr, compare_at_price_inr,
                    stock_units, badge, statement
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(sku) DO UPDATE SET
                    fragrance_id = excluded.fragrance_id,
                    sale_type = excluded.sale_type,
                    size_label = excluded.size_label,
                    size_ml = excluded.size_ml,
                    price_inr = excluded.price_inr,
                    compare_at_price_inr = excluded.compare_at_price_inr,
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
                    variant.get("compare_at_price_inr", 0),
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

    def create_customer(self, full_name: str, email: str, password: str) -> dict[str, Any]:
        full_name = re.sub(r"\s+", " ", str(full_name or "")).strip()
        email = str(email or "").strip().lower()
        password = str(password or "")

        if len(full_name) < 2 or len(full_name) > 100:
            raise ValidationError("Full name is required.")
        if len(email) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise ValidationError("Please enter a valid email address.")
        if len(password) < 8 or len(password) > 128:
            raise ValidationError("Password must be between 8 and 128 characters.")

        now = self._now()
        with self.connect() as conn:
            try:
                conn.begin_write()
                existing = conn.execute(
                    "SELECT 1 FROM customers WHERE email = ? LIMIT 1",
                    (email,),
                ).fetchone()
                if existing:
                    raise ValidationError("An account already exists for this email.")
                conn.execute(
                    """
                    INSERT INTO customers (full_name, email, password_hash, email_verified_at, created_at, last_login_at)
                    VALUES (?, ?, ?, '', ?, ?)
                    """,
                    (full_name, email, self._hash_password(password), now, now),
                )
                customer_row = conn.execute(
                    "SELECT id, full_name, email, created_at, last_login_at FROM customers WHERE email = ?",
                    (email,),
                ).fetchone()
                if customer_row is None:
                    raise ValidationError("Account could not be loaded after sign up.")
                conn.commit()
            except Exception:
                conn.rollback()
                raise

        customer = self.get_customer_by_email(email)
        if customer is None:
            raise ValidationError("Account could not be loaded after sign up.")
        return customer

    def authenticate_customer(self, email: str, password: str) -> dict[str, Any] | None:
        email = str(email or "").strip().lower()
        password = str(password or "")
        if not email or not password:
            return None

        with self.connect() as conn:
            row = conn.execute("SELECT * FROM customers WHERE email = ?", (email,)).fetchone()
            if row is None or not self._verify_password(password, row["password_hash"]):
                return None
            now = self._now()
            conn.execute(
                "UPDATE customers SET last_login_at = ? WHERE id = ?",
                (now, row["id"]),
            )
            conn.commit()

        return self.get_customer_by_email(email)

    def get_customer_by_email(self, email: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id, full_name, email, email_verified_at, created_at, last_login_at FROM customers WHERE email = ?",
                (str(email or "").strip().lower(),),
            ).fetchone()
        return dict(row) if row else None

    def create_customer_session(self, customer_id: int, days: int = 30) -> str:
        token = secrets.token_urlsafe(32)
        now = self._now()
        expires = (datetime.utcnow() + timedelta(days=days)).replace(microsecond=0).isoformat() + "Z"
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO customer_sessions (customer_id, session_token_hash, expires_at, created_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (customer_id, self._hash_token(token), expires, now, now),
            )
            conn.commit()
        return token

    def create_email_verification(self, customer_id: int, hours: int = 24) -> str:
        token = secrets.token_urlsafe(32)
        now = self._now()
        expires = (datetime.utcnow() + timedelta(hours=max(1, hours))).replace(microsecond=0).isoformat() + "Z"
        with self.connect() as conn:
            try:
                conn.begin_write()
                conn.execute(
                    "DELETE FROM customer_email_verifications WHERE customer_id = ? AND used_at = ''",
                    (customer_id,),
                )
                conn.execute(
                    """
                    INSERT INTO customer_email_verifications
                        (customer_id, token_hash, expires_at, created_at, used_at)
                    VALUES (?, ?, ?, ?, '')
                    """,
                    (customer_id, self._hash_token(token), expires, now),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return token

    def verify_customer_email(self, token: str) -> dict[str, Any] | None:
        token = str(token or "").strip()
        if not token:
            return None
        now = self._now()
        with self.connect() as conn:
            try:
                conn.begin_write()
                query = """
                    SELECT v.id AS verification_id, v.customer_id, c.email
                    FROM customer_email_verifications v
                    JOIN customers c ON c.id = v.customer_id
                    WHERE v.token_hash = ? AND v.used_at = '' AND v.expires_at > ?
                """
                if conn.dialect == "postgres":
                    query += " FOR UPDATE"
                row = conn.execute(query, (self._hash_token(token), now)).fetchone()
                if row is None:
                    conn.rollback()
                    return None
                conn.execute(
                    "UPDATE customers SET email_verified_at = ? WHERE id = ?",
                    (now, row["customer_id"]),
                )
                conn.execute(
                    "UPDATE customer_email_verifications SET used_at = ? WHERE id = ?",
                    (now, row["verification_id"]),
                )
                self._claim_customer_orders(conn, int(row["customer_id"]), str(row["email"]).lower())
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return self.get_customer_by_email(str(row["email"]))

    def get_customer_by_session_token(self, token: str) -> dict[str, Any] | None:
        token = str(token or "").strip()
        if not token:
            return None
        token_hash = self._hash_token(token)
        now = self._now()
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT c.id, c.full_name, c.email, c.email_verified_at, c.created_at, c.last_login_at,
                       s.expires_at, s.last_seen_at
                FROM customer_sessions s
                JOIN customers c ON c.id = s.customer_id
                WHERE s.session_token_hash = ? AND s.expires_at > ?
                """,
                (token_hash, now),
            ).fetchone()
            if row is None:
                conn.execute(
                    "DELETE FROM customer_sessions WHERE session_token_hash = ? OR expires_at <= ?",
                    (token_hash, now),
                )
                conn.commit()
                return None
            last_seen_cutoff = (datetime.utcnow() - timedelta(minutes=15)).replace(microsecond=0).isoformat() + "Z"
            if str(row["last_seen_at"]) < last_seen_cutoff:
                conn.execute(
                    "UPDATE customer_sessions SET last_seen_at = ? WHERE session_token_hash = ?",
                    (now, token_hash),
                )
                conn.commit()
        customer = dict(row)
        return customer

    def delete_customer_session(self, token: str) -> None:
        token = str(token or "").strip()
        if not token:
            return
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM customer_sessions WHERE session_token_hash = ?",
                (self._hash_token(token),),
            )
            conn.commit()

    def list_customer_orders(self, customer_id: int, email: str = "", limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT order_number, public_token, customer_name, email, total_inr, item_count,
                       status, payment_method, payment_status, created_at
                FROM orders
                WHERE customer_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (customer_id, limit),
            ).fetchall()
        orders = [dict(row) for row in rows]
        for order in orders:
            order["public_path"] = f"/account/orders/{order['order_number']}"
        return orders

    def get_customer_order(self, customer_id: int, email: str, order_number: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT order_number
                FROM orders
                WHERE order_number = ? AND customer_id = ?
                """,
                (order_number, customer_id),
            ).fetchone()
        if row is None:
            return None
        return self.get_order(order_number)

    def _claim_customer_orders(self, conn: DatabaseConnection, customer_id: int, email: str) -> None:
        conn.execute(
            """
            UPDATE orders
            SET customer_id = ?
            WHERE (customer_id IS NULL OR customer_id = 0) AND LOWER(email) = ?
            """,
            (customer_id, email),
        )

    def _hash_password(self, password: str) -> str:
        iterations = 260_000
        salt = secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt),
            iterations,
        ).hex()
        return f"pbkdf2_sha256${iterations}${salt}${digest}"

    def _verify_password(self, password: str, password_hash: str) -> bool:
        try:
            algorithm, iterations, salt, expected = password_hash.split("$", 3)
            if algorithm != "pbkdf2_sha256":
                return False
            digest = hashlib.pbkdf2_hmac(
                "sha256",
                password.encode("utf-8"),
                bytes.fromhex(salt),
                int(iterations),
            ).hex()
        except Exception:
            return False
        return secrets.compare_digest(digest, expected)

    def _hash_token(self, token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def list_filters(self) -> dict[str, list[str]]:
        with self.connect() as conn:
            sellable_clause = """
                EXISTS (
                    SELECT 1
                    FROM variants v
                    WHERE v.fragrance_id = fragrances.id
                      AND v.stock_units > 0
                      AND v.size_ml >= ?
                )
            """
            brands = [
                row["brand"]
                for row in conn.execute(
                    f"SELECT DISTINCT brand FROM fragrances WHERE is_active = 1 AND {sellable_clause} ORDER BY brand",
                    (MIN_PUBLIC_VARIANT_SIZE_ML,),
                )
            ]
            families = [
                row["family"]
                for row in conn.execute(
                    f"SELECT DISTINCT family FROM fragrances WHERE is_active = 1 AND {sellable_clause} ORDER BY family",
                    (MIN_PUBLIC_VARIANT_SIZE_ML,),
                )
            ]
            collections = [
                row["collection_type"]
                for row in conn.execute(
                    f"SELECT DISTINCT collection_type FROM fragrances WHERE is_active = 1 AND {sellable_clause} ORDER BY collection_type",
                    (MIN_PUBLIC_VARIANT_SIZE_ML,),
                )
            ]
            sale_types = [
                row["sale_type"]
                for row in conn.execute(
                    """
                    SELECT DISTINCT v.sale_type
                    FROM variants v
                    JOIN fragrances f ON f.id = v.fragrance_id
                    WHERE f.is_active = 1
                      AND v.stock_units > 0
                      AND v.size_ml >= ?
                    ORDER BY v.sale_type
                    """,
                    (MIN_PUBLIC_VARIANT_SIZE_ML,),
                )
            ]

        return {
            "brands": brands,
            "families": families,
            "collections": collections,
            "sale_types": sale_types,
            "genders": ["him", "her", "unisex"],
        }

    def list_brand_groups(self) -> dict[str, list[dict[str, Any]]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT collection_type, brand, COUNT(*) AS count
                  FROM fragrances
                 WHERE is_active = 1
                   AND EXISTS (
                       SELECT 1
                       FROM variants v
                       WHERE v.fragrance_id = fragrances.id
                         AND v.stock_units > 0
                         AND v.size_ml >= ?
                   )
                 GROUP BY collection_type, brand
                 ORDER BY collection_type, brand
                """,
                (MIN_PUBLIC_VARIANT_SIZE_ML,),
            ).fetchall()

        groups: dict[str, list[dict[str, Any]]] = {"designer": [], "niche": []}
        for row in rows:
            collection = row["collection_type"] or "niche"
            if collection not in groups:
                groups[collection] = []
            groups[collection].append(
                {
                    "name": row["brand"],
                    "href": f"/catalog?brand={quote(str(row['brand']))}",
                    "count": row["count"],
                }
            )
        return groups

    def list_fragrances(
        self,
        filters: dict[str, str] | None = None,
        *,
        limit: int | None = None,
        offset: int = 0,
        featured_only: bool = False,
        include_inactive: bool = False,
    ) -> list[dict[str, Any]]:
        clauses, params = self._fragrance_filter_clauses(
            filters,
            featured_only=featured_only,
            include_inactive=include_inactive,
        )

        order_by = self._fragrance_order_by((filters or {}).get("sort", ""))
        query = f"""
            SELECT *
            FROM fragrances
            WHERE {' AND '.join(clauses)}
            ORDER BY {order_by}
        """

        if limit:
            query += " LIMIT ?"
            params.append(limit)
            if offset:
                query += " OFFSET ?"
                params.append(offset)

        with self.connect() as conn:
            fragrance_rows = conn.execute(query, params).fetchall()
            fragrance_ids = [row["id"] for row in fragrance_rows]
            variants_by_fragrance = self._variants_by_fragrance(conn, fragrance_ids)

        return [self._serialize_fragrance(row, variants_by_fragrance.get(row["id"], [])) for row in fragrance_rows]

    def _fragrance_order_by(self, sort: str) -> str:
        min_price = f"""
            COALESCE((
                SELECT MIN(v.price_inr)
                FROM variants v
                WHERE v.fragrance_id = fragrances.id
                  AND v.stock_units > 0
                  AND v.size_ml >= {MIN_PUBLIC_VARIANT_SIZE_ML}
            ), 0)
        """
        max_discount = f"""
            COALESCE((
                SELECT MAX(
                    CASE
                        WHEN v.compare_at_price_inr > v.price_inr
                        THEN v.compare_at_price_inr - v.price_inr
                        ELSE 0
                    END
                )
                FROM variants v
                WHERE v.fragrance_id = fragrances.id
                  AND v.stock_units > 0
                  AND v.size_ml >= {MIN_PUBLIC_VARIANT_SIZE_ML}
            ), 0)
        """

        if sort == "price_low":
            return f"{min_price} ASC, featured DESC, rank ASC, brand ASC, name ASC"
        if sort == "price_high":
            return f"{min_price} DESC, featured DESC, rank ASC, brand ASC, name ASC"
        if sort == "discount_high":
            return f"{max_discount} DESC, featured DESC, rank ASC, brand ASC, name ASC"
        return "featured DESC, rank ASC, brand ASC, name ASC"

    def count_fragrances(
        self,
        filters: dict[str, str] | None = None,
        *,
        featured_only: bool = False,
        include_inactive: bool = False,
    ) -> int:
        clauses, params = self._fragrance_filter_clauses(
            filters,
            featured_only=featured_only,
            include_inactive=include_inactive,
        )
        with self.connect() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) AS count FROM fragrances WHERE {' AND '.join(clauses)}",
                params,
            ).fetchone()
        return int(row["count"]) if row else 0

    def _fragrance_filter_clauses(
        self,
        filters: dict[str, str] | None = None,
        *,
        featured_only: bool = False,
        include_inactive: bool = False,
    ) -> tuple[list[str], list[Any]]:
        filters = {key: value for key, value in (filters or {}).items() if value}
        clauses = ["1 = 1"]
        params: list[Any] = []

        if not include_inactive:
            clauses.append("is_active = 1")
            clauses.append(
                """
                EXISTS (
                    SELECT 1
                    FROM variants v
                    WHERE v.fragrance_id = fragrances.id
                      AND v.stock_units > 0
                      AND v.size_ml >= ?
                )
                """
            )
            params.append(MIN_PUBLIC_VARIANT_SIZE_ML)

        if featured_only:
            clauses.append("featured = 1")

        search = filters.get("search")
        if search:
            search_clauses = []
            for term in self._catalog_search_terms(search):
                like = f"%{term.lower()}%"
                search_clauses.append("(LOWER(name) LIKE ? OR LOWER(brand) LIKE ? OR LOWER(description) LIKE ?)")
                params.extend([like, like, like])
            clauses.append("(" + " OR ".join(search_clauses) + ")")

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
                """
                EXISTS (
                    SELECT 1
                    FROM variants v
                    WHERE v.fragrance_id = fragrances.id
                      AND v.sale_type = ?
                      AND v.stock_units > 0
                      AND v.size_ml >= ?
                )
                """
            )
            params.append(filters["sale_type"])
            params.append(MIN_PUBLIC_VARIANT_SIZE_ML)

        return clauses, params

    def _catalog_search_terms(self, search: str) -> list[str]:
        clean_search = " ".join(str(search or "").strip().split())
        terms = [clean_search] if clean_search else []
        normalized_search = self._normalize_search_term(clean_search)
        if not normalized_search:
            return terms

        search_tokens = [token for token in normalized_search.split() if len(token) >= 3]
        brand_matches: list[tuple[float, str, str, str]] = []
        name_matches: list[tuple[float, str, str, str]] = []
        token_matches: list[tuple[float, str, str, str]] = []

        for display_term, normalized_term, term_type in self._catalog_search_vocabulary():
            score = self._catalog_search_score(
                normalized_search,
                search_tokens,
                normalized_term,
                term_type,
            )
            if not score:
                continue
            match = (score, display_term, normalized_term, term_type)
            if term_type.startswith("brand"):
                brand_matches.append(match)
            elif term_type == "name":
                name_matches.append(match)
            else:
                token_matches.append(match)

        full_brand_matches = [match for match in brand_matches if match[3] == "brand"]
        exact_brand_token_matches = [
            match for match in brand_matches
            if match[3] == "brand_token" and match[2] in search_tokens
        ]
        matches = full_brand_matches or exact_brand_token_matches or brand_matches or name_matches or token_matches
        matches.sort(key=lambda item: item[0], reverse=True)
        if matches:
            best_score = matches[0][0]
            matches = [
                item for item in matches
                if item[0] >= max(0.78, best_score - 0.03)
            ]
        terms.extend(display_term for _, display_term, _, _ in matches[:5])

        unique_terms = []
        seen = set()
        for term in terms:
            key = self._normalize_search_term(term)
            if key and key not in seen:
                unique_terms.append(term)
                seen.add(key)
        return unique_terms[:10]

    def suggest_search_term(self, search: str) -> str:
        normalized_search = self._normalize_search_term(search)
        if len(normalized_search) < 3:
            return ""
        for term in self._catalog_search_terms(search):
            if self._normalize_search_term(term) != normalized_search:
                return term
        return ""

    def _catalog_search_vocabulary(self) -> list[tuple[str, str, str]]:
        if self._search_vocabulary is not None:
            return self._search_vocabulary

        vocabulary: list[tuple[str, str, str]] = []
        seen = set()

        def add_term(term: str, term_type: str, *, minimum_length: int = 3) -> None:
            display_term = " ".join(str(term or "").strip().split())
            normalized = self._normalize_search_term(display_term)
            key = f"{term_type}:{normalized}"
            if len(normalized) < minimum_length or key in seen:
                return
            vocabulary.append((display_term, normalized, term_type))
            seen.add(key)

        with self.connect() as conn:
            rows = conn.execute("SELECT DISTINCT brand, name FROM fragrances WHERE is_active = 1").fetchall()

        name_token_counts: dict[str, int] = {}
        for row in rows:
            for token in set(self._normalize_search_term(row["name"]).split()):
                if len(token) >= 5:
                    name_token_counts[token] = name_token_counts.get(token, 0) + 1
        max_name_token_frequency = max(8, len(rows) // 60)

        for row in rows:
            brand = row["brand"]
            name = row["name"]
            add_term(brand, "brand")
            for token in self._normalize_search_term(brand).split():
                add_term(token, "brand_token", minimum_length=4)
            add_term(name, "name")
            for token in self._normalize_search_term(name).split():
                if name_token_counts.get(token, 0) > max_name_token_frequency:
                    continue
                add_term(token, "name_token", minimum_length=5)

        self._search_vocabulary = vocabulary
        return vocabulary

    def _normalize_search_term(self, value: str) -> str:
        value = str(value or "").lower().replace("&", " and ")
        return re.sub(r"[^a-z0-9]+", " ", value).strip()

    def _catalog_search_score(
        self,
        query: str,
        query_tokens: list[str],
        candidate: str,
        term_type: str,
    ) -> float:
        if term_type.endswith("_token") and " " in query:
            full_score = 0.0
        else:
            full_score = self._search_match_score(query, candidate)
        token_scores = []
        for index, token in enumerate(query_tokens):
            if token == candidate:
                score = self._exact_token_score(token, candidate, term_type)
            else:
                score = self._search_match_score(token, candidate)
            if score and term_type.endswith("_token"):
                score = max(0.0, score - (index * 0.18))
            token_scores.append(score)
        token_score = max(token_scores, default=0.0)

        score = max(full_score, token_score)
        if not score:
            return 0.0

        if term_type.startswith("brand"):
            score += 0.06
        elif term_type == "name":
            score += 0.03
        return min(score, 1.0)

    def _exact_token_score(self, token: str, candidate: str, term_type: str) -> float:
        if token != candidate:
            return 0.0
        if term_type == "brand_token":
            return 0.88 if len(token) >= 5 else 0.76
        return 0.0

    def _search_match_score(self, query: str, candidate: str) -> float:
        if not query or not candidate or query == candidate:
            return 1.0 if query and query == candidate else 0.0
        if " " in candidate and candidate in query:
            return 0.94
        if len(candidate) >= 4 and (query in candidate or candidate in query):
            length_ratio = min(len(query), len(candidate)) / max(len(query), len(candidate))
            if length_ratio >= 0.55:
                return 0.98

        shorter = min(len(query), len(candidate))
        if shorter < 3:
            return 0.0
        threshold = 0.75 if shorter <= 4 else 0.76
        score = SequenceMatcher(None, query, candidate).ratio()
        return score if score >= threshold else 0.0

    def get_admin_summary(self) -> dict[str, int]:
        with self.connect() as conn:
            active = conn.execute("SELECT COUNT(*) AS count FROM fragrances WHERE is_active = 1").fetchone()["count"]
            archived = conn.execute("SELECT COUNT(*) AS count FROM fragrances WHERE is_active = 0").fetchone()["count"]
            variants = conn.execute("SELECT COUNT(*) AS count FROM variants").fetchone()["count"]
            stock = conn.execute("SELECT COALESCE(SUM(stock_units), 0) AS count FROM variants").fetchone()["count"]
            low_stock = conn.execute(
                "SELECT COUNT(*) AS count FROM variants WHERE stock_units > 0 AND stock_units <= 2"
            ).fetchone()["count"]
            out_of_stock = conn.execute("SELECT COUNT(*) AS count FROM variants WHERE stock_units <= 0").fetchone()["count"]
            open_orders = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM orders
                WHERE status NOT IN ('Delivered', 'Cancelled', 'Payment Expired', 'Payment Failed')
                """
            ).fetchone()["count"]
        return {
            "active_fragrances": int(active or 0),
            "archived_fragrances": int(archived or 0),
            "variants": int(variants or 0),
            "stock_units": int(stock or 0),
            "low_stock_variants": int(low_stock or 0),
            "out_of_stock_variants": int(out_of_stock or 0),
            "open_orders": int(open_orders or 0),
        }

    def list_admin_fragrances(
        self,
        *,
        search: str = "",
        status: str = "active",
        limit: int = 160,
    ) -> list[dict[str, Any]]:
        clauses = ["1 = 1"]
        params: list[Any] = []
        clean_search = " ".join(str(search or "").strip().split())
        if clean_search:
            like = f"%{clean_search.lower()}%"
            clauses.append(
                "(LOWER(f.brand) LIKE ? OR LOWER(f.name) LIKE ? OR LOWER(f.slug) LIKE ? OR LOWER(f.family) LIKE ?)"
            )
            params.extend([like, like, like, like])

        if status == "active":
            clauses.append("f.is_active = 1")
        elif status == "archived":
            clauses.append("f.is_active = 0")

        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    f.id,
                    f.slug,
                    f.brand,
                    f.name,
                    f.collection_type,
                    f.gender,
                    f.family,
                    f.concentration,
                    f.featured,
                    f.rank,
                    f.is_active,
                    COALESCE(vs.variant_count, 0) AS variant_count,
                    COALESCE(vs.total_stock, 0) AS total_stock,
                    COALESCE(vs.low_stock_count, 0) AS low_stock_count,
                    COALESCE(vs.min_price, 0) AS min_price,
                    COALESCE(vs.max_price, 0) AS max_price
                FROM fragrances f
                LEFT JOIN (
                    SELECT
                        fragrance_id,
                        COUNT(*) AS variant_count,
                        SUM(stock_units) AS total_stock,
                        SUM(CASE WHEN stock_units > 0 AND stock_units <= 2 THEN 1 ELSE 0 END) AS low_stock_count,
                        MIN(price_inr) AS min_price,
                        MAX(price_inr) AS max_price
                    FROM variants
                    GROUP BY fragrance_id
                ) vs ON vs.fragrance_id = f.id
                WHERE {' AND '.join(clauses)}
                ORDER BY f.is_active DESC, f.brand ASC, f.name ASC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def get_admin_fragrance(self, slug: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM fragrances WHERE slug = ?", (slug,)).fetchone()
            if row is None:
                return None
            variants = self._variants_by_fragrance(conn, [row["id"]]).get(row["id"], [])
        return self._serialize_admin_fragrance(row, variants)

    def save_admin_fragrance(
        self,
        fields: dict[str, str],
        *,
        current_slug: str | None = None,
    ) -> dict[str, Any]:
        fragrance = self._normalize_admin_fragrance_fields(fields, current_slug=current_slug)
        now_slug = current_slug or ""

        with self.connect() as conn:
            try:
                conn.begin_write()
                conflict = conn.execute(
                    "SELECT slug FROM fragrances WHERE slug = ? AND (? = '' OR slug != ?)",
                    (fragrance["slug"], now_slug, now_slug),
                ).fetchone()
                if conflict:
                    raise ValidationError("Another fragrance already uses this slug.")

                if current_slug:
                    existing = conn.execute("SELECT id FROM fragrances WHERE slug = ?", (current_slug,)).fetchone()
                    if existing is None:
                        raise ValidationError("Fragrance not found.")
                    conn.execute(
                        """
                        UPDATE fragrances
                        SET slug = ?,
                            brand = ?,
                            name = ?,
                            collection_type = ?,
                            gender = ?,
                            family = ?,
                            concentration = ?,
                            origin = ?,
                            description = ?,
                            signature = ?,
                            top_notes = ?,
                            heart_notes = ?,
                            base_notes = ?,
                            accent_from = ?,
                            accent_to = ?,
                            image_url = ?,
                            photo_icon_url = ?,
                            artwork_kind = ?,
                            bottle_size_ml = ?,
                            featured = ?,
                            rank = ?,
                            is_active = ?
                        WHERE slug = ?
                        """,
                        (
                            fragrance["slug"],
                            fragrance["brand"],
                            fragrance["name"],
                            fragrance["collection_type"],
                            fragrance["gender"],
                            fragrance["family"],
                            fragrance["concentration"],
                            fragrance["origin"],
                            fragrance["description"],
                            fragrance["signature"],
                            json.dumps(fragrance["top_notes"]),
                            json.dumps(fragrance["heart_notes"]),
                            json.dumps(fragrance["base_notes"]),
                            fragrance["accent_from"],
                            fragrance["accent_to"],
                            fragrance["image_url"],
                            fragrance["photo_icon_url"],
                            fragrance["artwork_kind"],
                            fragrance["bottle_size_ml"],
                            1 if fragrance["featured"] else 0,
                            fragrance["rank"],
                            1 if fragrance["is_active"] else 0,
                            current_slug,
                        ),
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO fragrances (
                            slug, brand, name, collection_type, gender, family, concentration,
                            origin, description, signature, top_notes, heart_notes, base_notes,
                            accent_from, accent_to, image_url, photo_icon_url, artwork_kind,
                            bottle_size_ml, featured, rank, is_active
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            fragrance["slug"],
                            fragrance["brand"],
                            fragrance["name"],
                            fragrance["collection_type"],
                            fragrance["gender"],
                            fragrance["family"],
                            fragrance["concentration"],
                            fragrance["origin"],
                            fragrance["description"],
                            fragrance["signature"],
                            json.dumps(fragrance["top_notes"]),
                            json.dumps(fragrance["heart_notes"]),
                            json.dumps(fragrance["base_notes"]),
                            fragrance["accent_from"],
                            fragrance["accent_to"],
                            fragrance["image_url"],
                            fragrance["photo_icon_url"],
                            fragrance["artwork_kind"],
                            fragrance["bottle_size_ml"],
                            1 if fragrance["featured"] else 0,
                            fragrance["rank"],
                            1 if fragrance["is_active"] else 0,
                        ),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

        self._search_vocabulary = None
        saved = self.get_admin_fragrance(fragrance["slug"])
        if saved is None:
            raise ValidationError("Fragrance could not be loaded after save.")
        return saved

    def save_admin_variant(self, fragrance_slug: str, fields: dict[str, str]) -> dict[str, Any]:
        variant = self._normalize_admin_variant_fields(fragrance_slug, fields)
        variant_id = int(str(fields.get("variant_id", "") or "0") or 0)

        with self.connect() as conn:
            try:
                conn.begin_write()
                fragrance_row = conn.execute(
                    "SELECT id FROM fragrances WHERE slug = ?",
                    (fragrance_slug,),
                ).fetchone()
                if fragrance_row is None:
                    raise ValidationError("Fragrance not found.")

                conflict = conn.execute(
                    "SELECT id FROM variants WHERE sku = ? AND (? = 0 OR id != ?)",
                    (variant["sku"], variant_id, variant_id),
                ).fetchone()
                if conflict:
                    raise ValidationError("Another variant already uses this SKU.")

                if variant_id:
                    existing = conn.execute(
                        "SELECT id FROM variants WHERE id = ? AND fragrance_id = ?",
                        (variant_id, fragrance_row["id"]),
                    ).fetchone()
                    if existing is None:
                        raise ValidationError("Variant not found.")
                    conn.execute(
                        """
                        UPDATE variants
                        SET sku = ?,
                            sale_type = ?,
                            size_label = ?,
                            size_ml = ?,
                            price_inr = ?,
                            compare_at_price_inr = ?,
                            stock_units = ?,
                            badge = ?,
                            statement = ?
                        WHERE id = ?
                        """,
                        (
                            variant["sku"],
                            variant["sale_type"],
                            variant["size_label"],
                            variant["size_ml"],
                            variant["price_inr"],
                            variant["compare_at_price_inr"],
                            variant["stock_units"],
                            variant["badge"],
                            variant["statement"],
                            variant_id,
                        ),
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO variants (
                            fragrance_id, sku, sale_type, size_label, size_ml, price_inr,
                            compare_at_price_inr, stock_units, badge, statement
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            fragrance_row["id"],
                            variant["sku"],
                            variant["sale_type"],
                            variant["size_label"],
                            variant["size_ml"],
                            variant["price_inr"],
                            variant["compare_at_price_inr"],
                            variant["stock_units"],
                            variant["badge"],
                            variant["statement"],
                        ),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

        saved = self.get_admin_fragrance(fragrance_slug)
        if saved is None:
            raise ValidationError("Fragrance could not be loaded after variant save.")
        return saved

    def delete_admin_variant(self, fragrance_slug: str, variant_id: int) -> str:
        with self.connect() as conn:
            try:
                conn.begin_write()
                row = conn.execute(
                    """
                    SELECT v.id, v.fragrance_id, f.slug
                    FROM variants v
                    JOIN fragrances f ON f.id = v.fragrance_id
                    WHERE v.id = ? AND f.slug = ?
                    """,
                    (variant_id, fragrance_slug),
                ).fetchone()
                if row is None:
                    raise ValidationError("Variant not found.")
                order_count = conn.execute(
                    "SELECT COUNT(*) AS count FROM order_items WHERE variant_id = ?",
                    (variant_id,),
                ).fetchone()["count"]
                if int(order_count or 0):
                    conn.execute(
                        """
                        UPDATE variants
                        SET stock_units = 0,
                            badge = 'Unavailable',
                            statement = 'Retained for order history.'
                        WHERE id = ?
                        """,
                        (variant_id,),
                    )
                else:
                    conn.execute("DELETE FROM variants WHERE id = ?", (variant_id,))
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return fragrance_slug

    def set_admin_fragrance_active(self, slug: str, is_active: bool) -> None:
        with self.connect() as conn:
            cursor = conn.execute(
                "UPDATE fragrances SET is_active = ? WHERE slug = ?",
                (1 if is_active else 0, slug),
            )
            if getattr(cursor, "rowcount", 1) == 0:
                raise ValidationError("Fragrance not found.")
            conn.commit()
        self._search_vocabulary = None

    def delete_admin_fragrance(self, slug: str) -> str:
        with self.connect() as conn:
            try:
                conn.begin_write()
                row = conn.execute("SELECT id FROM fragrances WHERE slug = ?", (slug,)).fetchone()
                if row is None:
                    raise ValidationError("Fragrance not found.")
                order_count = conn.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM order_items
                    WHERE fragrance_slug = ?
                       OR variant_id IN (SELECT id FROM variants WHERE fragrance_id = ?)
                    """,
                    (slug, row["id"]),
                ).fetchone()["count"]
                if int(order_count or 0):
                    conn.execute("UPDATE fragrances SET is_active = 0 WHERE id = ?", (row["id"],))
                    result = "archived"
                else:
                    conn.execute("DELETE FROM variants WHERE fragrance_id = ?", (row["id"],))
                    conn.execute("DELETE FROM fragrances WHERE id = ?", (row["id"],))
                    result = "deleted"
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        self._search_vocabulary = None
        return result

    def _serialize_admin_fragrance(self, row: Any, variants: list[dict[str, Any]]) -> dict[str, Any]:
        fragrance = dict(row)
        fragrance["top_notes"] = self._safe_note_list(fragrance["top_notes"])
        fragrance["heart_notes"] = self._safe_note_list(fragrance["heart_notes"])
        fragrance["base_notes"] = self._safe_note_list(fragrance["base_notes"])
        fragrance["featured"] = bool(fragrance["featured"])
        fragrance["is_active"] = bool(fragrance["is_active"])
        fragrance["variants"] = variants
        fragrance["variant_count"] = len(variants)
        fragrance["total_stock"] = sum(int(variant["stock_units"] or 0) for variant in variants)
        prices = [int(variant["price_inr"] or 0) for variant in variants]
        fragrance["min_price"] = min(prices, default=0)
        fragrance["max_price"] = max(prices, default=0)
        return fragrance

    def _normalize_admin_fragrance_fields(
        self,
        fields: dict[str, str],
        *,
        current_slug: str | None,
    ) -> dict[str, Any]:
        brand = self._clean_required(fields.get("brand"), "Brand")
        name = self._clean_required(fields.get("name"), "Fragrance name")
        clean_slug = slugify(fields.get("slug") or f"{brand}-{name}")
        if not clean_slug:
            clean_slug = slugify(f"{brand}-{name}")

        collection_type = self._clean_choice(
            fields.get("collection_type"),
            {"niche", "designer"},
            "Collection",
            default="niche",
        )
        gender = self._clean_choice(fields.get("gender"), {"him", "her", "unisex"}, "Gender", default="unisex")
        family = self._clean_required(fields.get("family"), "Family").lower()
        concentration = self._clean_text(fields.get("concentration")) or "Eau de Parfum"
        origin = self._clean_text(fields.get("origin")) or "Imported"
        signature = self._clean_text(fields.get("signature")) or "Curated for The Scentist."
        top_notes = self._parse_admin_note_list(fields.get("top_notes"))
        heart_notes = self._parse_admin_note_list(fields.get("heart_notes"))
        base_notes = self._parse_admin_note_list(fields.get("base_notes"))
        description = self._clean_text(fields.get("description"))
        if not description:
            all_notes = [*top_notes, *heart_notes, *base_notes]
            note_text = ", ".join(all_notes[:4]) if all_notes else family
            description = f"{brand} {name} is a curated {family} fragrance with {note_text}."

        return {
            "slug": clean_slug,
            "brand": brand,
            "name": name,
            "collection_type": collection_type,
            "gender": gender,
            "family": family,
            "concentration": concentration,
            "origin": origin,
            "description": description,
            "signature": signature,
            "top_notes": top_notes,
            "heart_notes": heart_notes,
            "base_notes": base_notes,
            "accent_from": self._clean_text(fields.get("accent_from")) or "#c2b4a3",
            "accent_to": self._clean_text(fields.get("accent_to")) or "#17120f",
            "image_url": self._clean_text(fields.get("image_url")) or f"/artwork/{clean_slug}.svg",
            "photo_icon_url": self._clean_text(fields.get("photo_icon_url")),
            "artwork_kind": self._clean_text(fields.get("artwork_kind")) or "photo",
            "bottle_size_ml": self._clean_non_negative_int(fields.get("bottle_size_ml"), "Bottle size", default=100),
            "featured": fields.get("featured") == "on",
            "rank": self._clean_non_negative_int(fields.get("rank"), "Rank", default=999),
            "is_active": fields.get("is_active") == "on" or not current_slug,
        }

    def _normalize_admin_variant_fields(self, fragrance_slug: str, fields: dict[str, str]) -> dict[str, Any]:
        sale_type = self._clean_choice(
            fields.get("sale_type"),
            {"retail", "tester", "decant", "partial"},
            "Sale type",
            default="retail",
        )
        size_ml = self._clean_non_negative_int(fields.get("size_ml"), "Size ml", default=100)
        if size_ml <= 0:
            raise ValidationError("Size ml must be greater than zero.")
        if size_ml < MIN_PUBLIC_VARIANT_SIZE_ML:
            raise ValidationError(f"Size ml must be at least {MIN_PUBLIC_VARIANT_SIZE_ML} ml.")
        size_label = self._clean_text(fields.get("size_label")) or f"{size_ml} ml"
        sku = slugify(fields.get("sku") or f"{fragrance_slug}-{sale_type}-{size_ml}")
        if not sku:
            raise ValidationError("SKU is required.")
        price = self._clean_non_negative_int(fields.get("price_inr"), "Price", default=0)
        if price <= 0:
            raise ValidationError("Price must be greater than zero.")
        compare_at = self._clean_non_negative_int(fields.get("compare_at_price_inr"), "Compare at price", default=0)
        stock = self._clean_non_negative_int(fields.get("stock_units"), "Stock", default=0)
        return {
            "sku": sku,
            "sale_type": sale_type,
            "size_label": size_label,
            "size_ml": size_ml,
            "price_inr": price,
            "compare_at_price_inr": compare_at,
            "stock_units": stock,
            "badge": self._clean_text(fields.get("badge")),
            "statement": self._clean_text(fields.get("statement")),
        }

    def _clean_text(self, value: Any) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()

    def _clean_required(self, value: Any, label: str) -> str:
        text = self._clean_text(value)
        if not text:
            raise ValidationError(f"{label} is required.")
        return text

    def _clean_choice(self, value: Any, choices: set[str], label: str, *, default: str) -> str:
        text = self._clean_text(value).lower() or default
        if text not in choices:
            raise ValidationError(f"{label} is invalid.")
        return text

    def _clean_non_negative_int(self, value: Any, label: str, *, default: int) -> int:
        text = self._clean_text(value)
        if not text:
            return default
        try:
            number = int(float(text))
        except ValueError as exc:
            raise ValidationError(f"{label} must be a number.") from exc
        if number < 0:
            raise ValidationError(f"{label} cannot be negative.")
        return number

    def _parse_admin_note_list(self, value: Any) -> list[str]:
        raw_parts = re.split(r"[\n,]+", str(value or ""))
        notes = []
        seen = set()
        for part in raw_parts:
            note = self._clean_text(part)
            key = note.lower()
            if note and key not in seen:
                notes.append(note)
                seen.add(key)
        return notes

    def get_featured(self, limit: int = 8) -> list[dict[str, Any]]:
        return self.list_fragrances(limit=limit, featured_only=True)

    def get_fragrance(self, slug: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM fragrances WHERE slug = ? AND is_active = 1",
                (slug,),
            ).fetchone()
            if row is None:
                return None
            variants = self._variants_by_fragrance(conn, [row["id"]]).get(row["id"], [])
            if not variants:
                return None
            return self._serialize_fragrance(row, variants)

    def get_related_fragrances(self, fragrance: dict[str, Any], limit: int = 3) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM fragrances
                WHERE slug != ?
                  AND is_active = 1
                  AND (brand = ? OR gender = ? OR family = ?)
                  AND EXISTS (
                      SELECT 1
                      FROM variants v
                      WHERE v.fragrance_id = fragrances.id
                        AND v.stock_units > 0
                        AND v.size_ml >= ?
                  )
                ORDER BY featured DESC, rank ASC
                LIMIT ?
                """,
                (
                    fragrance["slug"],
                    fragrance["brand"],
                    fragrance["gender"],
                    fragrance["family"],
                    MIN_PUBLIC_VARIANT_SIZE_ML,
                    limit,
                ),
            ).fetchall()
            ids = [row["id"] for row in rows]
            variants = self._variants_by_fragrance(conn, ids)
            return [self._serialize_fragrance(row, variants.get(row["id"], [])) for row in rows]

    def get_metrics(self) -> dict[str, int]:
        with self.connect() as conn:
            fragrances = conn.execute("SELECT COUNT(*) AS count FROM fragrances WHERE is_active = 1").fetchone()["count"]
            brands = conn.execute("SELECT COUNT(DISTINCT brand) AS count FROM fragrances WHERE is_active = 1").fetchone()["count"]
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
                WHERE is_active = 1
                GROUP BY brand, collection_type
                ORDER BY collection_type ASC, fragrance_count DESC, brand ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_concierge_candidates(self, terms: list[str] | None = None, limit: int = 120) -> list[dict[str, Any]]:
        clean_terms = []
        for term in terms or []:
            normalized = re.sub(r"[^a-z0-9 ]+", " ", term.lower()).strip()
            if len(normalized) >= 3 and normalized not in clean_terms:
                clean_terms.append(normalized)

        def fetch_rows(search_terms: list[str], row_limit: int) -> list[Any]:
            clauses = ["v.stock_units > 0"]
            params: list[Any] = []
            if search_terms:
                searchable_columns = [
                    "f.name",
                    "f.brand",
                    "f.collection_type",
                    "f.gender",
                    "f.family",
                    "f.concentration",
                    "f.description",
                    "f.signature",
                    "f.top_notes",
                    "f.heart_notes",
                    "f.base_notes",
                ]
                term_clauses = []
                for term in search_terms[:12]:
                    like = f"%{term}%"
                    term_clauses.append(
                        "("
                        + " OR ".join(f"LOWER({column}) LIKE ?" for column in searchable_columns)
                        + ")"
                    )
                    params.extend([like] * len(searchable_columns))
                clauses.append("(" + " OR ".join(term_clauses) + ")")

            params.append(row_limit)
            with self.connect() as conn:
                return conn.execute(
                    f"""
                    SELECT
                        f.*,
                        v.id AS variant_id,
                        v.sale_type AS variant_sale_type,
                        v.size_label AS variant_size_label,
                        v.size_ml AS variant_size_ml,
                        v.price_inr AS variant_price_inr,
                        v.stock_units AS variant_stock_units,
                        v.badge AS variant_badge
                    FROM fragrances f
                    JOIN variants v ON v.fragrance_id = f.id
                    WHERE f.is_active = 1 AND {' AND '.join(clauses)}
                    ORDER BY f.featured DESC, f.rank ASC, f.brand ASC, f.name ASC, v.price_inr ASC
                    LIMIT ?
                    """,
                    params,
                ).fetchall()

        rows = fetch_rows(clean_terms, max(limit * 5, 180))
        if len({row["slug"] for row in rows}) < 8 and clean_terms:
            rows = fetch_rows([], max(limit * 5, 180))

        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            slug = row["slug"]
            candidate = grouped.get(slug)
            if candidate is None:
                top_notes = self._safe_note_list(row["top_notes"])
                heart_notes = self._safe_note_list(row["heart_notes"])
                base_notes = self._safe_note_list(row["base_notes"])
                candidate = {
                    "id": row["id"],
                    "slug": slug,
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
                    "notes": [*top_notes, *heart_notes, *base_notes],
                    "image_url": row["image_url"],
                    "photo_icon_url": row["photo_icon_url"],
                    "featured": bool(row["featured"]),
                    "variants": [],
                    "starting_price": 0,
                    "product_path": f"/fragrances/{slug}",
                }
                grouped[slug] = candidate

            if len(candidate["variants"]) < 8:
                candidate["variants"].append(
                    {
                        "id": row["variant_id"],
                        "sale_type": row["variant_sale_type"],
                        "size_label": row["variant_size_label"],
                        "size_ml": row["variant_size_ml"],
                        "price_inr": row["variant_price_inr"],
                        "stock_units": row["variant_stock_units"],
                        "badge": row["variant_badge"],
                    }
                )

        candidates = list(grouped.values())[:limit]
        for candidate in candidates:
            prices = [variant["price_inr"] for variant in candidate["variants"]]
            candidate["starting_price"] = min(prices) if prices else 0
        return candidates

    def _safe_note_list(self, value: str) -> list[str]:
        try:
            parsed = json.loads(value or "[]")
        except json.JSONDecodeError:
            return []
        return [str(item) for item in parsed if str(item).strip()]

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
                    f.photo_icon_url,
                    f.artwork_kind
                FROM variants v
                JOIN fragrances f ON f.id = v.fragrance_id
                WHERE v.id IN ({placeholders})
                  AND f.is_active = 1
                  AND v.size_ml >= ?
                ORDER BY f.rank ASC, v.price_inr ASC
                """,
                [*variant_ids, MIN_PUBLIC_VARIANT_SIZE_ML],
            ).fetchall()
        return [dict(row) for row in rows]

    def preview_order(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        return self._build_checkout_snapshot(items)

    def create_order(
        self,
        payload: dict[str, Any],
        customer_id: int | None = None,
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        idempotency_key = self._normalize_idempotency_key(idempotency_key)
        existing = self.get_order_by_idempotency_key(idempotency_key)
        if existing is not None:
            return existing
        customer = payload.get("customer") or {}
        items = payload.get("items") or []
        self._validate_customer(customer)
        snapshot = self._build_checkout_snapshot(items)

        now = self._now()
        payment_method = customer.get("payment_method", "Cash on Delivery")
        if payment_method != "Cash on Delivery":
            raise ValidationError("Use online checkout for this payment method.")
        payment_status = "offline"
        status = "Confirmed"

        with self.connect() as conn:
            try:
                conn.begin_write()
                existing_number = self._order_number_for_idempotency_key(conn, idempotency_key)
                if existing_number:
                    conn.rollback()
                    existing = self.get_order(existing_number)
                    if existing is None:
                        raise ValidationError("Existing order could not be loaded.")
                    return existing
                self._deduct_stock(conn, snapshot["items"])
                order_number = self._insert_order(
                    conn,
                    customer_id=customer_id,
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
                    stock_reserved=0,
                    reservation_expires_at="",
                    last_error="",
                    idempotency_key=idempotency_key,
                )
                conn.commit()
            except Exception:
                conn.rollback()
                existing = self.get_order_by_idempotency_key(idempotency_key)
                if existing is not None:
                    return existing
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
        customer_id: int | None = None,
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        idempotency_key = self._normalize_idempotency_key(idempotency_key)
        existing = self.get_order_by_idempotency_key(idempotency_key)
        if existing is not None:
            return existing
        self._validate_customer(customer)
        snapshot = self._build_checkout_snapshot(items)
        now = self._now()

        with self.connect() as conn:
            try:
                conn.begin_write()
                existing_number = self._order_number_for_idempotency_key(conn, idempotency_key)
                if existing_number:
                    conn.rollback()
                    existing = self.get_order(existing_number)
                    if existing is None:
                        raise ValidationError("Existing payment order could not be loaded.")
                    return existing
                self._deduct_stock(conn, snapshot["items"])
                cursor_order_number = self._insert_order(
                    conn,
                    customer_id=customer_id,
                    customer=customer,
                    snapshot=snapshot,
                    payment_method=customer["payment_method"],
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
                    stock_reserved=1,
                    reservation_expires_at=self._reservation_expires_at(now),
                    last_error="",
                    idempotency_key=idempotency_key,
                )
                conn.commit()
            except Exception:
                conn.rollback()
                existing = self.get_order_by_idempotency_key(idempotency_key)
                if existing is not None:
                    return existing
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

                needs_stock_deduction = not int(order_row["stock_reserved"] or 0)
                review_error = ""
                if needs_stock_deduction:
                    item_rows = conn.execute(
                        """
                        SELECT variant_id, quantity
                        FROM order_items
                        WHERE order_id = ?
                        ORDER BY id ASC
                        """,
                        (order_row["id"],),
                    ).fetchall()
                    try:
                        self._deduct_stock(conn, [dict(row) for row in item_rows], compact=True)
                    except ValidationError as exc:
                        review_error = f"Payment captured but stock could not be reserved automatically: {exc}"

                conn.execute(
                    """
                    UPDATE orders
                    SET payment_status = 'paid',
                        status = ?,
                        gateway_payment_id = ?,
                        gateway_signature = ?,
                        payment_amount_inr = total_inr,
                        paid_at = ?,
                        stock_reserved = 0,
                        reservation_expires_at = '',
                        last_error = ?
                    WHERE id = ?
                    """,
                    (
                        "Review Required" if review_error else "Confirmed",
                        gateway_payment_id,
                        gateway_signature,
                        self._now(),
                        review_error,
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

                needs_stock_deduction = not int(order_row["stock_reserved"] or 0)
                review_error = ""
                if needs_stock_deduction:
                    item_rows = conn.execute(
                        "SELECT variant_id, quantity FROM order_items WHERE order_id = ? ORDER BY id ASC",
                        (order_row["id"],),
                    ).fetchall()
                    try:
                        self._deduct_stock(conn, [dict(row) for row in item_rows], compact=True)
                    except ValidationError as exc:
                        review_error = f"Payment captured but stock could not be reserved automatically: {exc}"
                conn.execute(
                    """
                    UPDATE orders
                    SET payment_status = 'paid',
                        status = ?,
                        gateway_payment_id = ?,
                        payment_amount_inr = total_inr,
                        paid_at = ?,
                        stock_reserved = 0,
                        reservation_expires_at = '',
                        last_error = ?
                    WHERE id = ?
                    """,
                    (
                        "Review Required" if review_error else "Confirmed",
                        gateway_payment_id,
                        self._now(),
                        review_error,
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
            try:
                conn.begin_write()
                order_row = conn.execute(
                    "SELECT * FROM orders WHERE order_number = ?",
                    (order_number,),
                ).fetchone()
                if order_row and order_row["payment_status"] != "paid":
                    self._release_reserved_stock(conn, dict(order_row))
                    conn.execute(
                        """
                        UPDATE orders
                        SET payment_status = 'failed',
                            status = 'Payment Failed',
                            stock_reserved = 0,
                            reservation_expires_at = '',
                            last_error = ?
                        WHERE order_number = ? AND payment_status != 'paid'
                        """,
                        (reason[:400], order_number),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def list_orders(self, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, int(limit or 100))
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT order_number, customer_name, email, phone, total_inr, item_count,
                       status, payment_method, payment_status, stock_reserved,
                       reservation_expires_at, courier_name, tracking_number, tracking_url,
                       status_updated_at, created_at
                FROM orders
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def expire_stale_reservations(self) -> int:
        now = self._now()
        expired_count = 0
        with self.connect() as conn:
            try:
                conn.begin_write()
                rows = conn.execute(
                    """
                    SELECT *
                    FROM orders
                    WHERE payment_gateway = 'razorpay'
                      AND payment_status = 'created'
                      AND stock_reserved = 1
                      AND reservation_expires_at != ''
                      AND reservation_expires_at < ?
                    """,
                    (now,),
                ).fetchall()
                for row in rows:
                    order = dict(row)
                    self._release_reserved_stock(conn, order)
                    conn.execute(
                        """
                        UPDATE orders
                        SET payment_status = 'expired',
                            status = 'Payment Expired',
                            stock_reserved = 0,
                            reservation_expires_at = '',
                            last_error = 'Payment reservation expired before capture.'
                        WHERE id = ?
                        """,
                        (order["id"],),
                    )
                    expired_count += 1
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return expired_count

    def update_order_status(self, order_number: str, status: str | dict[str, Any]) -> dict[str, Any]:
        allowed_statuses = {
            "Pending Payment",
            "Confirmed",
            "Packed",
            "Shipped",
            "Delivered",
            "Cancelled",
            "Review Required",
        }
        if isinstance(status, dict):
            fields = status
            status_value = self._clean_text(fields.get("status"))
            courier_name = self._clean_text(fields.get("courier_name"))
            tracking_number = self._clean_text(fields.get("tracking_number"))
            tracking_url = self._clean_text(fields.get("tracking_url"))
            admin_notes = self._clean_text(fields.get("admin_notes"))
        else:
            fields = {}
            status_value = self._clean_text(status)
            courier_name = ""
            tracking_number = ""
            tracking_url = ""
            admin_notes = ""

        if status_value not in allowed_statuses:
            raise ValidationError("Unsupported order status.")
        if tracking_url and not tracking_url.startswith(("https://", "http://")):
            raise ValidationError("Tracking URL must start with http:// or https://.")

        with self.connect() as conn:
            try:
                conn.begin_write()
                order_row = conn.execute(
                    "SELECT * FROM orders WHERE order_number = ?",
                    (order_number,),
                ).fetchone()
                if order_row is None:
                    raise ValidationError("Order not found.")
                order = dict(order_row)
                now = self._now()
                shipped_at = order.get("shipped_at", "")
                delivered_at = order.get("delivered_at", "")
                cancelled_at = order.get("cancelled_at", "")
                if status_value == "Shipped" and not shipped_at:
                    shipped_at = now
                if status_value == "Delivered" and not delivered_at:
                    delivered_at = now
                    shipped_at = shipped_at or now
                if status_value == "Cancelled" and not cancelled_at:
                    cancelled_at = now

                if status_value == "Cancelled" and order["payment_status"] != "paid":
                    self._release_reserved_stock(conn, order)
                    conn.execute(
                        """
                        UPDATE orders
                        SET status = ?,
                            payment_status = CASE WHEN payment_status = 'created' THEN 'cancelled' ELSE payment_status END,
                            stock_reserved = 0,
                            reservation_expires_at = '',
                            courier_name = ?,
                            tracking_number = ?,
                            tracking_url = ?,
                            admin_notes = ?,
                            status_updated_at = ?,
                            status_notification_sent_at = '',
                            shipped_at = ?,
                            delivered_at = ?,
                            cancelled_at = ?
                        WHERE order_number = ?
                        """,
                        (
                            status_value,
                            courier_name,
                            tracking_number,
                            tracking_url,
                            admin_notes,
                            now,
                            shipped_at,
                            delivered_at,
                            cancelled_at,
                            order_number,
                        ),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE orders
                        SET status = ?,
                            courier_name = ?,
                            tracking_number = ?,
                            tracking_url = ?,
                            admin_notes = ?,
                            status_updated_at = ?,
                            status_notification_sent_at = '',
                            shipped_at = ?,
                            delivered_at = ?,
                            cancelled_at = ?
                        WHERE order_number = ?
                        """,
                        (
                            status_value,
                            courier_name,
                            tracking_number,
                            tracking_url,
                            admin_notes,
                            now,
                            shipped_at,
                            delivered_at,
                            cancelled_at,
                            order_number,
                        ),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

        order = self.get_order(order_number)
        if order is None:
            raise ValidationError("Order could not be loaded after update.")
        return order

    def mark_order_notified(self, order_number: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE orders
                SET notification_sent_at = ?
                WHERE order_number = ? AND notification_sent_at = ''
                """,
                (self._now(), order_number),
            )
            conn.commit()

    def mark_order_status_notified(self, order_number: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE orders
                SET status_notification_sent_at = ?
                WHERE order_number = ? AND status_notification_sent_at = ''
                """,
                (self._now(), order_number),
            )
            conn.commit()

    def enqueue_order_notification(self, order_number: str, event_type: str) -> None:
        if event_type not in {"order_received", "order_status"}:
            raise ValidationError("Unsupported notification event.")
        order = self.get_order(order_number)
        if order is None:
            raise ValidationError("Order not found for notification.")
        marker = order.get("status_updated_at") or order.get("status") or "created"
        dedupe_key = f"{event_type}:{order_number}:{marker}" if event_type == "order_status" else f"{event_type}:{order_number}"
        now = self._now()
        with self.connect() as conn:
            if conn.dialect == "postgres":
                conn.execute(
                    """
                    INSERT INTO notification_outbox
                        (dedupe_key, event_type, order_number, status, attempts, available_at, created_at)
                    VALUES (?, ?, ?, 'pending', 0, ?, ?)
                    ON CONFLICT (dedupe_key) DO NOTHING
                    """,
                    (dedupe_key, event_type, order_number, now, now),
                )
            else:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO notification_outbox
                        (dedupe_key, event_type, order_number, status, attempts, available_at, created_at)
                    VALUES (?, ?, ?, 'pending', 0, ?, ?)
                    """,
                    (dedupe_key, event_type, order_number, now, now),
                )
            conn.commit()

    def claim_notification(self) -> dict[str, Any] | None:
        now = self._now()
        stale = (datetime.utcnow() - timedelta(minutes=10)).replace(microsecond=0).isoformat() + "Z"
        with self.connect() as conn:
            try:
                conn.begin_write()
                conn.execute(
                    """
                    UPDATE notification_outbox
                    SET status = 'pending', locked_at = ''
                    WHERE status = 'processing' AND locked_at != '' AND locked_at < ?
                    """,
                    (stale,),
                )
                query = """
                    SELECT id, event_type, order_number, attempts
                    FROM notification_outbox
                    WHERE status = 'pending' AND available_at <= ?
                    ORDER BY id ASC
                    LIMIT 1
                """
                if conn.dialect == "postgres":
                    query += " FOR UPDATE SKIP LOCKED"
                row = conn.execute(query, (now,)).fetchone()
                if row is None:
                    conn.commit()
                    return None
                conn.execute(
                    """
                    UPDATE notification_outbox
                    SET status = 'processing', locked_at = ?, attempts = attempts + 1
                    WHERE id = ?
                    """,
                    (now, row["id"]),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        event = dict(row)
        event["order"] = self.get_order(event["order_number"])
        return event

    def complete_notification(self, notification_id: int, order_number: str, event_type: str) -> None:
        now = self._now()
        with self.connect() as conn:
            try:
                conn.begin_write()
                conn.execute(
                    """
                    UPDATE notification_outbox
                    SET status = 'sent', sent_at = ?, locked_at = '', last_error = ''
                    WHERE id = ? AND status = 'processing'
                    """,
                    (now, notification_id),
                )
                marker_column = "notification_sent_at" if event_type == "order_received" else "status_notification_sent_at"
                conn.execute(
                    f"UPDATE orders SET {marker_column} = ? WHERE order_number = ?",
                    (now, order_number),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def retry_notification(self, notification_id: int, attempts: int, error: str) -> None:
        delay_minutes = min(60, 2 ** min(max(1, attempts), 6))
        available_at = (datetime.utcnow() + timedelta(minutes=delay_minutes)).replace(microsecond=0).isoformat() + "Z"
        final_status = "failed" if attempts >= 8 else "pending"
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE notification_outbox
                SET status = ?, available_at = ?, locked_at = '', last_error = ?
                WHERE id = ?
                """,
                (final_status, available_at, str(error)[:500], notification_id),
            )
            conn.commit()

    def enqueue_email_verification(self, customer: dict[str, Any], verification_url: str) -> None:
        now = self._now()
        dedupe_key = f"verification:{customer['id']}:{hashlib.sha256(verification_url.encode('utf-8')).hexdigest()}"
        with self.connect() as conn:
            insert_prefix = "INSERT" if conn.dialect == "postgres" else "INSERT OR IGNORE"
            conflict_clause = " ON CONFLICT (dedupe_key) DO NOTHING" if conn.dialect == "postgres" else ""
            conn.execute(
                f"""
                {insert_prefix} INTO account_email_outbox
                    (dedupe_key, customer_id, recipient, full_name, verification_url,
                     status, attempts, available_at, created_at)
                VALUES (?, ?, ?, ?, ?, 'pending', 0, ?, ?){conflict_clause}
                """,
                (
                    dedupe_key,
                    customer["id"],
                    customer["email"],
                    customer["full_name"],
                    verification_url,
                    now,
                    now,
                ),
            )
            conn.commit()

    def claim_email_verification(self) -> dict[str, Any] | None:
        now = self._now()
        stale = (datetime.utcnow() - timedelta(minutes=10)).replace(microsecond=0).isoformat() + "Z"
        with self.connect() as conn:
            try:
                conn.begin_write()
                conn.execute(
                    """
                    UPDATE account_email_outbox
                    SET status = 'pending', locked_at = ''
                    WHERE status = 'processing' AND locked_at != '' AND locked_at < ?
                    """,
                    (stale,),
                )
                query = """
                    SELECT id, recipient, full_name, verification_url, attempts
                    FROM account_email_outbox
                    WHERE status = 'pending' AND available_at <= ?
                    ORDER BY id ASC
                    LIMIT 1
                """
                if conn.dialect == "postgres":
                    query += " FOR UPDATE SKIP LOCKED"
                row = conn.execute(query, (now,)).fetchone()
                if row is None:
                    conn.commit()
                    return None
                conn.execute(
                    """
                    UPDATE account_email_outbox
                    SET status = 'processing', locked_at = ?, attempts = attempts + 1
                    WHERE id = ?
                    """,
                    (now, row["id"]),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return dict(row)

    def complete_email_verification(self, notification_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE account_email_outbox
                SET status = 'sent', sent_at = ?, locked_at = '', last_error = ''
                WHERE id = ? AND status = 'processing'
                """,
                (self._now(), notification_id),
            )
            conn.commit()

    def retry_email_verification(self, notification_id: int, attempts: int, error: str) -> None:
        delay_minutes = min(60, 2 ** min(max(1, attempts), 6))
        available_at = (datetime.utcnow() + timedelta(minutes=delay_minutes)).replace(microsecond=0).isoformat() + "Z"
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE account_email_outbox
                SET status = ?, available_at = ?, locked_at = '', last_error = ?
                WHERE id = ?
                """,
                ("failed" if attempts >= 8 else "pending", available_at, str(error)[:500], notification_id),
            )
            conn.commit()

    def get_readiness_metrics(self) -> dict[str, int]:
        with self.connect() as conn:
            fragrance_row = conn.execute(
                """
                SELECT
                    COUNT(*) AS active_fragrances,
                    SUM(CASE WHEN image_url LIKE '/artwork/%' THEN 1 ELSE 0 END) AS placeholder_images,
                    SUM(CASE WHEN image_url = '' OR image_url IS NULL THEN 1 ELSE 0 END) AS missing_images
                FROM fragrances
                WHERE is_active = 1
                """
            ).fetchone()
            variant_row = conn.execute(
                """
                SELECT
                    COUNT(*) AS active_variants,
                    SUM(CASE WHEN stock_units <= 0 THEN 1 ELSE 0 END) AS out_of_stock_variants,
                    SUM(CASE WHEN stock_units > 0 AND stock_units <= 2 THEN 1 ELSE 0 END) AS low_stock_variants
                FROM variants v
                JOIN fragrances f ON f.id = v.fragrance_id
                WHERE f.is_active = 1
                """
            ).fetchone()
            no_variant_row = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM fragrances f
                WHERE f.is_active = 1
                  AND NOT EXISTS (SELECT 1 FROM variants v WHERE v.fragrance_id = f.id)
                """
            ).fetchone()
            review_row = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM orders
                WHERE status = 'Review Required'
                   OR payment_status IN ('created', 'failed')
                """
            ).fetchone()

        fragrance = dict(fragrance_row or {})
        variant = dict(variant_row or {})
        no_variant = dict(no_variant_row or {})
        review = dict(review_row or {})
        return {
            "active_fragrances": int(fragrance.get("active_fragrances") or 0),
            "placeholder_images": int(fragrance.get("placeholder_images") or 0),
            "missing_images": int(fragrance.get("missing_images") or 0),
            "active_variants": int(variant.get("active_variants") or 0),
            "out_of_stock_variants": int(variant.get("out_of_stock_variants") or 0),
            "low_stock_variants": int(variant.get("low_stock_variants") or 0),
            "fragrances_without_variants": int(no_variant.get("count") or 0),
            "orders_requiring_attention": int(review.get("count") or 0),
        }

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
        order["public_path"] = f"/order/{order['order_number']}/{order['public_token']}"
        return order

    def get_public_order(self, order_number: str, public_token: str) -> dict[str, Any] | None:
        order = self.get_order(order_number)
        if not order or not secrets.compare_digest(order.get("public_token", ""), public_token):
            return None
        return order

    def _build_checkout_snapshot(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        if not items:
            raise ValidationError("Your cart is empty.")
        if not isinstance(items, list) or len(items) > 50:
            raise ValidationError("A cart can contain at most 50 fragrance options.")

        with self.connect() as conn:
            normalized_items: list[dict[str, Any]] = []
            subtotal = 0
            seen_variant_ids: set[int] = set()

            for item in items:
                if not isinstance(item, dict):
                    raise ValidationError("Every cart item must be an object.")
                variant_id = self._checkout_integer(item.get("variant_id"), "Variant")
                quantity = self._checkout_integer(item.get("quantity"), "Quantity")
                if variant_id < 1 or quantity < 1 or quantity > 20:
                    raise ValidationError("Every cart item needs a valid option and a quantity from 1 to 20.")
                if variant_id in seen_variant_ids:
                    raise ValidationError("Duplicate fragrance options are not allowed in the cart.")
                seen_variant_ids.add(variant_id)

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
                      AND f.is_active = 1
                      AND v.size_ml >= ?
                    """,
                    (variant_id, MIN_PUBLIC_VARIANT_SIZE_ML),
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
        customer_id: int | None,
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
        stock_reserved: int,
        reservation_expires_at: str,
        last_error: str,
        idempotency_key: str = "",
    ) -> str:
        order_number = self._generate_order_number(conn)
        public_token = self._generate_public_token(conn)
        conn.execute(
            """
            INSERT INTO orders (
                customer_id, order_number, public_token, idempotency_key, customer_name, email, phone, shipping_line1, shipping_line2,
                city, state, postal_code, country, payment_method, payment_gateway, payment_status,
                gateway_order_id, gateway_payment_id, gateway_signature, payment_amount_inr,
                delivery_notes, subtotal_inr, shipping_inr, total_inr, item_count, status,
                stock_reserved, reservation_expires_at, initiated_at, paid_at, notification_sent_at,
                last_error, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                customer_id,
                order_number,
                public_token,
                idempotency_key,
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
                stock_reserved,
                reservation_expires_at,
                initiated_at,
                paid_at,
                "",
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

    def _release_reserved_stock(self, conn: DatabaseConnection, order: dict[str, Any]) -> None:
        if not int(order.get("stock_reserved") or 0):
            return

        item_rows = conn.execute(
            "SELECT variant_id, quantity FROM order_items WHERE order_id = ? ORDER BY id ASC",
            (order["id"],),
        ).fetchall()
        for item in item_rows:
            conn.execute(
                "UPDATE variants SET stock_units = stock_units + ? WHERE id = ?",
                (item["quantity"], item["variant_id"]),
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
              AND size_ml >= ?
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
            [*fragrance_ids, MIN_PUBLIC_VARIANT_SIZE_ML],
        ).fetchall()

        grouped: dict[int, list[dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(row["fragrance_id"], []).append(dict(row))
        return grouped

    def _serialize_fragrance(self, row: Any, variants: list[dict[str, Any]]) -> dict[str, Any]:
        top_notes = self._safe_note_list(row["top_notes"])
        heart_notes = self._safe_note_list(row["heart_notes"])
        base_notes = self._safe_note_list(row["base_notes"])
        available_variants = [variant for variant in variants if variant["stock_units"] > 0]
        available_prices = [int(variant["price_inr"]) for variant in available_variants]
        starting_price = min(available_prices, default=0)
        ending_price = max(available_prices, default=0)
        has_price_range = bool(available_prices) and starting_price != ending_price
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
            "photo_icon_url": row["photo_icon_url"],
            "artwork_kind": row["artwork_kind"],
            "bottle_size_ml": row["bottle_size_ml"],
            "featured": bool(row["featured"]),
            "is_active": bool(row["is_active"]),
            "variants": available_variants,
            "starting_price": starting_price,
            "ending_price": ending_price,
            "has_price_range": has_price_range,
            "quick_add_variant": quick_add,
            "sale_types": sorted({variant["sale_type"] for variant in available_variants}),
        }

    def _validate_customer(self, customer: dict[str, Any]) -> None:
        if not isinstance(customer, dict):
            raise ValidationError("Customer details must be an object.")
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

        field_limits = {
            "customer_name": (2, 100),
            "email": (5, 254),
            "shipping_line1": (8, 200),
            "shipping_line2": (0, 200),
            "city": (2, 100),
            "state": (2, 100),
            "country": (2, 100),
            "delivery_notes": (0, 500),
        }
        for field, (minimum, maximum) in field_limits.items():
            default = self.settings.default_country if field == "country" else ""
            value = str(customer.get(field, default)).strip()
            if len(value) < minimum or len(value) > maximum:
                label = field.replace("_", " ").title()
                raise ValidationError(f"{label} must be between {minimum} and {maximum} characters.")

        email = str(customer["email"]).strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise ValidationError("Please enter a valid email address.")

        phone = re.sub(r"\D+", "", str(customer["phone"]))
        if len(phone) < 10 or len(phone) > 15:
            raise ValidationError("Please enter a valid phone number.")
        customer["phone"] = phone

        postal = re.sub(r"\s+", "", str(customer["postal_code"]))
        country = str(customer.get("country", self.settings.default_country)).strip() or self.settings.default_country
        if country.lower() == "india" and not re.fullmatch(r"[1-9][0-9]{5}", postal):
            raise ValidationError("Please enter a valid 6 digit Indian postal code.")
        if country.lower() != "india" and not re.fullmatch(r"[A-Za-z0-9-]{5,12}", postal):
            raise ValidationError("Please enter a valid postal code.")
        customer["postal_code"] = postal

        customer["customer_name"] = str(customer["customer_name"]).strip()
        customer["email"] = email
        customer["shipping_line1"] = str(customer["shipping_line1"]).strip()
        customer["shipping_line2"] = str(customer.get("shipping_line2", "")).strip()
        customer["city"] = str(customer["city"]).strip()
        customer["state"] = str(customer["state"]).strip()
        customer["country"] = country
        customer["delivery_notes"] = str(customer.get("delivery_notes", "")).strip()
        customer["payment_method"] = str(customer["payment_method"]).strip()
        if customer["payment_method"] not in {"Cash on Delivery", "UPI", "Netbanking", "Credit/Debit Card"}:
            raise ValidationError("Unsupported payment method.")

    def _checkout_integer(self, value: Any, label: str) -> int:
        if isinstance(value, bool):
            raise ValidationError(f"{label} must be a whole number.")
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            if not value.is_integer():
                raise ValidationError(f"{label} must be a whole number.")
            return int(value)
        clean = str(value or "").strip()
        if not re.fullmatch(r"[0-9]+", clean):
            raise ValidationError(f"{label} must be a whole number.")
        return int(clean)

    def _generate_order_number(self, conn: DatabaseConnection) -> str:
        while True:
            stamp = datetime.utcnow().strftime("%Y%m%d")
            suffix = secrets.token_hex(6).upper()
            order_number = f"{self.settings.order_prefix}-{stamp}-{suffix}"
            exists = conn.execute(
                "SELECT 1 FROM orders WHERE order_number = ? LIMIT 1",
                (order_number,),
            ).fetchone()
            if not exists:
                return order_number

    def _normalize_idempotency_key(self, value: str) -> str:
        key = str(value or "").strip()
        if len(key) > 128:
            raise ValidationError("Idempotency key is too long.")
        return key

    def _order_number_for_idempotency_key(
        self, conn: DatabaseConnection, idempotency_key: str
    ) -> str:
        if not idempotency_key:
            return ""
        row = conn.execute(
            "SELECT order_number FROM orders WHERE idempotency_key = ? LIMIT 1",
            (idempotency_key,),
        ).fetchone()
        return str(row["order_number"]) if row else ""

    def get_order_by_idempotency_key(self, idempotency_key: str) -> dict[str, Any] | None:
        idempotency_key = self._normalize_idempotency_key(idempotency_key)
        if not idempotency_key:
            return None
        with self.connect() as conn:
            order_number = self._order_number_for_idempotency_key(conn, idempotency_key)
        return self.get_order(order_number) if order_number else None

    def _reservation_expires_at(self, now: str | None = None) -> str:
        base = datetime.utcnow()
        if now:
            try:
                base = datetime.fromisoformat(now.removesuffix("Z"))
            except ValueError:
                pass
        expires_at = base + timedelta(minutes=max(5, self.settings.payment_reservation_minutes))
        return expires_at.replace(microsecond=0).isoformat() + "Z"

    def _generate_public_token(self, conn: DatabaseConnection) -> str:
        while True:
            token = secrets.token_urlsafe(24)
            exists = conn.execute(
                "SELECT 1 FROM orders WHERE public_token = ? LIMIT 1",
                (token,),
            ).fetchone()
            if not exists:
                return token

    def _now(self) -> str:
        return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
