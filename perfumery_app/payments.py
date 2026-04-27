from __future__ import annotations

import base64
import hashlib
import hmac
import json
import urllib.error
import urllib.request
from typing import Any


class PaymentGatewayError(Exception):
    """Raised when the payment gateway rejects a request."""


class RazorpayClient:
    api_base = "https://api.razorpay.com/v1"

    def __init__(self, key_id: str, key_secret: str):
        self.key_id = key_id.strip()
        self.key_secret = key_secret.strip()

    @property
    def enabled(self) -> bool:
        return bool(self.key_id and self.key_secret)

    def create_order(
        self,
        *,
        amount_subunits: int,
        receipt: str,
        notes: dict[str, Any] | None = None,
        currency: str = "INR",
    ) -> dict[str, Any]:
        if not self.enabled:
            raise PaymentGatewayError("Razorpay keys are not configured.")

        payload = {
            "amount": amount_subunits,
            "currency": currency,
            "receipt": receipt,
            "notes": notes or {},
        }
        return self._request("POST", "/orders", payload)

    def verify_payment_signature(self, *, order_id: str, payment_id: str, signature: str) -> bool:
        if not self.enabled:
            return False

        message = f"{order_id}|{payment_id}".encode("utf-8")
        digest = hmac.new(
            self.key_secret.encode("utf-8"),
            message,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(digest, signature)

    def verify_webhook_signature(self, *, body: bytes, signature: str, webhook_secret: str) -> bool:
        if not signature or not webhook_secret:
            return False

        digest = hmac.new(
            webhook_secret.encode("utf-8"),
            body,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(digest, signature)

    def _request(self, method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        encoded = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.api_base}{path}",
            data=encoded,
            method=method,
            headers={
                "Authorization": self._auth_header(),
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "AllureAlchemy/1.0",
            },
        )

        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "ignore")
            try:
                data = json.loads(body)
                description = data.get("error", {}).get("description") or data.get("message") or body
            except json.JSONDecodeError:
                description = body or exc.reason
            raise PaymentGatewayError(f"Razorpay order creation failed: {description}") from exc
        except urllib.error.URLError as exc:
            raise PaymentGatewayError(f"Unable to reach Razorpay: {exc.reason}") from exc

    def _auth_header(self) -> str:
        token = base64.b64encode(f"{self.key_id}:{self.key_secret}".encode("utf-8")).decode("ascii")
        return f"Basic {token}"

