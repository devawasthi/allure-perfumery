from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from typing import Any

from perfumery_app.config import Settings


logger = logging.getLogger("the_scentist.notifications")


class OrderNotifier:
    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return bool(
            self.settings.smtp_host
            and self.settings.notification_from_email
            and (self.settings.admin_email or self.settings.smtp_username)
        )

    def send_order_received(self, order: dict[str, Any]) -> bool:
        if not self.enabled:
            logger.info("Order notification skipped because SMTP is not configured.")
            return False

        customer_subject = f"The Scentist order {order['order_number']}"
        customer_body = self._customer_body(order)
        delivered = self._send(order["email"], customer_subject, customer_body)

        if self.settings.admin_email:
            admin_subject = f"New order {order['order_number']} - {order['payment_status'].title()}"
            delivered = self._send(self.settings.admin_email, admin_subject, self._admin_body(order)) and delivered
        return delivered

    def _send(self, recipient: str, subject: str, body: str) -> bool:
        message = EmailMessage()
        message["From"] = self.settings.notification_from_email
        message["To"] = recipient
        message["Subject"] = subject
        message.set_content(body)

        try:
            with smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=12) as smtp:
                if self.settings.smtp_use_tls:
                    smtp.starttls()
                if self.settings.smtp_username or self.settings.smtp_password:
                    smtp.login(self.settings.smtp_username, self.settings.smtp_password)
                smtp.send_message(message)
            return True
        except Exception:
            logger.exception("Unable to send order notification to %s", recipient)
            return False

    def _customer_body(self, order: dict[str, Any]) -> str:
        lines = [
            f"Thank you for shopping with The Scentist.",
            "",
            f"Order: {order['order_number']}",
            f"Status: {order['status']}",
            f"Payment: {order['payment_status'].title()}",
            f"Total: INR {order['total_inr']:,}",
            "",
            "Items:",
        ]
        for item in order["items"]:
            lines.append(
                f"- {item['brand']} {item['fragrance_name']} / {item['size_label']} x {item['quantity']}"
            )
        lines.extend(["", "We will share the next update as soon as your order is ready."])
        return "\n".join(lines)

    def _admin_body(self, order: dict[str, Any]) -> str:
        lines = [
            f"Order: {order['order_number']}",
            f"Customer: {order['customer_name']}",
            f"Email: {order['email']}",
            f"Phone: {order['phone']}",
            f"Payment: {order['payment_method']} / {order['payment_status']}",
            f"Status: {order['status']}",
            f"Total: INR {order['total_inr']:,}",
            "",
            "Ship to:",
            f"{order['shipping_line1']}",
            f"{order['shipping_line2']}" if order.get("shipping_line2") else "",
            f"{order['city']}, {order['state']} {order['postal_code']}, {order['country']}",
            "",
            "Items:",
        ]
        for item in order["items"]:
            lines.append(
                f"- {item['brand']} {item['fragrance_name']} / {item['size_label']} x {item['quantity']}"
            )
        return "\n".join(line for line in lines if line != "")
