from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import quote_plus


def env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_text(name: str, default: str, *, retired_value: str = "") -> str:
    value = os.getenv(name, default).strip()
    if not value or value == retired_value:
        return default
    return value


@dataclass(frozen=True)
class Settings:
    site_name: str
    host: str
    port: int
    base_url: str
    app_env: str
    currency: str
    default_country: str
    shipping_fee_inr: int
    free_shipping_threshold_inr: int
    order_prefix: str
    database_url: str
    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: str
    db_sslmode: str
    database_pool_min_size: int
    database_pool_max_size: int
    database_connect_timeout_seconds: int
    database_statement_timeout_ms: int
    sqlite_database_path: str
    sqlite_busy_timeout_ms: int
    max_request_body_bytes: int
    static_cache_max_age_seconds: int
    auto_seed_catalog: bool
    razorpay_key_id: str
    razorpay_key_secret: str
    razorpay_webhook_secret: str
    enable_manual_checkout: bool
    payment_reservation_minutes: int
    app_secret_key: str
    redis_url: str
    admin_token: str
    admin_email: str
    notification_from_email: str
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    smtp_use_tls: bool
    support_email: str
    support_phone: str
    business_address: str
    ai_concierge_enabled: bool
    openai_api_key: str
    openai_model: str

    @property
    def razorpay_enabled(self) -> bool:
        return bool(self.razorpay_key_id and self.razorpay_key_secret)

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def database_engine(self) -> str:
        if self.resolved_database_url.lower().startswith(("postgres://", "postgresql://")):
            return "postgres"
        return "sqlite"

    @property
    def enforce_hsts(self) -> bool:
        return self.is_production and self.base_url.startswith("https://")

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url

        if self.db_host and self.db_name and self.db_user and self.db_password:
            user = quote_plus(self.db_user)
            password = quote_plus(self.db_password)
            sslmode = f"?sslmode={quote_plus(self.db_sslmode)}" if self.db_sslmode else ""
            return f"postgresql://{user}:{password}@{self.db_host}:{self.db_port}/{self.db_name}{sslmode}"

        return ""


def load_settings() -> Settings:
    return Settings(
        site_name=env_text("SITE_NAME", "The Scentist", retired_value="Allure Alchemy"),
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8780")),
        base_url=os.getenv("BASE_URL", "").rstrip("/"),
        app_env=os.getenv("APP_ENV", "development"),
        currency=os.getenv("STORE_CURRENCY", "INR"),
        default_country=os.getenv("STORE_DEFAULT_COUNTRY", "India"),
        shipping_fee_inr=int(os.getenv("SHIPPING_FEE_INR", "350")),
        free_shipping_threshold_inr=int(os.getenv("FREE_SHIPPING_THRESHOLD_INR", "12500")),
        order_prefix=env_text("ORDER_PREFIX", "TSC", retired_value="ALR"),
        database_url=os.getenv("DATABASE_URL", "").strip(),
        db_host=os.getenv("DB_HOST", "").strip(),
        db_port=int(os.getenv("DB_PORT", "5432")),
        db_name=os.getenv("DB_NAME", "").strip(),
        db_user=os.getenv("DB_USER", "").strip(),
        db_password=os.getenv("DB_PASSWORD", "").strip(),
        db_sslmode=os.getenv("DB_SSLMODE", "require").strip(),
        database_pool_min_size=int(os.getenv("DATABASE_POOL_MIN_SIZE", "2")),
        database_pool_max_size=int(os.getenv("DATABASE_POOL_MAX_SIZE", "12")),
        database_connect_timeout_seconds=int(os.getenv("DATABASE_CONNECT_TIMEOUT_SECONDS", "10")),
        database_statement_timeout_ms=int(os.getenv("DATABASE_STATEMENT_TIMEOUT_MS", "5000")),
        sqlite_database_path=os.getenv("SQLITE_DATABASE_PATH", "data/perfumery.sqlite3").strip(),
        sqlite_busy_timeout_ms=int(os.getenv("SQLITE_BUSY_TIMEOUT_MS", "5000")),
        max_request_body_bytes=int(os.getenv("MAX_REQUEST_BODY_BYTES", "5242880")),
        static_cache_max_age_seconds=int(os.getenv("STATIC_CACHE_MAX_AGE_SECONDS", "86400")),
        auto_seed_catalog=env_flag("AUTO_SEED_CATALOG", False),
        razorpay_key_id=os.getenv("RAZORPAY_KEY_ID", "").strip(),
        razorpay_key_secret=os.getenv("RAZORPAY_KEY_SECRET", "").strip(),
        razorpay_webhook_secret=os.getenv("RAZORPAY_WEBHOOK_SECRET", "").strip(),
        enable_manual_checkout=env_flag("ENABLE_MANUAL_CHECKOUT", True),
        payment_reservation_minutes=int(os.getenv("PAYMENT_RESERVATION_MINUTES", "30")),
        app_secret_key=os.getenv("APP_SECRET_KEY", "").strip(),
        redis_url=os.getenv("REDIS_URL", "").strip(),
        admin_token=os.getenv("ADMIN_TOKEN", "").strip(),
        admin_email=os.getenv("ADMIN_EMAIL", "").strip(),
        notification_from_email=os.getenv("NOTIFICATION_FROM_EMAIL", "").strip(),
        smtp_host=os.getenv("SMTP_HOST", "").strip(),
        smtp_port=int(os.getenv("SMTP_PORT", "587")),
        smtp_username=os.getenv("SMTP_USERNAME", "").strip(),
        smtp_password=os.getenv("SMTP_PASSWORD", "").strip(),
        smtp_use_tls=env_flag("SMTP_USE_TLS", True),
        support_email=os.getenv("SUPPORT_EMAIL", "").strip(),
        support_phone=os.getenv("SUPPORT_PHONE", "").strip(),
        business_address=os.getenv("BUSINESS_ADDRESS", "").strip(),
        ai_concierge_enabled=env_flag("AI_CONCIERGE_ENABLED", True),
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini").strip() or "gpt-4.1-mini",
    )
