from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from perfumery_app.config import load_settings
from perfumery_app.database import Database, ValidationError


class DatabaseCheckoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        settings = replace(
            load_settings(),
            app_env="test",
            database_url="",
            db_host="",
            auto_seed_catalog=False,
            default_country="India",
        )
        self.db = Database(Path(self.temp_dir.name) / "test.sqlite3", settings)
        self.db.initialize()
        self.db.save_admin_fragrance(
            {
                "slug": "test-house-test-scent",
                "brand": "Test House",
                "name": "Test Scent",
                "collection_type": "niche",
                "gender": "unisex",
                "family": "woody",
                "image_url": "/assets/test.webp",
                "is_active": "on",
            }
        )
        fragrance = self.db.save_admin_variant(
            "test-house-test-scent",
            {
                "sku": "test-house-test-scent-decant-10",
                "sale_type": "decant",
                "size_label": "10 ml",
                "size_ml": "10",
                "price_inr": "1200",
                "stock_units": "10",
            },
        )
        self.variant_id = fragrance["variants"][0]["id"]

    def tearDown(self) -> None:
        self.db.close()
        self.temp_dir.cleanup()

    def payload(self, email: str = "buyer@example.com") -> dict:
        return {
            "customer": {
                "customer_name": "Test Buyer",
                "email": email,
                "phone": "9876543210",
                "shipping_line1": "12 Test Market Road",
                "shipping_line2": "",
                "city": "New Delhi",
                "state": "Delhi",
                "postal_code": "110001",
                "country": "India",
                "payment_method": "Cash on Delivery",
                "delivery_notes": "",
            },
            "items": [{"variant_id": self.variant_id, "quantity": 2}],
        }

    def stock(self) -> int:
        with self.db.connect() as conn:
            row = conn.execute("SELECT stock_units FROM variants WHERE id = ?", (self.variant_id,)).fetchone()
        return int(row["stock_units"])

    def test_idempotent_checkout_deducts_stock_once(self) -> None:
        first = self.db.create_order(self.payload(), idempotency_key="checkout-idempotency-0001")
        second = self.db.create_order(self.payload(), idempotency_key="checkout-idempotency-0001")

        self.assertEqual(first["order_number"], second["order_number"])
        self.assertEqual(8, self.stock())

    def test_unverified_account_cannot_claim_guest_orders(self) -> None:
        guest_order = self.db.create_order(self.payload(), idempotency_key="guest-checkout-00000001")
        customer = self.db.create_customer("Test Buyer", "buyer@example.com", "long-password-123")

        self.assertEqual([], self.db.list_customer_orders(customer["id"]))
        token = self.db.create_email_verification(customer["id"])
        verified = self.db.verify_customer_email(token)
        orders = self.db.list_customer_orders(customer["id"])

        self.assertTrue(verified["email_verified_at"])
        self.assertEqual([guest_order["order_number"]], [order["order_number"] for order in orders])

    def test_order_numbers_have_high_entropy_suffix(self) -> None:
        order = self.db.create_order(self.payload(), idempotency_key="unique-checkout-0000001")
        suffix = order["order_number"].rsplit("-", 1)[-1]
        self.assertEqual(12, len(suffix))
        self.assertRegex(suffix, r"^[0-9A-F]+$")

    def test_invalid_indian_pin_is_rejected(self) -> None:
        payload = self.payload()
        payload["customer"]["postal_code"] = "000001"
        with self.assertRaisesRegex(ValidationError, "Indian postal code"):
            self.db.create_order(payload, idempotency_key="invalid-checkout-00001")

    def test_fractional_quantity_is_rejected(self) -> None:
        payload = self.payload()
        payload["items"][0]["quantity"] = 1.5
        with self.assertRaisesRegex(ValidationError, "whole number"):
            self.db.create_order(payload, idempotency_key="fractional-checkout-01")

    def test_notification_outbox_is_deduplicated(self) -> None:
        order = self.db.create_order(self.payload(), idempotency_key="notify-checkout-00001")
        self.db.enqueue_order_notification(order["order_number"], "order_received")
        self.db.enqueue_order_notification(order["order_number"], "order_received")
        with self.db.connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM notification_outbox").fetchone()
        self.assertEqual(1, row["count"])


if __name__ == "__main__":
    unittest.main()
