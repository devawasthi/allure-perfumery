from __future__ import annotations

import csv
import io
import hashlib
import hmac
import json
import logging
import mimetypes
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from email.parser import BytesParser
from email.policy import default as email_policy
from http import HTTPStatus
from pathlib import Path
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, unquote, urlencode
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from jinja2 import Environment, FileSystemLoader, select_autoescape

from perfumery_app.artwork import build_fragrance_artwork
from perfumery_app.catalog_seed import slugify
from perfumery_app.concierge import ScentConcierge
from perfumery_app.config import load_settings
from perfumery_app.database import Database, ValidationError
from perfumery_app.notifications import OrderNotifier
from perfumery_app.payments import PaymentGatewayError, RazorpayClient
from perfumery_app.rate_limit import RateLimiter

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional local helper
    def load_dotenv(*args, **kwargs):
        return False


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

settings = load_settings()
if settings.is_production and settings.razorpay_enabled and not settings.razorpay_webhook_secret:
    raise RuntimeError("RAZORPAY_WEBHOOK_SECRET is required in production when Razorpay is enabled.")
if settings.is_production and len(settings.app_secret_key) < 32:
    raise RuntimeError("APP_SECRET_KEY must contain at least 32 characters in production.")
APP_HMAC_SECRET = settings.app_secret_key or settings.admin_token or secrets.token_urlsafe(32)

TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
ASSETS_DIR = STATIC_DIR / "assets"
configured_sqlite_path = Path(settings.sqlite_database_path or "data/perfumery.sqlite3")
DATABASE_PATH = configured_sqlite_path if configured_sqlite_path.is_absolute() else BASE_DIR / configured_sqlite_path


def build_asset_version() -> str:
    digest = hashlib.sha256()
    for file_path in (STATIC_DIR / "styles.css", STATIC_DIR / "app.js"):
        try:
            digest.update(file_path.read_bytes())
        except FileNotFoundError:
            continue
    return digest.hexdigest()[:12]


ASSET_VERSION = os.getenv("ASSET_VERSION", "").strip() or build_asset_version()

db = Database(DATABASE_PATH, settings)
db.initialize()
razorpay = RazorpayClient(settings.razorpay_key_id, settings.razorpay_key_secret)
notifier = OrderNotifier(settings)
concierge = ScentConcierge(settings, db)
rate_limiter = RateLimiter(settings.redis_url)
ONLINE_PAYMENT_METHODS = {"UPI", "Netbanking", "Credit/Debit Card"}

BRAND_WORDMARKS: dict[str, tuple[str, list[str]]] = {
    "acqua di parma": ("acqua-di-parma", ["ACQUA", "DI PARMA"]),
    "ajmal": ("ajmal", ["AJMAL"]),
    "bvlgari": ("bvlgari", ["BVLGARI"]),
    "byredo": ("byredo", ["BYREDO"]),
    "carolina herrera": ("carolina-herrera", ["CAROLINA", "HERRERA"]),
    "chanel": ("chanel", ["CHANEL"]),
    "christian dior": ("dior", ["DIOR"]),
    "diptyque": ("diptyque", ["DIPTYQUE"]),
    "dolce & gabbana": ("dolce-gabbana", ["DOLCE", "& GABBANA"]),
    "frederic malle": ("frederic-malle", ["FREDERIC", "MALLE"]),
    "giorgio armani": ("giorgio-armani", ["GIORGIO", "ARMANI"]),
    "guerlain": ("guerlain", ["GUERLAIN"]),
    "gucci": ("gucci", ["GUCCI"]),
    "hermes": ("hermes", ["HERMES"]),
    "kilian": ("kilian", ["KILIAN"]),
    "lancome": ("lancome", ["LANCOME"]),
    "maison francis kurkdjian": ("mfk", ["MAISON", "FRANCIS", "KURKDJIAN"]),
    "mfk": ("mfk", ["MAISON", "FRANCIS", "KURKDJIAN"]),
    "penhaligon's": ("penhaligons", ["PENHALIGON'S"]),
    "prada": ("prada", ["PRADA"]),
    "roja": ("roja", ["ROJA"]),
    "serge lutens": ("serge-lutens", ["SERGE", "LUTENS"]),
    "tom ford": ("tom-ford", ["TOM", "FORD"]),
    "valentino": ("valentino", ["VALENTINO"]),
    "xerjoff": ("xerjoff", ["XERJOFF"]),
    "ysl": ("ysl", ["YSL"]),
}

env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)

logger = logging.getLogger("allure_alchemy")


class RequestTooLargeError(ValueError):
    """Raised when the request body exceeds the configured limit."""


def money(value: int) -> str:
    return f"INR {value:,.0f}"


def titleize_enum(value: str) -> str:
    return value.replace("-", " ").replace("_", " ").title()


env.filters["money"] = money
env.filters["titleize_enum"] = titleize_enum


def notify_order_once(order: dict) -> None:
    try:
        db.enqueue_order_notification(order["order_number"], "order_received")
    except Exception:
        logger.exception("Unable to enqueue order notification for %s", order.get("order_number"))


def notify_order_status_once(order: dict) -> None:
    try:
        db.enqueue_order_notification(order["order_number"], "order_status")
    except Exception:
        logger.exception("Unable to enqueue order status notification for %s", order.get("order_number"))


def first_value(query: dict[str, list[str]], key: str) -> str:
    return (query.get(key) or [""])[0].strip()


def render_template(name: str, context: dict) -> bytes:
    template = env.get_template(name)
    return template.render(**context).encode("utf-8")


def normalize_brand_key(brand: str) -> str:
    return " ".join(brand.lower().replace("and", "&").split())


def fallback_logo_key(brand: str) -> str:
    key = []
    previous_dash = False
    for character in brand.lower():
        if character.isalnum():
            key.append(character)
            previous_dash = False
        elif not previous_dash:
            key.append("-")
            previous_dash = True
    return "".join(key).strip("-") or "brand"


def build_brand_logos(rows: list[dict]) -> list[dict]:
    logos = []
    for row in rows:
        brand = str(row.get("brand", "")).strip()
        logo_key, logo_lines = BRAND_WORDMARKS.get(
            normalize_brand_key(brand),
            (fallback_logo_key(brand), [brand.upper()]),
        )
        logos.append(
            {
                **row,
                "logo_key": logo_key,
                "logo_lines": logo_lines,
                "catalog_href": "/catalog?" + urlencode({"brand": brand}),
            }
        )
    return logos


def canonical_path(path: str) -> str:
    if path != "/" and path.endswith("/"):
        return path.rstrip("/")
    return path or "/"


def random_suffix() -> str:
    return str(int.from_bytes(os.urandom(3), "big")).zfill(6)


@dataclass
class Request:
    method: str
    path: str
    query: dict[str, list[str]]
    headers: dict[str, str]
    body: bytes = b""
    request_id: str = ""
    remote_ip: str = ""
    customer: dict | None = None

    @property
    def is_api(self) -> bool:
        return self.path.startswith("/api/")


@dataclass
class Response:
    status: HTTPStatus
    body: bytes
    content_type: str
    cache_control: str = "no-store"
    headers: list[tuple[str, str]] = field(default_factory=list)
    content_length: int | None = None


class PerfumeryApplication:
    def __init__(self) -> None:
        self._site_cache: dict | None = None
        self._site_cache_expires_at = 0.0
        self._site_cache_lock = threading.Lock()
        self._home_cache: dict | None = None
        self._home_cache_expires_at = 0.0
        self._home_cache_lock = threading.Lock()

    def __call__(self, environ, start_response):
        started_at = time.perf_counter()
        request_id = environ.get("HTTP_X_REQUEST_ID") or f"req_{int.from_bytes(os.urandom(6), 'big'):012x}"

        try:
            request = self._build_request(environ, request_id)
            request.customer = self.customer_from_request(request)
            response = self.dispatch(request)
        except ValidationError as exc:
            request = None
            response = self.json_response({"error": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
        except RequestTooLargeError as exc:
            request = None
            response = self.json_response({"error": str(exc)}, status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        except ValueError as exc:
            request = None
            response = self.json_response({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
        except Exception:  # pragma: no cover - defensive fallback
            logger.exception("Unhandled application error", extra={"request_id": request_id})
            response = self.json_response(
                {"error": "Internal server error."},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )

        headers = [
            ("Content-Type", response.content_type),
            ("Content-Length", str(response.content_length if response.content_length is not None else len(response.body))),
            ("Cache-Control", response.cache_control),
            ("X-Content-Type-Options", "nosniff"),
            ("Referrer-Policy", "strict-origin-when-cross-origin"),
            ("X-Frame-Options", "DENY"),
            ("Permissions-Policy", "camera=(), microphone=(), geolocation=()"),
            ("Cross-Origin-Resource-Policy", "same-origin"),
            ("Content-Security-Policy", self.content_security_policy()),
            ("X-Request-Id", request_id),
        ]
        if settings.enforce_hsts:
            headers.append(("Strict-Transport-Security", "max-age=63072000; includeSubDomains; preload"))
        headers.extend(response.headers)

        start_response(f"{response.status.value} {response.status.phrase}", headers)

        duration_ms = (time.perf_counter() - started_at) * 1000
        logger.info(
            "%s %s -> %s in %.2fms",
            getattr(request, "method", environ.get("REQUEST_METHOD", "GET")),
            getattr(request, "path", environ.get("PATH_INFO", "/")),
            response.status.value,
            duration_ms,
        )
        return [response.body]

    def dispatch(self, request: Request) -> Response:
        if request.method in {"GET", "HEAD"}:
            response = self.handle_get(request)
        elif request.method == "POST":
            response = self.handle_post(request)
        else:
            response = self.json_response(
                {"error": "Method not allowed."},
                status=HTTPStatus.METHOD_NOT_ALLOWED,
                headers=[("Allow", "GET, HEAD, POST")],
            )

        if request.method == "HEAD":
            return Response(
                status=response.status,
                body=b"",
                content_type=response.content_type,
                cache_control=response.cache_control,
                headers=response.headers,
                content_length=response.content_length if response.content_length is not None else len(response.body),
            )
        return response

    def handle_get(self, request: Request) -> Response:
        path = request.path
        query = request.query

        if path.startswith("/static/"):
            return self.serve_file(STATIC_DIR, path.removeprefix("/static/"), request)

        if path.startswith("/assets/"):
            return self.serve_file(ASSETS_DIR, path.removeprefix("/assets/"), request)

        if path.startswith("/artwork/") and path.endswith(".svg"):
            slug = path.removeprefix("/artwork/").removesuffix(".svg")
            fragrance = db.get_fragrance(slug)
            if fragrance is None:
                return self.render_404(path)
            return self.bytes_response(
                build_fragrance_artwork(fragrance),
                content_type="image/svg+xml; charset=utf-8",
                cache_control=f"public, max-age={settings.static_cache_max_age_seconds}",
            )

        if path == "/healthz":
            return self.json_response({"status": "ok", "environment": settings.app_env, "engine": settings.database_engine})

        if path == "/readyz":
            try:
                ready = db.ping()
            except Exception as exc:  # pragma: no cover - readiness failure path
                return self.json_response(
                    {"status": "degraded", "database": "unreachable", "error": str(exc)},
                    status=HTTPStatus.SERVICE_UNAVAILABLE,
                )
            redis_ready = rate_limiter.is_ready()
            ready = ready and redis_ready
            return self.json_response(
                {
                    "status": "ready" if ready else "degraded",
                    "database": settings.database_engine,
                    "rate_limit_store": "ready" if redis_ready else "unreachable",
                },
                status=HTTPStatus.OK if ready else HTTPStatus.SERVICE_UNAVAILABLE,
            )

        if path == "/admin/login":
            context = {
                **self.get_site_context(path, request),
                "page_title": "Admin login",
                "admin_enabled": bool(settings.admin_token),
                "error": "",
            }
            return self.html_response(render_template("admin_login.html", context))

        if path == "/account/sign-in":
            if request.customer:
                return self.redirect_response("/account")
            context = {
                **self.get_site_context(path, request),
                "page_title": "Sign in",
                "error": "",
                "next_path": self.safe_next_path(first_value(query, "next") or "/account"),
            }
            return self.html_response(render_template("account_sign_in.html", context))

        if path == "/account/sign-up":
            if request.customer:
                return self.redirect_response("/account")
            context = {
                **self.get_site_context(path, request),
                "page_title": "Create account",
                "error": "",
                "form": {},
            }
            return self.html_response(render_template("account_sign_up.html", context))

        if path.startswith("/account/verify/"):
            token = path.removeprefix("/account/verify/").split("/", 1)[0]
            customer = db.verify_customer_email(token)
            status = "verified" if customer else "invalid"
            return self.redirect_response(f"/account?verification={status}")

        if path == "/account":
            if not request.customer:
                return self.redirect_response("/account/sign-in?next=/account")
            is_verified = bool(request.customer.get("email_verified_at"))
            context = {
                **self.get_site_context(path, request),
                "page_title": "My account",
                "orders": db.list_customer_orders(request.customer["id"]) if is_verified else [],
                "email_verified": is_verified,
                "verification_status": first_value(query, "verification"),
                "local_verification_url": first_value(query, "verify") if not settings.is_production else "",
                "csrf_token": self.customer_csrf_token(request),
            }
            return self.html_response(render_template("account.html", context))

        if path.startswith("/account/orders/"):
            if not request.customer:
                return self.redirect_response(f"/account/sign-in?{urlencode({'next': path})}")
            if not request.customer.get("email_verified_at"):
                return self.redirect_response("/account?verification=required")
            order_number = path.removeprefix("/account/orders/").split("/", 1)[0]
            order = db.get_customer_order(request.customer["id"], request.customer["email"], order_number)
            if order is None:
                return self.render_404(path)
            context = {
                **self.get_site_context(path, request),
                "page_title": f"Order {order_number}",
                "order": order,
            }
            return self.html_response(render_template("order.html", context))

        if path == "/admin":
            if not self.is_admin_request(request):
                return self.redirect_response("/admin/login")
            context = {
                **self.get_site_context(path, request),
                "page_title": "Admin dashboard",
                "summary": db.get_admin_summary(),
                "recent_orders": db.list_orders(limit=8),
                "csrf_token": self.admin_csrf_token(request),
            }
            return self.html_response(render_template("admin_dashboard.html", context))

        if path == "/admin/orders":
            if not self.is_admin_request(request):
                return self.redirect_response("/admin/login")
            context = {
                **self.get_site_context(path, request),
                "page_title": "Admin orders",
                "orders": db.list_orders(),
                "csrf_token": self.admin_csrf_token(request),
            }
            return self.html_response(render_template("admin_orders.html", context))

        if path == "/admin/orders/export.csv":
            if not self.is_admin_request(request):
                return self.redirect_response("/admin/login")
            return self.admin_orders_export_response()

        if path == "/admin/readiness":
            if not self.is_admin_request(request):
                return self.redirect_response("/admin/login")
            context = {
                **self.get_site_context(path, request),
                "page_title": "Production readiness",
                "readiness": self.build_readiness_report(),
                "email_test": {
                    "status": first_value(query, "email_test"),
                    "message": first_value(query, "message"),
                },
                "csrf_token": self.admin_csrf_token(request),
            }
            return self.html_response(render_template("admin_readiness.html", context))

        if path == "/admin/fragrances":
            if not self.is_admin_request(request):
                return self.redirect_response("/admin/login")
            search = first_value(query, "q")
            status = first_value(query, "status") or "active"
            if status not in {"active", "archived", "all"}:
                status = "active"
            context = {
                **self.get_site_context(path, request),
                "page_title": "Admin inventory",
                "summary": db.get_admin_summary(),
                "items": db.list_admin_fragrances(search=search, status=status),
                "search": search,
                "status": status,
                "csrf_token": self.admin_csrf_token(request),
            }
            return self.html_response(render_template("admin_fragrances.html", context))

        if path == "/admin/fragrances/import":
            if not self.is_admin_request(request):
                return self.redirect_response("/admin/login")
            context = {
                **self.get_site_context(path, request),
                "page_title": "Import fragrances",
                "csrf_token": self.admin_csrf_token(request),
                "result": None,
                "error": "",
                "csv_columns": self.admin_import_csv_columns(),
            }
            return self.html_response(render_template("admin_fragrance_import.html", context))

        if path == "/admin/fragrances/import-template.csv":
            if not self.is_admin_request(request):
                return self.redirect_response("/admin/login")
            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=self.admin_import_csv_columns())
            writer.writeheader()
            writer.writerow(
                {
                    "slug": "creed-aventus-100ml",
                    "brand": "Creed",
                    "name": "Aventus",
                    "collection_type": "niche",
                    "gender": "him",
                    "family": "woody fruity",
                    "concentration": "Eau de Parfum",
                    "origin": "Imported",
                    "description": "A bright pineapple, birch, and musk signature.",
                    "signature": "A decisive modern classic.",
                    "top_notes": "Pineapple, Bergamot, Blackcurrant",
                    "heart_notes": "Birch, Jasmine, Patchouli",
                    "base_notes": "Musk, Oakmoss, Ambergris",
                    "image_url": "https://example.com/aventus.jpg",
                    "photo_icon_url": "",
                    "artwork_kind": "photo",
                    "bottle_size_ml": "100",
                    "featured": "false",
                    "rank": "999",
                    "is_active": "true",
                    "sku": "creed-aventus-retail-100",
                    "sale_type": "retail",
                    "size_label": "100 ML RETAIL",
                    "size_ml": "100",
                    "price_inr": "28900",
                    "compare_at_price_inr": "0",
                    "stock_units": "2",
                    "badge": "Last Units Available",
                    "statement": "Full presentation bottle.",
                }
            )
            return self.bytes_response(
                output.getvalue().encode("utf-8"),
                content_type="text/csv; charset=utf-8",
                headers=[("Content-Disposition", 'attachment; filename="the-scentist-import-template.csv"')],
            )

        if path == "/admin/fragrances/new":
            if not self.is_admin_request(request):
                return self.redirect_response("/admin/login")
            context = {
                **self.get_site_context(path, request),
                "page_title": "Add fragrance",
                "mode": "new",
                "form": self.admin_fragrance_form_data(),
                "fragrance": None,
                "error": "",
                "csrf_token": self.admin_csrf_token(request),
            }
            return self.html_response(render_template("admin_fragrance_form.html", context))

        if path.startswith("/admin/fragrances/") and path.endswith("/edit"):
            if not self.is_admin_request(request):
                return self.redirect_response("/admin/login")
            slug = path.removeprefix("/admin/fragrances/").removesuffix("/edit").strip("/")
            fragrance = db.get_admin_fragrance(slug)
            if fragrance is None:
                return self.render_404(path)
            context = {
                **self.get_site_context(path, request),
                "page_title": f"Edit {fragrance['name']}",
                "mode": "edit",
                "form": self.admin_fragrance_form_data(fragrance),
                "fragrance": fragrance,
                "variant_form": self.admin_variant_form_data(),
                "error": "",
                "variant_error": "",
                "csrf_token": self.admin_csrf_token(request),
            }
            return self.html_response(render_template("admin_fragrance_form.html", context))

        if path.startswith("/admin/orders/"):
            if not self.is_admin_request(request):
                return self.redirect_response("/admin/login")
            order_number = path.removeprefix("/admin/orders/").split("/", 1)[0]
            order = db.get_order(order_number)
            if order is None:
                return self.render_404(path)
            context = {
                **self.get_site_context(path, request),
                "page_title": f"Admin {order_number}",
                "order": order,
                "csrf_token": self.admin_csrf_token(request),
                "order_statuses": [
                    "Pending Payment",
                    "Confirmed",
                    "Packed",
                    "Shipped",
                    "Delivered",
                    "Cancelled",
                    "Review Required",
                ],
            }
            return self.html_response(render_template("admin_order.html", context))

        if path == "/api/fragrances":
            filters = self.extract_filters(query)
            try:
                page = max(1, int(first_value(query, "page") or "1"))
                per_page = min(100, max(1, int(first_value(query, "per_page") or "24")))
            except ValueError:
                raise ValidationError("Page and per_page must be integers.")
            total_items = db.count_fragrances(filters)
            total_pages = max(1, (total_items + per_page - 1) // per_page)
            page = min(page, total_pages)
            items = db.list_fragrances(filters, limit=per_page, offset=(page - 1) * per_page)
            return self.json_response(
                {
                    "filters": filters,
                    "items": items,
                    "pagination": {
                        "page": page,
                        "per_page": per_page,
                        "total_items": total_items,
                        "total_pages": total_pages,
                    },
                }
            )

        if path.startswith("/api/fragrances/"):
            slug = path.removeprefix("/api/fragrances/")
            fragrance = db.get_fragrance(slug)
            if fragrance is None:
                return self.json_response({"error": "Fragrance not found."}, status=HTTPStatus.NOT_FOUND)
            return self.json_response(fragrance)

        if path == "/api/cart-items":
            raw_ids = first_value(query, "variant_ids")
            variant_ids = list(
                dict.fromkeys(int(item) for item in raw_ids.split(",") if item.isdigit() and int(item) > 0)
            )[:100]
            return self.json_response({"items": db.get_cart_items(variant_ids)})

        if path.startswith("/api/orders/"):
            order_number = path.removeprefix("/api/orders/")
            public_token = first_value(query, "token")
            order = db.get_public_order(order_number, public_token) if public_token else None
            if order is None:
                return self.json_response({"error": "Order not found."}, status=HTTPStatus.NOT_FOUND)
            return self.json_response(order)

        if path.startswith("/api/"):
            return self.json_response({"error": "Unsupported route."}, status=HTTPStatus.NOT_FOUND)

        policy_pages = self.policy_pages()
        if path in policy_pages:
            page = policy_pages[path]
            context = {
                **self.get_site_context(path, request),
                "page_title": page["title"],
                "policy": page,
            }
            return self.html_response(render_template("policy.html", context))

        if path == "/":
            home_data = self.cached_home_data()
            context = {
                **self.get_site_context(path, request),
                "page_title": "Luxury niche and designer fragrances",
                "hero_image": "/assets/hero6.jpg",
                "hero_images": [
                    "/assets/hero6.jpg",
                    "/assets/louis-vuitton-pink-hero.webp",
                    "/assets/louis-vuitton-on-the-beach.jpg",
                ],
                "showcase_image": "/assets/louis-vuitton-on-the-beach.jpg",
                "motion_video": "/assets/louis-vuitton-mobile.mp4",
                "motion_poster": "/assets/louis-vuitton-on-the-beach.jpg",
                "motion_images": [
                    "/assets/hero6.jpg",
                    "/assets/louis-vuitton-pink-hero.webp",
                    "/assets/louis-vuitton-on-the-beach.jpg",
                ],
                "center_discovery": {
                    "kicker": "Louis Vuitton",
                    "title": "A sunlit escape in motion.",
                    "href": "/catalog?brand=Louis%20Vuitton",
                },
                "left_discovery": {
                    "image": "/assets/creed1.jpeg",
                    "kicker": "For Him",
                    "title": "Bold signatures with presence.",
                    "href": "/catalog?gender=him",
                },
                "right_discovery": {
                    "image": "/assets/her11.jpg",
                    "kicker": "For Her",
                    "title": "Soft trails, luminous evenings.",
                    "href": "/catalog?gender=her",
                },
                **home_data,
            }
            return self.html_response(render_template("home.html", context))

        if path == "/animation-lab":
            context = {
                **self.get_site_context(path, request),
                "page_title": "Animation Lab",
                "motion_video": "/assets/louis-vuitton-mobile.mp4",
                "motion_poster": "/assets/louis-vuitton-on-the-beach.jpg",
                "motion_images": [
                    "/assets/hero6.jpg",
                    "/assets/louis-vuitton-pink-hero.webp",
                    "/assets/louis-vuitton-on-the-beach.jpg",
                ],
                "center_discovery": {
                    "kicker": "Louis Vuitton",
                    "title": "A sunlit escape in motion.",
                    "href": "/catalog?brand=Louis%20Vuitton",
                },
                "left_discovery": {
                    "image": "/assets/creed1.jpeg",
                    "kicker": "For Him",
                    "title": "Bold signatures with presence.",
                    "href": "/catalog?gender=him",
                },
                "right_discovery": {
                    "image": "/assets/her11.jpg",
                    "kicker": "For Her",
                    "title": "Soft trails, luminous evenings.",
                    "href": "/catalog?gender=her",
                },
            }
            return self.html_response(render_template("animation_lab.html", context))

        if path == "/catalog":
            filters = self.extract_filters(query)
            per_page = 24
            try:
                page = max(1, int(first_value(query, "page") or "1"))
            except ValueError:
                page = 1
            total_items = db.count_fragrances(filters)
            total_pages = max(1, (total_items + per_page - 1) // per_page)
            page = min(page, total_pages)
            items = db.list_fragrances(filters, limit=per_page, offset=(page - 1) * per_page)
            search_suggestion = db.suggest_search_term(filters.get("search", ""))
            suggestion_href = ""
            if search_suggestion:
                suggestion_filters = {**filters, "search": search_suggestion}
                suggestion_query = urlencode({key: value for key, value in suggestion_filters.items() if value})
                suggestion_href = f"/catalog?{suggestion_query}" if suggestion_query else "/catalog"
            context = {
                **self.get_site_context(path, request),
                "page_title": "Perfume catalog",
                "catalog_items": items,
                "catalog_total": total_items,
                "pagination": self.catalog_pagination(filters, page, total_pages),
                "filters": db.list_filters(),
                "active_filters": filters,
                "active_filter_chips": self.catalog_filter_chips(filters),
                "search_suggestion": search_suggestion,
                "search_suggestion_href": suggestion_href,
            }
            return self.html_response(render_template("catalog.html", context))

        if path.startswith("/fragrances/"):
            slug = path.removeprefix("/fragrances/")
            fragrance = db.get_fragrance(slug)
            if fragrance is None:
                return self.render_404(path)
            context = {
                **self.get_site_context(path, request),
                "page_title": f"{fragrance['brand']} {fragrance['name']}",
                "fragrance": fragrance,
                "related_items": db.get_related_fragrances(fragrance),
            }
            return self.html_response(render_template("product.html", context))

        if path == "/cart":
            context = {
                **self.get_site_context(path, request),
                "page_title": "Your fragrance cart",
            }
            return self.html_response(render_template("cart.html", context))

        if path == "/checkout":
            context = {
                **self.get_site_context(path, request),
                "page_title": "Checkout",
            }
            return self.html_response(render_template("checkout.html", context))

        if path.startswith("/order/"):
            order_parts = path.removeprefix("/order/").split("/", 1)
            order_number = order_parts[0]
            public_token = order_parts[1] if len(order_parts) == 2 else ""
            order = db.get_public_order(order_number, public_token) if public_token else None
            if order is None:
                return self.render_404(path)
            context = {
                **self.get_site_context(path, request),
                "page_title": f"Order {order_number}",
                "order": order,
            }
            return self.html_response(render_template("order.html", context))

        return self.render_404(path)

    def handle_post(self, request: Request) -> Response:
        path = request.path

        if path == "/account/sign-up":
            limited = self.enforce_rate_limit(request, "account-sign-up", 5, 3600)
            if limited:
                return limited
            fields = self.read_form_body(request)
            try:
                password = fields.get("password", "")
                if password != fields.get("confirm_password", ""):
                    raise ValidationError("Passwords do not match.")
                customer = db.create_customer(
                    fields.get("full_name", ""),
                    fields.get("email", ""),
                    password,
                )
                verification_token = db.create_email_verification(customer["id"])
                session_token = db.create_customer_session(customer["id"])
                verification_url = self.absolute_url(request, f"/account/verify/{verification_token}")
                db.enqueue_email_verification(customer, verification_url)
                destination = "/account"
                if not settings.is_production:
                    destination += "?" + urlencode({"verify": f"/account/verify/{verification_token}"})
                return self.redirect_response(
                    destination,
                    headers=[("Set-Cookie", self.build_customer_cookie(session_token))],
                )
            except ValidationError as exc:
                context = {
                    **self.get_site_context(path, request),
                    "page_title": "Create account",
                    "error": str(exc),
                    "form": {
                        "full_name": fields.get("full_name", ""),
                        "email": fields.get("email", ""),
                    },
                }
                return self.html_response(render_template("account_sign_up.html", context), status=HTTPStatus.UNPROCESSABLE_ENTITY)

        if path == "/account/sign-in":
            limited = self.enforce_rate_limit(request, "account-sign-in", 10, 900)
            if limited:
                return limited
            fields = self.read_form_body(request)
            next_path = self.safe_next_path(fields.get("next", "") or "/account")
            customer = db.authenticate_customer(fields.get("email", ""), fields.get("password", ""))
            if customer is None:
                context = {
                    **self.get_site_context(path, request),
                    "page_title": "Sign in",
                    "error": "Invalid email or password.",
                    "next_path": next_path,
                }
                return self.html_response(render_template("account_sign_in.html", context), status=HTTPStatus.UNAUTHORIZED)
            session_token = db.create_customer_session(customer["id"])
            return self.redirect_response(
                next_path,
                headers=[("Set-Cookie", self.build_customer_cookie(session_token))],
            )

        if path == "/account/sign-out":
            fields = self.read_form_body(request)
            if not request.customer or not self.validate_customer_csrf(request, fields):
                return self.json_response({"error": "Invalid account form token."}, status=HTTPStatus.FORBIDDEN)
            db.delete_customer_session(self.customer_cookie_value(request))
            return self.redirect_response(
                "/",
                headers=[("Set-Cookie", self.clear_customer_cookie())],
            )

        if path == "/account/resend-verification":
            if not request.customer:
                return self.redirect_response("/account/sign-in")
            fields = self.read_form_body(request)
            if not self.validate_customer_csrf(request, fields):
                return self.json_response({"error": "Invalid account form token."}, status=HTTPStatus.FORBIDDEN)
            limited = self.enforce_rate_limit(request, "account-verification", 3, 3600)
            if limited:
                return limited
            token = db.create_email_verification(request.customer["id"])
            verification_url = self.absolute_url(request, f"/account/verify/{token}")
            db.enqueue_email_verification(request.customer, verification_url)
            destination = "/account?verification=sent"
            if not settings.is_production:
                destination += "&" + urlencode({"verify": f"/account/verify/{token}"})
            return self.redirect_response(destination)

        if path == "/admin/login":
            limited = self.enforce_rate_limit(request, "admin-login", 10, 900)
            if limited:
                return limited
            fields = self.read_form_body(request)
            submitted_token = fields.get("admin_token", "")
            if settings.admin_token and secrets.compare_digest(submitted_token, settings.admin_token):
                return self.redirect_response(
                    "/admin",
                    headers=[("Set-Cookie", self.build_admin_cookie())],
                )
            context = {
                **self.get_site_context(path, request),
                "page_title": "Admin login",
                "admin_enabled": bool(settings.admin_token),
                "error": "Invalid admin token." if settings.admin_token else "Admin token is not configured.",
            }
            return self.html_response(render_template("admin_login.html", context), status=HTTPStatus.UNAUTHORIZED)

        if path == "/admin/logout":
            return self.redirect_response(
                "/",
                headers=[("Set-Cookie", self.clear_admin_cookie())],
            )

        if path == "/admin/readiness/test-email":
            if not self.is_admin_request(request):
                return self.redirect_response("/admin/login")
            fields = self.read_form_body(request)
            csrf_error = self.validate_admin_csrf(request, fields)
            if csrf_error:
                return csrf_error
            recipient = fields.get("recipient") or settings.admin_email or settings.notification_from_email
            ok, message = notifier.send_test_email(recipient)
            return self.redirect_response(
                "/admin/readiness?"
                + urlencode(
                    {
                        "email_test": "sent" if ok else "failed",
                        "message": message[:220],
                    }
                )
            )

        if path == "/admin/fragrances":
            if not self.is_admin_request(request):
                return self.redirect_response("/admin/login")
            fields = self.read_form_body(request)
            csrf_error = self.validate_admin_csrf(request, fields)
            if csrf_error:
                return csrf_error
            try:
                fragrance = db.save_admin_fragrance(fields)
                return self.redirect_response(f"/admin/fragrances/{fragrance['slug']}/edit")
            except ValidationError as exc:
                context = {
                    **self.get_site_context(path, request),
                    "page_title": "Add fragrance",
                    "mode": "new",
                    "form": self.admin_fragrance_form_data(fields=fields),
                    "fragrance": None,
                    "error": str(exc),
                    "csrf_token": self.admin_csrf_token(request),
                }
                return self.html_response(
                    render_template("admin_fragrance_form.html", context),
                    status=HTTPStatus.UNPROCESSABLE_ENTITY,
                )

        if path == "/admin/fragrances/import":
            if not self.is_admin_request(request):
                return self.redirect_response("/admin/login")
            try:
                fields, files = self.read_multipart_form(request)
                csrf_error = self.validate_admin_csrf(request, fields)
                if csrf_error:
                    return csrf_error
                upload = files.get("catalog_csv")
                if not upload or not upload.get("content"):
                    raise ValidationError("Choose a CSV file to import.")
                rows = self.parse_admin_import_csv(upload["content"])
                result = self.import_admin_catalog_rows(rows)
                context = {
                    **self.get_site_context(path, request),
                    "page_title": "Import fragrances",
                    "csrf_token": self.admin_csrf_token(request),
                    "result": result,
                    "error": "",
                    "csv_columns": self.admin_import_csv_columns(),
                }
                return self.html_response(render_template("admin_fragrance_import.html", context))
            except ValidationError as exc:
                context = {
                    **self.get_site_context(path, request),
                    "page_title": "Import fragrances",
                    "csrf_token": self.admin_csrf_token(request),
                    "result": None,
                    "error": str(exc),
                    "csv_columns": self.admin_import_csv_columns(),
                }
                return self.html_response(
                    render_template("admin_fragrance_import.html", context),
                    status=HTTPStatus.UNPROCESSABLE_ENTITY,
                )

        if path.startswith("/admin/fragrances/"):
            if not self.is_admin_request(request):
                return self.redirect_response("/admin/login")
            fields = self.read_form_body(request)
            csrf_error = self.validate_admin_csrf(request, fields)
            if csrf_error:
                return csrf_error

            parts = path.removeprefix("/admin/fragrances/").split("/")
            slug = parts[0] if parts else ""
            action = parts[1] if len(parts) > 1 else ""
            fragrance = db.get_admin_fragrance(slug)
            if fragrance is None:
                return self.render_404(path)

            try:
                if action == "save":
                    saved = db.save_admin_fragrance(fields, current_slug=slug)
                    return self.redirect_response(f"/admin/fragrances/{saved['slug']}/edit")
                if action == "archive":
                    db.set_admin_fragrance_active(slug, False)
                    return self.redirect_response("/admin/fragrances?status=archived")
                if action == "restore":
                    db.set_admin_fragrance_active(slug, True)
                    return self.redirect_response(f"/admin/fragrances/{slug}/edit")
                if action == "delete":
                    result = db.delete_admin_fragrance(slug)
                    status_query = "archived" if result == "archived" else "all"
                    return self.redirect_response(f"/admin/fragrances?status={status_query}")
                if action == "variants":
                    if len(parts) >= 4 and parts[2].isdigit() and parts[3] == "delete":
                        db.delete_admin_variant(slug, int(parts[2]))
                    else:
                        db.save_admin_variant(slug, fields)
                    return self.redirect_response(f"/admin/fragrances/{slug}/edit#variants")
            except ValidationError as exc:
                refreshed = db.get_admin_fragrance(slug) or fragrance
                context = {
                    **self.get_site_context(path, request),
                    "page_title": f"Edit {refreshed['name']}",
                    "mode": "edit",
                    "form": self.admin_fragrance_form_data(fields=fields if action == "save" else None, fragrance=refreshed),
                    "fragrance": refreshed,
                    "variant_form": self.admin_variant_form_data(fields if action == "variants" else None),
                    "error": str(exc) if action == "save" else "",
                    "variant_error": str(exc) if action == "variants" else "",
                    "csrf_token": self.admin_csrf_token(request),
                }
                return self.html_response(
                    render_template("admin_fragrance_form.html", context),
                    status=HTTPStatus.UNPROCESSABLE_ENTITY,
                )

            return self.json_response({"error": "Unsupported admin fragrance action."}, status=HTTPStatus.NOT_FOUND)

        if path.startswith("/admin/orders/") and path.endswith("/status"):
            if not self.is_admin_request(request):
                return self.redirect_response("/admin/login")
            fields = self.read_form_body(request)
            csrf_error = self.validate_admin_csrf(request, fields)
            if csrf_error:
                return csrf_error
            order_number = path.removeprefix("/admin/orders/").removesuffix("/status").strip("/")
            try:
                order = db.update_order_status(order_number, fields)
                if fields.get("notify_customer") == "on":
                    notify_order_status_once(order)
            except ValidationError as exc:
                return self.json_response({"error": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
            return self.redirect_response(f"/admin/orders/{order_number}")

        if path == "/api/concierge":
            limited = self.enforce_rate_limit(request, "concierge", 20, 3600)
            if limited:
                return limited
            if not settings.ai_concierge_enabled:
                return self.json_response(
                    {"error": "The scent concierge is currently unavailable."},
                    status=HTTPStatus.SERVICE_UNAVAILABLE,
                )
            try:
                payload = self.read_json_body(request)
                message = str(payload.get("message", "")).strip()
                if len(message) < 3:
                    raise ValidationError("Tell the concierge a little more about the scent you want.")
                return self.json_response(concierge.recommend(message))
            except ValidationError as exc:
                return self.json_response({"error": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
            except json.JSONDecodeError:
                return self.json_response({"error": "Invalid JSON body."}, status=HTTPStatus.BAD_REQUEST)
            except Exception as exc:  # pragma: no cover
                logger.exception("Concierge request failed", extra={"request_id": request.request_id})
                return self.json_response(
                    {"error": f"Unable to prepare concierge recommendations: {exc}"},
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )

        if path == "/api/orders":
            limited = self.enforce_rate_limit(request, "orders", 10, 600)
            if limited:
                return limited
            if not settings.enable_manual_checkout:
                return self.json_response(
                    {"error": "Manual checkout is disabled on this deployment."},
                    status=HTTPStatus.FORBIDDEN,
                )
            try:
                payload = self.read_json_body(request)
                customer_id = request.customer["id"] if request.customer else None
                if request.customer:
                    payload = self.attach_account_to_payload(payload, request.customer)
                customer = payload.get("customer") or {}
                if customer.get("payment_method") in ONLINE_PAYMENT_METHODS:
                    raise ValidationError("Use the Razorpay checkout flow for online payments.")
                idempotency_key = self.checkout_idempotency_key(request)
                order = db.create_order(
                    payload,
                    customer_id=customer_id,
                    idempotency_key=idempotency_key,
                )
                notify_order_once(order)
                return self.json_response({"order": order}, status=HTTPStatus.CREATED)
            except ValidationError as exc:
                return self.json_response({"error": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
            except json.JSONDecodeError:
                return self.json_response({"error": "Invalid JSON body."}, status=HTTPStatus.BAD_REQUEST)
            except Exception as exc:  # pragma: no cover
                return self.json_response({"error": f"Unable to place order: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

        if path == "/api/checkout/razorpay-order":
            limited = self.enforce_rate_limit(request, "razorpay-order", 10, 600)
            if limited:
                return limited
            if not razorpay.enabled:
                return self.json_response(
                    {"error": "Razorpay is not configured on this deployment."},
                    status=HTTPStatus.SERVICE_UNAVAILABLE,
                )

            try:
                payload = self.read_json_body(request)
                customer_id = request.customer["id"] if request.customer else None
                if request.customer:
                    payload = self.attach_account_to_payload(payload, request.customer)
                customer = payload.get("customer") or {}
                items = payload.get("items") or []
                idempotency_key = self.checkout_idempotency_key(request)
                if customer.get("payment_method") not in ONLINE_PAYMENT_METHODS:
                    raise ValidationError("Please choose UPI, netbanking, or card for online payment.")
                existing = db.get_order_by_idempotency_key(idempotency_key)
                if existing is not None:
                    return self.razorpay_checkout_response(existing)
                lock_token = rate_limiter.acquire_lock(f"razorpay:{idempotency_key}", ttl_seconds=45)
                if lock_token is None:
                    return self.json_response(
                        {"error": "This checkout is already being prepared. Please retry in a moment."},
                        status=HTTPStatus.CONFLICT,
                        headers=[("Retry-After", "2")],
                    )
                try:
                    existing = db.get_order_by_idempotency_key(idempotency_key)
                    if existing is not None:
                        return self.razorpay_checkout_response(existing)
                    snapshot = db.preview_order(items)
                    gateway_order = razorpay.create_order(
                        amount_subunits=snapshot["total_inr"] * 100,
                        currency=settings.currency,
                        receipt=f"{settings.order_prefix}-{random_suffix()}",
                        notes={
                            "customer_name": customer.get("customer_name", "")[:40],
                            "email": customer.get("email", "")[:40],
                        },
                    )
                    local_order = db.create_pending_razorpay_order(
                        customer=customer,
                        items=items,
                        gateway_order_id=gateway_order["id"],
                        customer_id=customer_id,
                        idempotency_key=idempotency_key,
                    )
                    return self.razorpay_checkout_response(local_order)
                finally:
                    rate_limiter.release_lock(f"razorpay:{idempotency_key}", lock_token)
            except ValidationError as exc:
                return self.json_response({"error": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
            except PaymentGatewayError as exc:
                return self.json_response({"error": str(exc)}, status=HTTPStatus.BAD_GATEWAY)
            except json.JSONDecodeError:
                return self.json_response({"error": "Invalid JSON body."}, status=HTTPStatus.BAD_REQUEST)
            except Exception as exc:  # pragma: no cover
                return self.json_response(
                    {"error": f"Unable to start Razorpay checkout: {exc}"},
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )

        if path == "/api/payments/razorpay/verify":
            limited = self.enforce_rate_limit(request, "razorpay-verify", 20, 600)
            if limited:
                return limited
            try:
                payload = self.read_json_body(request)
                local_order_number = str(payload.get("local_order_number", "")).strip()
                payment_id = str(payload.get("razorpay_payment_id", "")).strip()
                received_order_id = str(payload.get("razorpay_order_id", "")).strip()
                signature = str(payload.get("razorpay_signature", "")).strip()

                order = db.get_order(local_order_number)
                if order is None:
                    raise ValidationError("Pending order not found.")

                expected_order_id = order["gateway_order_id"]
                if received_order_id != expected_order_id:
                    raise ValidationError("Payment order mismatch detected.")

                is_valid = razorpay.verify_payment_signature(
                    order_id=expected_order_id,
                    payment_id=payment_id,
                    signature=signature,
                )
                if not is_valid:
                    db.mark_payment_failure(local_order_number, "Signature verification failed.")
                    raise ValidationError("Payment verification failed.")

                final_order = db.finalize_razorpay_order(
                    order_number=local_order_number,
                    gateway_order_id=expected_order_id,
                    gateway_payment_id=payment_id,
                    gateway_signature=signature,
                )
                notify_order_once(final_order)
                return self.json_response({"order": final_order}, status=HTTPStatus.OK)
            except ValidationError as exc:
                return self.json_response({"error": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
            except json.JSONDecodeError:
                return self.json_response({"error": "Invalid JSON body."}, status=HTTPStatus.BAD_REQUEST)
            except Exception as exc:  # pragma: no cover
                return self.json_response(
                    {"error": f"Unable to verify Razorpay payment: {exc}"},
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )

        if path == "/api/payments/razorpay/failure":
            limited = self.enforce_rate_limit(request, "razorpay-failure", 20, 600)
            if limited:
                return limited
            try:
                payload = self.read_json_body(request)
                local_order_number = str(payload.get("local_order_number", "")).strip()
                received_order_id = str(payload.get("razorpay_order_id", "")).strip()
                reason = str(payload.get("reason", "Payment failed before capture.")).strip()

                order = db.get_order(local_order_number)
                if order is None or order["gateway_order_id"] != received_order_id:
                    raise ValidationError("Pending order not found.")
                db.mark_payment_failure(local_order_number, reason)
                return self.json_response({"status": "recorded"}, status=HTTPStatus.OK)
            except ValidationError as exc:
                return self.json_response({"error": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
            except json.JSONDecodeError:
                return self.json_response({"error": "Invalid JSON body."}, status=HTTPStatus.BAD_REQUEST)

        if path == "/api/webhooks/razorpay":
            signature = request.headers.get("x-razorpay-signature", "")
            event_type = ""

            if not settings.razorpay_webhook_secret:
                return self.json_response(
                    {"error": "Razorpay webhook secret is not configured."},
                    status=HTTPStatus.SERVICE_UNAVAILABLE,
                )
            if not razorpay.verify_webhook_signature(
                body=request.body,
                signature=signature,
                webhook_secret=settings.razorpay_webhook_secret,
            ):
                return self.json_response({"error": "Invalid webhook signature."}, status=HTTPStatus.UNAUTHORIZED)

            try:
                payload = json.loads(request.body.decode("utf-8") or "{}")
                event_type = payload.get("event", "")
                entity = ((payload.get("payload") or {}).get("payment") or {}).get("entity") or {}
                if event_type in {"payment.captured", "order.paid"} and entity.get("order_id"):
                    final_order = db.finalize_razorpay_order_from_webhook(
                        gateway_order_id=entity["order_id"],
                        gateway_payment_id=entity.get("id", ""),
                        paid_amount_subunits=int(entity.get("amount", 0) or 0),
                    )
                    if final_order:
                        notify_order_once(final_order)
                return self.json_response({"status": "accepted", "event": event_type}, status=HTTPStatus.OK)
            except ValidationError as exc:
                return self.json_response({"error": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
            except json.JSONDecodeError:
                return self.json_response({"error": "Invalid webhook payload."}, status=HTTPStatus.BAD_REQUEST)
            except Exception as exc:  # pragma: no cover
                return self.json_response({"error": f"Webhook processing failed: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

        return self.json_response({"error": "Unsupported route."}, status=HTTPStatus.NOT_FOUND)

    def extract_filters(self, query: dict[str, list[str]]) -> dict[str, str]:
        return {
            "search": first_value(query, "search"),
            "gender": first_value(query, "gender"),
            "brand": first_value(query, "brand"),
            "collection_type": first_value(query, "collection_type"),
            "family": first_value(query, "family"),
            "sale_type": first_value(query, "sale_type"),
            "sort": first_value(query, "sort"),
        }

    def catalog_pagination(self, filters: dict[str, str], page: int, total_pages: int) -> dict[str, Any]:
        def href(target_page: int) -> str:
            params = {key: value for key, value in filters.items() if value}
            if target_page > 1:
                params["page"] = str(target_page)
            query = urlencode(params)
            return f"/catalog?{query}" if query else "/catalog"

        candidates = {1, total_pages, page - 2, page - 1, page, page + 1, page + 2}
        page_numbers = [number for number in sorted(candidates) if 1 <= number <= total_pages]
        pages: list[dict[str, Any]] = []
        previous_number = 0
        for number in page_numbers:
            if previous_number and number - previous_number > 1:
                pages.append({"ellipsis": True})
            pages.append({"number": number, "href": href(number), "current": number == page})
            previous_number = number

        return {
            "page": page,
            "total_pages": total_pages,
            "has_previous": page > 1,
            "has_next": page < total_pages,
            "previous_href": href(page - 1) if page > 1 else "",
            "next_href": href(page + 1) if page < total_pages else "",
            "pages": pages,
        }

    def catalog_filter_chips(self, filters: dict[str, str]) -> list[dict[str, str]]:
        labels = {
            "search": "Search",
            "gender": "For",
            "brand": "House",
            "collection_type": "Collection",
            "family": "Family",
            "sale_type": "Type",
        }
        chips = []
        for key, label in labels.items():
            value = filters.get(key, "")
            if not value:
                continue
            remaining = {name: item for name, item in filters.items() if item and name != key}
            query = urlencode(remaining)
            chips.append(
                {
                    "label": f"{label}: {titleize_enum(value)}",
                    "href": f"/catalog?{query}" if query else "/catalog",
                }
            )
        return chips

    def get_site_context(self, path: str, request: Request | None = None) -> dict:
        cached_site_data = self.cached_site_data()
        metrics = cached_site_data["metrics"]
        preview_label = os.getenv("PREVIEW_LABEL", "").strip()
        if not preview_label and not settings.is_production:
            preview_label = settings.app_env.upper()
        current_customer = request.customer if request else None
        return {
            "site_name": settings.site_name,
            "site_logo": "/assets/perfume-logo.png",
            "current_path": path,
            "current_customer": current_customer,
            "asset_version": ASSET_VERSION,
            "preview_label": preview_label,
            "site_metrics": metrics,
            "support": {
                "email": settings.support_email or settings.admin_email,
                "phone": settings.support_phone,
                "business_address": settings.business_address,
            },
            "payment": {
                "razorpay_enabled": settings.razorpay_enabled,
                "razorpay_key_id": settings.razorpay_key_id,
                "manual_checkout_enabled": settings.enable_manual_checkout,
            },
            "concierge": {
                "enabled": settings.ai_concierge_enabled,
                "ai_enabled": bool(settings.openai_api_key),
            },
            "nav_links": [
                {"href": "/", "label": "Home"},
                {
                    "href": "/catalog",
                    "label": "Fragrances",
                    "children": [
                        {"href": "/catalog?sale_type=retail", "label": "Retail"},
                        {"href": "/catalog?sale_type=decant", "label": "Decants"},
                        {"href": "/catalog?sale_type=partial", "label": "Partials"},
                        {"href": "/catalog?sale_type=tester", "label": "Testers"},
                    ],
                },
                {"href": "/#brands", "label": "Brands", "mega": "brands"},
                {"href": "/#scent-concierge", "label": "Concierge"},
            ],
            "nav_brand_groups": cached_site_data["brand_groups"],
            "nav_brand_totals": cached_site_data["brand_totals"],
            "support_brands": [
                "Creed",
                "Amouage",
                "Kilian",
                "Clive Christian",
                "Roja",
                "Xerjoff",
                "MFK",
                "Chanel",
                "Gucci",
                "Dior",
                "YSL",
            ],
        }

    def cached_site_data(self) -> dict:
        now = time.monotonic()
        if self._site_cache is not None and now < self._site_cache_expires_at:
            return self._site_cache
        with self._site_cache_lock:
            now = time.monotonic()
            if self._site_cache is None or now >= self._site_cache_expires_at:
                brand_groups = db.list_brand_groups()
                self._site_cache = {
                    "metrics": db.get_metrics(),
                    "brand_groups": {
                        collection: sorted(items, key=lambda item: (-int(item["count"]), item["name"]))[:18]
                        for collection, items in brand_groups.items()
                    },
                    "brand_totals": {collection: len(items) for collection, items in brand_groups.items()},
                }
                self._site_cache_expires_at = now + 60
        return self._site_cache

    def cached_home_data(self) -> dict:
        now = time.monotonic()
        if self._home_cache is not None and now < self._home_cache_expires_at:
            return self._home_cache
        with self._home_cache_lock:
            now = time.monotonic()
            if self._home_cache is None or now >= self._home_cache_expires_at:
                featured = []
                seen_slugs = set()
                for group in (
                    db.list_fragrances({"brand": "Creed"}, limit=3),
                    db.get_featured(9),
                    db.list_fragrances(limit=9),
                ):
                    for item in group:
                        if item["slug"] in seen_slugs:
                            continue
                        featured.append(item)
                        seen_slugs.add(item["slug"])
                        if len(featured) >= 9:
                            break
                    if len(featured) >= 9:
                        break
                self._home_cache = {
                    "featured": featured,
                    "brands": build_brand_logos(db.get_brand_showcase(16)),
                    "filters": db.list_filters(),
                }
                self._home_cache_expires_at = now + 60
        return self._home_cache

    def attach_account_to_payload(self, payload: dict, customer: dict) -> dict:
        updated = dict(payload)
        customer_payload = dict(updated.get("customer") or {})
        customer_payload["email"] = customer["email"]
        if not customer_payload.get("customer_name"):
            customer_payload["customer_name"] = customer["full_name"]
        updated["customer"] = customer_payload
        return updated

    def read_json_body(self, request: Request) -> dict:
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise ValidationError("Content-Type must be application/json.")
        body = request.body or b"{}"
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValidationError("JSON body must be an object.")
        return payload

    def read_form_body(self, request: Request) -> dict[str, str]:
        fields = parse_qs(request.body.decode("utf-8"), keep_blank_values=True)
        return {key: values[0] if values else "" for key, values in fields.items()}

    def read_multipart_form(self, request: Request) -> tuple[dict[str, str], dict[str, dict[str, object]]]:
        content_type = request.headers.get("content-type", "")
        if "multipart/form-data" not in content_type:
            raise ValidationError("Expected a multipart form upload.")

        message = BytesParser(policy=email_policy).parsebytes(
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8")
            + request.body
        )
        if not message.is_multipart():
            raise ValidationError("Invalid upload payload.")

        fields: dict[str, str] = {}
        files: dict[str, dict[str, object]] = {}
        for part in message.iter_parts():
            if part.get_content_disposition() != "form-data":
                continue
            name = part.get_param("name", header="content-disposition")
            if not name:
                continue
            payload = part.get_payload(decode=True) or b""
            filename = part.get_filename()
            if filename:
                files[str(name)] = {
                    "filename": filename,
                    "content_type": part.get_content_type(),
                    "content": payload,
                }
            else:
                charset = part.get_content_charset() or "utf-8"
                fields[str(name)] = payload.decode(charset, errors="replace")
        return fields, files

    def admin_import_csv_columns(self) -> list[str]:
        return [
            "slug",
            "brand",
            "name",
            "collection_type",
            "gender",
            "family",
            "concentration",
            "origin",
            "description",
            "signature",
            "top_notes",
            "heart_notes",
            "base_notes",
            "image_url",
            "photo_icon_url",
            "artwork_kind",
            "bottle_size_ml",
            "featured",
            "rank",
            "is_active",
            "sku",
            "sale_type",
            "size_label",
            "size_ml",
            "price_inr",
            "compare_at_price_inr",
            "stock_units",
            "badge",
            "statement",
        ]

    def parse_admin_import_csv(self, content: bytes) -> list[dict[str, str]]:
        if len(content) > settings.max_request_body_bytes:
            raise ValidationError("CSV file is too large for this deployment.")
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValidationError("CSV must be UTF-8 encoded.") from exc

        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            raise ValidationError("CSV is missing a header row.")

        rows: list[dict[str, str]] = []
        for row_number, row in enumerate(reader, start=2):
            normalized = {
                self.admin_import_column_name(key): str(value or "").strip()
                for key, value in row.items()
                if key is not None
            }
            if not any(normalized.values()):
                continue
            normalized["_row_number"] = str(row_number)
            rows.append(normalized)

        if not rows:
            raise ValidationError("CSV did not contain any product rows.")
        if len(rows) > 5000:
            raise ValidationError("CSV import is limited to 5,000 rows at a time.")
        return rows

    def admin_import_column_name(self, value: str) -> str:
        key = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
        aliases = {
            "house": "brand",
            "brand_name": "brand",
            "fragrance": "name",
            "fragrance_name": "name",
            "product_name": "name",
            "product_slug": "slug",
            "collection": "collection_type",
            "category": "collection_type",
            "wearer": "gender",
            "sex": "gender",
            "scent_family": "family",
            "olfactive_family": "family",
            "middle_notes": "heart_notes",
            "mid_notes": "heart_notes",
            "main_image": "image_url",
            "image": "image_url",
            "photo": "image_url",
            "icon": "photo_icon_url",
            "active": "is_active",
            "visible": "is_active",
            "variant_sku": "sku",
            "format": "sale_type",
            "type": "sale_type",
            "price": "price_inr",
            "mrp": "compare_at_price_inr",
            "compare_at": "compare_at_price_inr",
            "stock": "stock_units",
            "quantity": "stock_units",
        }
        return aliases.get(key, key)

    def import_admin_catalog_rows(self, rows: list[dict[str, str]]) -> dict[str, object]:
        result: dict[str, object] = {
            "rows": len(rows),
            "created": 0,
            "updated": 0,
            "variants_created": 0,
            "variants_updated": 0,
            "errors": [],
        }
        errors: list[dict[str, str]] = []
        counted_product_slugs: set[str] = set()

        for row in rows:
            row_number = row.get("_row_number", "?")
            try:
                product_fields = self.admin_import_product_fields(row)
                import_slug = slugify(product_fields.get("slug") or f"{product_fields.get('brand')} {product_fields.get('name')}")
                existing = db.get_admin_fragrance(import_slug) if import_slug else None
                saved = db.save_admin_fragrance(
                    product_fields,
                    current_slug=existing["slug"] if existing else None,
                )
                if saved["slug"] not in counted_product_slugs:
                    result["updated" if existing else "created"] = int(result["updated" if existing else "created"]) + 1
                    counted_product_slugs.add(saved["slug"])

                if self.admin_import_has_variant(row):
                    variant_fields = self.admin_import_variant_fields(row, saved["slug"])
                    variant_sku = slugify(
                        variant_fields.get("sku")
                        or f"{saved['slug']}-{variant_fields.get('sale_type')}-{variant_fields.get('size_ml')}"
                    )
                    refreshed = db.get_admin_fragrance(saved["slug"]) or saved
                    existing_variant = next(
                        (variant for variant in refreshed.get("variants", []) if variant.get("sku") == variant_sku),
                        None,
                    )
                    if existing_variant:
                        variant_fields["variant_id"] = str(existing_variant["id"])
                    db.save_admin_variant(saved["slug"], variant_fields)
                    key = "variants_updated" if existing_variant else "variants_created"
                    result[key] = int(result[key]) + 1
            except ValidationError as exc:
                errors.append({"row": row_number, "message": str(exc)})

        result["errors"] = errors
        return result

    def admin_import_product_fields(self, row: dict[str, str]) -> dict[str, str]:
        brand = row.get("brand", "")
        name = row.get("name", "")
        image_url = row.get("image_url", "")
        is_active = self.admin_import_bool(row.get("is_active"), default=True)
        if is_active and not image_url:
            raise ValidationError("Image URL is required for active CSV products.")

        collection_type = self.admin_import_choice(
            row.get("collection_type"),
            {"niche", "designer"},
            default="niche",
        )
        gender = self.admin_import_gender(row.get("gender"))

        return {
            "slug": row.get("slug", ""),
            "brand": brand,
            "name": name,
            "collection_type": collection_type,
            "gender": gender,
            "family": row.get("family", ""),
            "concentration": row.get("concentration", "Eau de Parfum"),
            "origin": row.get("origin", "Imported"),
            "description": row.get("description", ""),
            "signature": row.get("signature", ""),
            "top_notes": row.get("top_notes", ""),
            "heart_notes": row.get("heart_notes", ""),
            "base_notes": row.get("base_notes", ""),
            "accent_from": row.get("accent_from", "#c2b4a3"),
            "accent_to": row.get("accent_to", "#17120f"),
            "image_url": image_url,
            "photo_icon_url": row.get("photo_icon_url", ""),
            "artwork_kind": row.get("artwork_kind", "photo") or "photo",
            "bottle_size_ml": row.get("bottle_size_ml", "100"),
            "featured": "on" if self.admin_import_bool(row.get("featured"), default=False) else "",
            "rank": row.get("rank", "999"),
            "is_active": "on" if is_active else "",
        }

    def admin_import_has_variant(self, row: dict[str, str]) -> bool:
        return any(
            row.get(key, "")
            for key in ("sku", "sale_type", "size_label", "size_ml", "price_inr", "stock_units")
        )

    def admin_import_variant_fields(self, row: dict[str, str], fragrance_slug: str) -> dict[str, str]:
        sale_type = self.admin_import_sale_type(row.get("sale_type"))
        size_ml = row.get("size_ml", "100")
        sku = row.get("sku") or f"{fragrance_slug}-{sale_type}-{size_ml}"
        return {
            "sku": sku,
            "sale_type": sale_type,
            "size_label": row.get("size_label") or f"{size_ml} ML",
            "size_ml": size_ml,
            "price_inr": row.get("price_inr", ""),
            "compare_at_price_inr": row.get("compare_at_price_inr", "0"),
            "stock_units": row.get("stock_units", "0"),
            "badge": row.get("badge", ""),
            "statement": row.get("statement", ""),
        }

    def admin_import_bool(self, value: str, *, default: bool) -> bool:
        clean = str(value or "").strip().lower()
        if not clean:
            return default
        if clean in {"1", "true", "yes", "y", "on", "active", "visible", "live"}:
            return True
        if clean in {"0", "false", "no", "n", "off", "archived", "hidden", "inactive"}:
            return False
        return default

    def admin_import_choice(self, value: str, choices: set[str], *, default: str) -> str:
        clean = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
        return clean if clean in choices else default

    def admin_import_gender(self, value: str) -> str:
        clean = str(value or "").strip().lower()
        if clean in {"men", "male", "man", "for_him", "for him"}:
            return "him"
        if clean in {"women", "female", "woman", "for_her", "for her"}:
            return "her"
        if clean in {"him", "her", "unisex"}:
            return clean
        return "unisex"

    def admin_import_sale_type(self, value: str) -> str:
        clean = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
        if clean in {"retail_bottle", "bottle", "full_bottle", "retail"}:
            return "retail"
        if clean in {"decants", "decant"}:
            return "decant"
        if clean in {"partials", "partial_bottle", "partial"}:
            return "partial"
        if clean in {"testers", "tester"}:
            return "tester"
        return "retail"

    def admin_orders_export_response(self) -> Response:
        orders = [db.get_order(order["order_number"]) for order in db.list_orders(limit=10000)]
        output = io.StringIO()
        columns = [
            "order_number",
            "created_at",
            "status",
            "payment_method",
            "payment_status",
            "customer_name",
            "email",
            "phone",
            "subtotal_inr",
            "shipping_inr",
            "total_inr",
            "item_count",
            "items",
            "shipping_address",
            "courier_name",
            "tracking_number",
            "tracking_url",
            "admin_notes",
            "last_error",
        ]
        writer = csv.DictWriter(output, fieldnames=columns)
        writer.writeheader()
        for order in (item for item in orders if item):
            items = "; ".join(
                f"{line['brand']} {line['fragrance_name']} {line['size_label']} x {line['quantity']}"
                for line in order["items"]
            )
            address = ", ".join(
                part
                for part in [
                    order.get("shipping_line1", ""),
                    order.get("shipping_line2", ""),
                    order.get("city", ""),
                    order.get("state", ""),
                    order.get("postal_code", ""),
                    order.get("country", ""),
                ]
                if part
            )
            writer.writerow(
                {
                    "order_number": order["order_number"],
                    "created_at": order["created_at"],
                    "status": order["status"],
                    "payment_method": order["payment_method"],
                    "payment_status": order["payment_status"],
                    "customer_name": order["customer_name"],
                    "email": order["email"],
                    "phone": order["phone"],
                    "subtotal_inr": order["subtotal_inr"],
                    "shipping_inr": order["shipping_inr"],
                    "total_inr": order["total_inr"],
                    "item_count": order["item_count"],
                    "items": items,
                    "shipping_address": address,
                    "courier_name": order.get("courier_name", ""),
                    "tracking_number": order.get("tracking_number", ""),
                    "tracking_url": order.get("tracking_url", ""),
                    "admin_notes": order.get("admin_notes", ""),
                    "last_error": order.get("last_error", ""),
                }
            )

        return self.bytes_response(
            output.getvalue().encode("utf-8"),
            content_type="text/csv; charset=utf-8",
            headers=[("Content-Disposition", 'attachment; filename="the-scentist-orders.csv"')],
        )

    def build_readiness_report(self) -> dict[str, object]:
        metrics = db.get_readiness_metrics()
        checks = [
            self.readiness_check(
                "PostgreSQL database",
                settings.database_engine == "postgres",
                "Production should use Neon/PostgreSQL, not local SQLite.",
            ),
            self.readiness_check(
                "Admin token",
                bool(settings.admin_token),
                "Set ADMIN_TOKEN so only you can access operations.",
            ),
            self.readiness_check(
                "Razorpay keys",
                settings.razorpay_enabled,
                "Set production RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET before online payments.",
            ),
            self.readiness_check(
                "Razorpay webhook secret",
                bool(settings.razorpay_webhook_secret),
                "Set RAZORPAY_WEBHOOK_SECRET and configure the webhook URL in Razorpay.",
            ),
            self.readiness_check(
                "Email notifications",
                notifier.enabled,
                "Set SMTP_HOST, NOTIFICATION_FROM_EMAIL, and ADMIN_EMAIL for customer/admin notifications.",
            ),
            self.readiness_check(
                "Support contact",
                bool(settings.support_email or settings.admin_email),
                "Set SUPPORT_EMAIL or ADMIN_EMAIL so policy pages have a real support destination.",
            ),
            self.readiness_check(
                "Active catalog",
                metrics["active_fragrances"] > 0,
                "Import active fragrances before launch.",
            ),
            self.readiness_check(
                "Real catalog images",
                metrics["placeholder_images"] == 0 and metrics["missing_images"] == 0,
                "No active product should use placeholder or missing images.",
            ),
            self.readiness_check(
                "Buying options",
                metrics["fragrances_without_variants"] == 0 and metrics["active_variants"] > 0,
                "Every active fragrance needs at least one variant.",
            ),
            self.readiness_check(
                "Order attention queue",
                metrics["orders_requiring_attention"] == 0,
                "Resolve failed, pending, or review-required orders before opening sales.",
            ),
        ]
        return {
            "checks": checks,
            "ready": all(check["ok"] for check in checks),
            "metrics": metrics,
            "webhook_url": f"{settings.base_url or 'https://your-domain.example'}/api/webhooks/razorpay",
            "smtp": {
                "configured": notifier.enabled,
                "host": settings.smtp_host or "Missing",
                "port": settings.smtp_port,
                "username_set": bool(settings.smtp_username),
                "password_set": bool(settings.smtp_password),
                "tls": settings.smtp_use_tls,
                "from_email": settings.notification_from_email or "Missing",
                "admin_email": settings.admin_email or "Missing",
                "missing": notifier.missing_configuration(),
            },
        }

    def readiness_check(self, label: str, ok: bool, action: str) -> dict[str, object]:
        return {"label": label, "ok": bool(ok), "action": action}

    def policy_pages(self) -> dict[str, dict[str, object]]:
        support_email = settings.support_email or settings.admin_email or "support@example.com"
        support_phone = settings.support_phone or "Add SUPPORT_PHONE in production"
        business_address = settings.business_address or "Add BUSINESS_ADDRESS in production"
        return {
            "/shipping-policy": {
                "title": "Shipping Policy",
                "kicker": "Shipping",
                "intro": "How The Scentist packs and ships fragrance orders across India.",
                "sections": [
                    ("Dispatch", "Orders are normally prepared within 1-3 business days after confirmation or successful payment."),
                    ("Delivery", "Delivery timelines depend on the destination and courier partner. Metro deliveries are typically faster than remote locations."),
                    ("Tracking", "Once shipped, the tracking number and courier details are added to your order and shared by email when notifications are configured."),
                    ("Support", f"For delivery help, contact {support_email} or {support_phone}."),
                ],
            },
            "/refund-policy": {
                "title": "Refund and Return Policy",
                "kicker": "Returns",
                "intro": "Fragrance products require careful handling, so returns are reviewed case by case.",
                "sections": [
                    ("Eligibility", "Unopened, unused, and sealed products may be reviewed for return requests raised within 48 hours of delivery."),
                    ("Decants and partials", "Decants, testers, and partial bottles are final sale unless the wrong item or a damaged item was delivered."),
                    ("Damage or mismatch", "Share unboxing photos or video immediately if the parcel arrives damaged or the product does not match the order."),
                    ("Refunds", "Approved refunds are processed back to the original payment method or another mutually agreed method after inspection."),
                ],
            },
            "/privacy-policy": {
                "title": "Privacy Policy",
                "kicker": "Privacy",
                "intro": "We collect only the information needed to process orders, payments, customer accounts, and support requests.",
                "sections": [
                    ("Data collected", "Name, email, phone, address, order details, payment references, and account session data may be stored."),
                    ("Payments", "Online payments are processed through Razorpay. Card, UPI, and netbanking details are handled by the payment gateway, not stored by The Scentist."),
                    ("Use", "Information is used for checkout, fulfilment, fraud prevention, customer support, and operational analytics."),
                    ("Contact", f"Privacy requests can be sent to {support_email}."),
                ],
            },
            "/terms": {
                "title": "Terms of Service",
                "kicker": "Terms",
                "intro": "By using The Scentist, you agree to purchase only for lawful personal use and provide accurate checkout details.",
                "sections": [
                    ("Authenticity", "Products are represented as accurately as possible with available stock, images, descriptions, and variants."),
                    ("Pricing and stock", "Prices and availability can change. An order may require review if stock, payment, or address information is inconsistent."),
                    ("Cancellations", "Orders can be cancelled before dispatch where operationally possible. Paid orders may require payment gateway reconciliation."),
                    ("Business details", business_address),
                ],
            },
            "/contact": {
                "title": "Contact",
                "kicker": "Support",
                "intro": "For order, shipping, sourcing, or concierge help, reach The Scentist support desk.",
                "sections": [
                    ("Email", support_email),
                    ("Phone", support_phone),
                    ("Business address", business_address),
                    ("Order help", "Include your order number, email, and phone number when asking about an existing order."),
                ],
            },
        }

    def admin_cookie_signature(self) -> str:
        if not settings.admin_token:
            return ""
        return hmac.new(
            settings.admin_token.encode("utf-8"),
            b"the-scentist-admin",
            hashlib.sha256,
        ).hexdigest()

    def admin_csrf_token(self, request: Request) -> str:
        cookie = self.admin_cookie_value(request)
        if not cookie:
            return ""
        return hmac.new(
            settings.admin_token.encode("utf-8"),
            f"csrf:{cookie}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def customer_csrf_token(self, request: Request) -> str:
        cookie = self.customer_cookie_value(request)
        if not cookie:
            return ""
        return hmac.new(
            APP_HMAC_SECRET.encode("utf-8"),
            f"customer-csrf:{cookie}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def validate_customer_csrf(self, request: Request, fields: dict[str, str]) -> bool:
        expected = self.customer_csrf_token(request)
        return bool(expected) and secrets.compare_digest(fields.get("csrf_token", ""), expected)

    def absolute_url(self, request: Request, path: str) -> str:
        if settings.base_url:
            return f"{settings.base_url}{path}"
        host = request.headers.get("host", f"127.0.0.1:{settings.port}")
        if not re.fullmatch(r"[A-Za-z0-9.:[\]-]+", host):
            host = f"127.0.0.1:{settings.port}"
        scheme = request.headers.get("x-forwarded-proto", "http").split(",", 1)[0].strip()
        if scheme not in {"http", "https"}:
            scheme = "http"
        return f"{scheme}://{host}{path}"

    def enforce_rate_limit(
        self, request: Request, scope: str, limit: int, window_seconds: int
    ) -> Response | None:
        allowed, retry_after = rate_limiter.check(
            f"{scope}:{request.remote_ip or 'unknown'}", limit, window_seconds
        )
        if allowed:
            return None
        return self.json_response(
            {"error": "Too many requests. Please wait and try again."},
            status=HTTPStatus.TOO_MANY_REQUESTS,
            headers=[("Retry-After", str(retry_after))],
        )

    def checkout_idempotency_key(self, request: Request) -> str:
        key = request.headers.get("idempotency-key", "").strip()
        if len(key) < 16 or len(key) > 128 or not re.fullmatch(r"[A-Za-z0-9._:-]+", key):
            raise ValidationError("A valid Idempotency-Key header is required for checkout.")
        return key

    def razorpay_checkout_response(self, order: dict) -> Response:
        return self.json_response(
            {
                "checkout": {
                    "razorpay_key_id": settings.razorpay_key_id,
                    "gateway_order_id": order["gateway_order_id"],
                    "amount_subunits": int(order["total_inr"]) * 100,
                    "currency": settings.currency,
                    "local_order_number": order["order_number"],
                    "customer_name": order["customer_name"],
                    "email": order["email"],
                    "phone": order["phone"],
                }
            },
            status=HTTPStatus.CREATED,
        )

    def validate_admin_csrf(self, request: Request, fields: dict[str, str]) -> Response | None:
        if secrets.compare_digest(fields.get("csrf_token", ""), self.admin_csrf_token(request)):
            return None
        return self.json_response({"error": "Invalid admin form token."}, status=HTTPStatus.FORBIDDEN)

    def admin_fragrance_form_data(
        self,
        fragrance: dict | None = None,
        *,
        fields: dict[str, str] | None = None,
    ) -> dict:
        if fields is not None:
            return {
                "slug": fields.get("slug", ""),
                "brand": fields.get("brand", ""),
                "name": fields.get("name", ""),
                "collection_type": fields.get("collection_type", "niche"),
                "gender": fields.get("gender", "unisex"),
                "family": fields.get("family", ""),
                "concentration": fields.get("concentration", "Eau de Parfum"),
                "origin": fields.get("origin", "Imported"),
                "description": fields.get("description", ""),
                "signature": fields.get("signature", ""),
                "top_notes": fields.get("top_notes", ""),
                "heart_notes": fields.get("heart_notes", ""),
                "base_notes": fields.get("base_notes", ""),
                "accent_from": fields.get("accent_from", "#c2b4a3"),
                "accent_to": fields.get("accent_to", "#17120f"),
                "image_url": fields.get("image_url", ""),
                "photo_icon_url": fields.get("photo_icon_url", ""),
                "artwork_kind": fields.get("artwork_kind", "photo"),
                "bottle_size_ml": fields.get("bottle_size_ml", "100"),
                "featured": fields.get("featured") == "on",
                "rank": fields.get("rank", "999"),
                "is_active": fields.get("is_active") == "on",
            }

        if fragrance is None:
            return {
                "slug": "",
                "brand": "",
                "name": "",
                "collection_type": "niche",
                "gender": "unisex",
                "family": "",
                "concentration": "Eau de Parfum",
                "origin": "Imported",
                "description": "",
                "signature": "",
                "top_notes": "",
                "heart_notes": "",
                "base_notes": "",
                "accent_from": "#c2b4a3",
                "accent_to": "#17120f",
                "image_url": "",
                "photo_icon_url": "",
                "artwork_kind": "photo",
                "bottle_size_ml": "100",
                "featured": False,
                "rank": "999",
                "is_active": True,
            }

        return {
            "slug": fragrance.get("slug", ""),
            "brand": fragrance.get("brand", ""),
            "name": fragrance.get("name", ""),
            "collection_type": fragrance.get("collection_type", "niche"),
            "gender": fragrance.get("gender", "unisex"),
            "family": fragrance.get("family", ""),
            "concentration": fragrance.get("concentration", "Eau de Parfum"),
            "origin": fragrance.get("origin", "Imported"),
            "description": fragrance.get("description", ""),
            "signature": fragrance.get("signature", ""),
            "top_notes": ", ".join(fragrance.get("top_notes") or []),
            "heart_notes": ", ".join(fragrance.get("heart_notes") or []),
            "base_notes": ", ".join(fragrance.get("base_notes") or []),
            "accent_from": fragrance.get("accent_from", "#c2b4a3"),
            "accent_to": fragrance.get("accent_to", "#17120f"),
            "image_url": fragrance.get("image_url", ""),
            "photo_icon_url": fragrance.get("photo_icon_url", ""),
            "artwork_kind": fragrance.get("artwork_kind", "photo"),
            "bottle_size_ml": str(fragrance.get("bottle_size_ml", 100)),
            "featured": bool(fragrance.get("featured")),
            "rank": str(fragrance.get("rank", 999)),
            "is_active": bool(fragrance.get("is_active", True)),
        }

    def admin_variant_form_data(self, fields: dict[str, str] | None = None) -> dict:
        fields = fields or {}
        return {
            "variant_id": fields.get("variant_id", ""),
            "sku": fields.get("sku", ""),
            "sale_type": fields.get("sale_type", "retail"),
            "size_label": fields.get("size_label", ""),
            "size_ml": fields.get("size_ml", "100"),
            "price_inr": fields.get("price_inr", ""),
            "compare_at_price_inr": fields.get("compare_at_price_inr", "0"),
            "stock_units": fields.get("stock_units", "1"),
            "badge": fields.get("badge", ""),
            "statement": fields.get("statement", ""),
        }

    def admin_cookie_value(self, request: Request) -> str:
        cookie_header = request.headers.get("cookie", "")
        for part in cookie_header.split(";"):
            name, _, value = part.strip().partition("=")
            if name == "ts_admin":
                return value
        return ""

    def is_admin_request(self, request: Request) -> bool:
        if not settings.admin_token:
            return False
        return secrets.compare_digest(self.admin_cookie_value(request), self.admin_cookie_signature())

    def build_admin_cookie(self) -> str:
        attributes = [
            f"ts_admin={self.admin_cookie_signature()}",
            "Path=/admin",
            "HttpOnly",
            "SameSite=Lax",
            "Max-Age=28800",
        ]
        if settings.enforce_hsts:
            attributes.append("Secure")
        return "; ".join(attributes)

    def clear_admin_cookie(self) -> str:
        attributes = [
            "ts_admin=",
            "Path=/admin",
            "HttpOnly",
            "SameSite=Lax",
            "Max-Age=0",
        ]
        if settings.enforce_hsts:
            attributes.append("Secure")
        return "; ".join(attributes)

    def customer_from_request(self, request: Request) -> dict | None:
        return db.get_customer_by_session_token(self.customer_cookie_value(request))

    def customer_cookie_value(self, request: Request) -> str:
        cookie_header = request.headers.get("cookie", "")
        for part in cookie_header.split(";"):
            name, _, value = part.strip().partition("=")
            if name == "ts_customer":
                return value
        return ""

    def build_customer_cookie(self, session_token: str) -> str:
        attributes = [
            f"ts_customer={session_token}",
            "Path=/",
            "HttpOnly",
            "SameSite=Lax",
            "Max-Age=2592000",
        ]
        if settings.enforce_hsts:
            attributes.append("Secure")
        return "; ".join(attributes)

    def clear_customer_cookie(self) -> str:
        attributes = [
            "ts_customer=",
            "Path=/",
            "HttpOnly",
            "SameSite=Lax",
            "Max-Age=0",
        ]
        if settings.enforce_hsts:
            attributes.append("Secure")
        return "; ".join(attributes)

    def safe_next_path(self, path: str) -> str:
        path = str(path or "").strip()
        if not path.startswith("/") or path.startswith("//"):
            return "/account"
        if path.startswith("/admin"):
            return "/account"
        return path

    def html_response(
        self,
        body: bytes,
        *,
        status: HTTPStatus = HTTPStatus.OK,
        headers: list[tuple[str, str]] | None = None,
    ) -> Response:
        return Response(
            status=status,
            body=body,
            content_type="text/html; charset=utf-8",
            headers=headers or [],
        )

    def json_response(
        self,
        payload: dict,
        *,
        status: HTTPStatus = HTTPStatus.OK,
        headers: list[tuple[str, str]] | None = None,
    ) -> Response:
        body = json.dumps(payload).encode("utf-8")
        return Response(
            status=status,
            body=body,
            content_type="application/json; charset=utf-8",
            headers=headers or [],
        )

    def redirect_response(
        self,
        location: str,
        *,
        headers: list[tuple[str, str]] | None = None,
    ) -> Response:
        return Response(
            status=HTTPStatus.SEE_OTHER,
            body=b"",
            content_type="text/plain; charset=utf-8",
            headers=[("Location", location), *(headers or [])],
        )

    def bytes_response(
        self,
        body: bytes,
        *,
        content_type: str,
        status: HTTPStatus = HTTPStatus.OK,
        cache_control: str = "no-store",
        headers: list[tuple[str, str]] | None = None,
        content_length: int | None = None,
    ) -> Response:
        return Response(
            status=status,
            body=body,
            content_type=content_type,
            cache_control=cache_control,
            headers=headers or [],
            content_length=content_length,
        )

    def render_404(self, path: str) -> Response:
        context = {
            **self.get_site_context(path),
            "page_title": "Page not found",
            "missing_path": path,
        }
        return self.html_response(render_template("404.html", context), status=HTTPStatus.NOT_FOUND)

    def serve_file(self, root: Path, relative_path: str, request: Request) -> Response:
        file_path = (root / relative_path).resolve()
        root_resolved = root.resolve()

        if root_resolved not in file_path.parents and file_path != root_resolved:
            return self.json_response({"error": "Forbidden."}, status=HTTPStatus.FORBIDDEN)

        if not file_path.exists() or not file_path.is_file():
            return self.json_response({"error": "Not found."}, status=HTTPStatus.NOT_FOUND)

        mime_type, _ = mimetypes.guess_type(str(file_path))
        content_type = mime_type or "application/octet-stream"
        file_size = file_path.stat().st_size
        response_headers = [("Accept-Ranges", "bytes")]
        range_header = request.headers.get("range", "")
        if range_header:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
            if not match or (not match.group(1) and not match.group(2)):
                return self.bytes_response(
                    b"",
                    content_type=content_type,
                    status=HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE,
                    headers=[("Content-Range", f"bytes */{file_size}"), *response_headers],
                )
            if match.group(1):
                start = int(match.group(1))
                end = int(match.group(2)) if match.group(2) else file_size - 1
            else:
                suffix_length = int(match.group(2))
                start = max(0, file_size - suffix_length)
                end = file_size - 1
            if start >= file_size or start > end:
                return self.bytes_response(
                    b"",
                    content_type=content_type,
                    status=HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE,
                    headers=[("Content-Range", f"bytes */{file_size}"), *response_headers],
                )
            end = min(end, file_size - 1)
            length = end - start + 1
            with file_path.open("rb") as handle:
                handle.seek(start)
                body = handle.read(length)
            return self.bytes_response(
                body,
                content_type=content_type,
                status=HTTPStatus.PARTIAL_CONTENT,
                cache_control=f"public, max-age={settings.static_cache_max_age_seconds}",
                headers=[("Content-Range", f"bytes {start}-{end}/{file_size}"), *response_headers],
            )
        return self.bytes_response(
            file_path.read_bytes(),
            content_type=content_type,
            cache_control=f"public, max-age={settings.static_cache_max_age_seconds}",
            headers=response_headers,
            content_length=file_size,
        )

    def _build_request(self, environ, request_id: str) -> Request:
        method = environ.get("REQUEST_METHOD", "GET").upper()
        path = canonical_path(unquote(environ.get("PATH_INFO", "") or "/"))
        query = parse_qs(environ.get("QUERY_STRING", ""))
        headers = self._extract_headers(environ)
        body = b""

        if method in {"POST", "PUT", "PATCH"}:
            raw_length = environ.get("CONTENT_LENGTH", "").strip()
            if raw_length:
                try:
                    length = int(raw_length)
                except ValueError as exc:
                    raise ValueError("Invalid Content-Length header.") from exc
                if length < 0:
                    raise ValueError("Invalid Content-Length header.")
                if length > settings.max_request_body_bytes:
                    raise RequestTooLargeError("Request body too large.")
                body = environ["wsgi.input"].read(length) if length else b""
            else:
                body = environ["wsgi.input"].read(settings.max_request_body_bytes + 1)
                if len(body) > settings.max_request_body_bytes:
                    raise RequestTooLargeError("Request body too large.")

        return Request(
            method=method,
            path=path,
            query=query,
            headers=headers,
            body=body,
            request_id=request_id,
            remote_ip=(headers.get("x-forwarded-for", "").split(",", 1)[0].strip() or environ.get("REMOTE_ADDR", "")),
        )

    def _extract_headers(self, environ) -> dict[str, str]:
        headers: dict[str, str] = {}
        for key, value in environ.items():
            if key.startswith("HTTP_"):
                header_name = key[5:].replace("_", "-").lower()
                headers[header_name] = value
        if environ.get("CONTENT_TYPE"):
            headers["content-type"] = environ["CONTENT_TYPE"]
        if environ.get("CONTENT_LENGTH"):
            headers["content-length"] = environ["CONTENT_LENGTH"]
        return headers

    def content_security_policy(self) -> str:
        directives = [
            "default-src 'self'",
            "base-uri 'self'",
            "frame-ancestors 'none'",
            "img-src 'self' data: https:",
            "script-src 'self' 'unsafe-inline' https://checkout.razorpay.com",
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
            "font-src 'self' data: https://fonts.gstatic.com",
            "connect-src 'self' https://api.razorpay.com https://checkout.razorpay.com",
            "frame-src 'self' https://api.razorpay.com https://checkout.razorpay.com",
            "form-action 'self' https://api.razorpay.com https://checkout.razorpay.com",
        ]
        return "; ".join(directives)


class ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


class QuietWSGIRequestHandler(WSGIRequestHandler):
    def log_message(self, format: str, *args) -> None:
        return


application = PerfumeryApplication()


def main() -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(asctime)s %(levelname)s %(message)s")

    with make_server(
        settings.host,
        settings.port,
        application,
        server_class=ThreadingWSGIServer,
        handler_class=QuietWSGIRequestHandler,
    ) as server:
        print(f"{settings.site_name} is running on http://127.0.0.1:{settings.port}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            db.close()


if __name__ == "__main__":
    main()
