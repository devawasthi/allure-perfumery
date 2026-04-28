from __future__ import annotations

import json
import logging
import mimetypes
import os
import time
from dataclasses import dataclass, field
from http import HTTPStatus
from pathlib import Path
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, unquote
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from jinja2 import Environment, FileSystemLoader, select_autoescape

from perfumery_app.artwork import build_fragrance_artwork
from perfumery_app.config import load_settings
from perfumery_app.database import Database, ValidationError
from perfumery_app.payments import PaymentGatewayError, RazorpayClient

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional local helper
    def load_dotenv(*args, **kwargs):
        return False


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

settings = load_settings()

TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
ASSETS_DIR = STATIC_DIR / "assets"
configured_sqlite_path = Path(settings.sqlite_database_path or "data/perfumery.sqlite3")
DATABASE_PATH = configured_sqlite_path if configured_sqlite_path.is_absolute() else BASE_DIR / configured_sqlite_path

db = Database(DATABASE_PATH, settings)
db.initialize()
razorpay = RazorpayClient(settings.razorpay_key_id, settings.razorpay_key_secret)

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


def first_value(query: dict[str, list[str]], key: str) -> str:
    return (query.get(key) or [""])[0].strip()


def render_template(name: str, context: dict) -> bytes:
    template = env.get_template(name)
    return template.render(**context).encode("utf-8")


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


class PerfumeryApplication:
    def __call__(self, environ, start_response):
        started_at = time.perf_counter()
        request_id = environ.get("HTTP_X_REQUEST_ID") or f"req_{int.from_bytes(os.urandom(6), 'big'):012x}"

        try:
            request = self._build_request(environ, request_id)
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
            ("Content-Length", str(len(response.body))),
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
            )
        return response

    def handle_get(self, request: Request) -> Response:
        path = request.path
        query = request.query

        if path.startswith("/static/"):
            return self.serve_file(STATIC_DIR, path.removeprefix("/static/"))

        if path.startswith("/assets/"):
            return self.serve_file(ASSETS_DIR, path.removeprefix("/assets/"))

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
            return self.json_response(
                {"status": "ready" if ready else "degraded", "database": settings.database_engine},
                status=HTTPStatus.OK if ready else HTTPStatus.SERVICE_UNAVAILABLE,
            )

        if path == "/api/fragrances":
            filters = self.extract_filters(query)
            return self.json_response({"filters": filters, "items": db.list_fragrances(filters)})

        if path.startswith("/api/fragrances/"):
            slug = path.removeprefix("/api/fragrances/")
            fragrance = db.get_fragrance(slug)
            if fragrance is None:
                return self.json_response({"error": "Fragrance not found."}, status=HTTPStatus.NOT_FOUND)
            return self.json_response(fragrance)

        if path == "/api/cart-items":
            raw_ids = first_value(query, "variant_ids")
            variant_ids = [int(item) for item in raw_ids.split(",") if item.isdigit()]
            return self.json_response({"items": db.get_cart_items(variant_ids)})

        if path.startswith("/api/orders/"):
            order_number = path.removeprefix("/api/orders/")
            order = db.get_order(order_number)
            if order is None:
                return self.json_response({"error": "Order not found."}, status=HTTPStatus.NOT_FOUND)
            return self.json_response(order)

        if path.startswith("/api/"):
            return self.json_response({"error": "Unsupported route."}, status=HTTPStatus.NOT_FOUND)

        if path == "/":
            featured = db.get_featured(9)
            context = {
                **self.get_site_context(path),
                "page_title": "Luxury niche and designer fragrances",
                "hero_image": "/assets/hero6.jpg",
                "showcase_image": "/assets/banner1.jpg",
                "featured": featured,
                "brands": db.get_brand_showcase(16),
                "filters": db.list_filters(),
            }
            return self.html_response(render_template("home.html", context))

        if path == "/catalog":
            filters = self.extract_filters(query)
            items = db.list_fragrances(filters)
            context = {
                **self.get_site_context(path),
                "page_title": "Perfume catalog",
                "catalog_items": items,
                "filters": db.list_filters(),
                "active_filters": filters,
            }
            return self.html_response(render_template("catalog.html", context))

        if path.startswith("/fragrances/"):
            slug = path.removeprefix("/fragrances/")
            fragrance = db.get_fragrance(slug)
            if fragrance is None:
                return self.render_404(path)
            context = {
                **self.get_site_context(path),
                "page_title": f"{fragrance['brand']} {fragrance['name']}",
                "fragrance": fragrance,
                "related_items": db.get_related_fragrances(fragrance),
            }
            return self.html_response(render_template("product.html", context))

        if path == "/cart":
            context = {
                **self.get_site_context(path),
                "page_title": "Your fragrance cart",
            }
            return self.html_response(render_template("cart.html", context))

        if path == "/checkout":
            context = {
                **self.get_site_context(path),
                "page_title": "Checkout",
            }
            return self.html_response(render_template("checkout.html", context))

        if path.startswith("/order/"):
            order_number = path.removeprefix("/order/")
            order = db.get_order(order_number)
            if order is None:
                return self.render_404(path)
            context = {
                **self.get_site_context(path),
                "page_title": f"Order {order_number}",
                "order": order,
            }
            return self.html_response(render_template("order.html", context))

        return self.render_404(path)

    def handle_post(self, request: Request) -> Response:
        path = request.path

        if path == "/api/orders":
            try:
                payload = self.read_json_body(request)
                customer = payload.get("customer") or {}
                if customer.get("payment_method") == "Razorpay":
                    raise ValidationError("Use the Razorpay checkout flow for online payments.")
                order = db.create_order(payload)
                return self.json_response({"order": order}, status=HTTPStatus.CREATED)
            except ValidationError as exc:
                return self.json_response({"error": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
            except json.JSONDecodeError:
                return self.json_response({"error": "Invalid JSON body."}, status=HTTPStatus.BAD_REQUEST)
            except Exception as exc:  # pragma: no cover
                return self.json_response({"error": f"Unable to place order: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

        if path == "/api/checkout/razorpay-order":
            if not razorpay.enabled:
                return self.json_response(
                    {"error": "Razorpay is not configured on this deployment."},
                    status=HTTPStatus.SERVICE_UNAVAILABLE,
                )

            try:
                payload = self.read_json_body(request)
                customer = payload.get("customer") or {}
                items = payload.get("items") or []
                customer["payment_method"] = "Razorpay"
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
                )
                return self.json_response(
                    {
                        "checkout": {
                            "razorpay_key_id": settings.razorpay_key_id,
                            "gateway_order_id": gateway_order["id"],
                            "amount_subunits": gateway_order["amount"],
                            "currency": gateway_order["currency"],
                            "local_order_number": local_order["order_number"],
                            "customer_name": local_order["customer_name"],
                            "email": local_order["email"],
                            "phone": local_order["phone"],
                        }
                    },
                    status=HTTPStatus.CREATED,
                )
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

        if path == "/api/webhooks/razorpay":
            signature = request.headers.get("x-razorpay-signature", "")
            event_type = ""

            if settings.razorpay_webhook_secret:
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
                    db.finalize_razorpay_order_from_webhook(
                        gateway_order_id=entity["order_id"],
                        gateway_payment_id=entity.get("id", ""),
                        paid_amount_subunits=int(entity.get("amount", 0) or 0),
                    )
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
        }

    def get_site_context(self, path: str) -> dict:
        metrics = db.get_metrics()
        preview_label = os.getenv("PREVIEW_LABEL", "").strip()
        if not preview_label and not settings.is_production:
            preview_label = settings.app_env.upper()
        return {
            "site_name": settings.site_name,
            "site_logo": "/assets/perfume-logo.png",
            "current_path": path,
            "preview_label": preview_label,
            "site_metrics": metrics,
            "payment": {
                "razorpay_enabled": settings.razorpay_enabled,
                "razorpay_key_id": settings.razorpay_key_id,
                "manual_checkout_enabled": settings.enable_manual_checkout,
            },
            "nav_links": [
                {"href": "/", "label": "Home"},
                {"href": "/catalog?collection_type=niche", "label": "Niche"},
                {"href": "/catalog?collection_type=designer", "label": "Designer"},
                {"href": "/catalog?sale_type=decant", "label": "Decants"},
                {"href": "/catalog?sale_type=partial", "label": "Partials"},
                {"href": "/checkout", "label": "Checkout"},
            ],
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

    def read_json_body(self, request: Request) -> dict:
        body = request.body or b"{}"
        return json.loads(body.decode("utf-8"))

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

    def bytes_response(
        self,
        body: bytes,
        *,
        content_type: str,
        status: HTTPStatus = HTTPStatus.OK,
        cache_control: str = "no-store",
        headers: list[tuple[str, str]] | None = None,
    ) -> Response:
        return Response(
            status=status,
            body=body,
            content_type=content_type,
            cache_control=cache_control,
            headers=headers or [],
        )

    def render_404(self, path: str) -> Response:
        context = {
            **self.get_site_context(path),
            "page_title": "Page not found",
            "missing_path": path,
        }
        return self.html_response(render_template("404.html", context), status=HTTPStatus.NOT_FOUND)

    def serve_file(self, root: Path, relative_path: str) -> Response:
        file_path = (root / relative_path).resolve()
        root_resolved = root.resolve()

        if root_resolved not in file_path.parents and file_path != root_resolved:
            return self.json_response({"error": "Forbidden."}, status=HTTPStatus.FORBIDDEN)

        if not file_path.exists() or not file_path.is_file():
            return self.json_response({"error": "Not found."}, status=HTTPStatus.NOT_FOUND)

        mime_type, _ = mimetypes.guess_type(str(file_path))
        content_type = mime_type or "application/octet-stream"
        return self.bytes_response(
            file_path.read_bytes(),
            content_type=content_type,
            cache_control=f"public, max-age={settings.static_cache_max_age_seconds}",
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
