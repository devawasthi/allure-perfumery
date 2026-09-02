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
        return not self.missing_configuration()

    def missing_configuration(self) -> list[str]:
        missing: list[str] = []
        if not self.settings.smtp_host:
            missing.append("SMTP_HOST")
        if not self.settings.notification_from_email:
            missing.append("NOTIFICATION_FROM_EMAIL")
        if not self.settings.admin_email and not self.settings.smtp_username:
            missing.append("ADMIN_EMAIL or SMTP_USERNAME")
        return missing

    def send_test_email(self, recipient: str) -> tuple[bool, str]:
        recipient = str(recipient or "").strip()
        if not recipient:
            return False, "Enter an email address to receive the SMTP test."

        missing = self.missing_configuration()
        if missing:
            return False, f"Missing SMTP configuration: {', '.join(missing)}."

        subject = "The Scentist SMTP test"
        body = (
            "SMTP is connected for The Scentist.\n\n"
            "If you received this email, local order notifications can be delivered."
        )
        try:
            self._send_message(recipient, subject, body)
            return True, f"Sent test email to {recipient}."
        except Exception as exc:
            logger.exception("Unable to send SMTP test email to %s", recipient)
            return False, f"{exc.__class__.__name__}: {exc}"

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

    def send_order_status_update(self, order: dict[str, Any]) -> bool:
        if not self.enabled:
            logger.info("Order status notification skipped because SMTP is not configured.")
            return False

        subject = f"The Scentist order {order['order_number']} is {order['status']}"
        delivered = self._send(order["email"], subject, self._status_body(order))
        if self.settings.admin_email:
            delivered = self._send(
                self.settings.admin_email,
                f"Status updated: {order['order_number']} - {order['status']}",
                self._admin_body(order),
            ) and delivered
        return delivered

    def send_email_verification(self, customer: dict[str, Any], verification_url: str) -> bool:
        if not self.enabled:
            logger.info("Email verification delivery skipped because SMTP is not configured.")
            return False
        subject = "Verify your The Scentist account"
        body = "\n".join(
            [
                f"Hello {customer['full_name']},",
                "",
                "Verify your email to securely connect past and future orders to your account:",
                verification_url,
                "",
                "This link expires in 24 hours. If you did not create this account, ignore this email.",
            ]
        )
        return self._send(customer["email"], subject, body)

    def _send(self, recipient: str, subject: str, body: str) -> bool:
        try:
            self._send_message(recipient, subject, body)
            return True
        except Exception:
            logger.exception("Unable to send order notification to %s", recipient)
            return False

    def _send_message(self, recipient: str, subject: str, body: str) -> None:
        message = EmailMessage()
        message["From"] = self.settings.notification_from_email
        message["To"] = recipient
        message["Subject"] = subject
        if self.settings.support_email or self.settings.admin_email:
            message["Reply-To"] = self.settings.support_email or self.settings.admin_email
        message.set_content(body)

        if self.settings.smtp_use_tls and self.settings.smtp_port == 465:
            with smtplib.SMTP_SSL(self.settings.smtp_host, self.settings.smtp_port, timeout=12) as smtp:
                if self.settings.smtp_username or self.settings.smtp_password:
                    smtp.login(self.settings.smtp_username, self.settings.smtp_password)
                smtp.send_message(message)
            return

        with smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=12) as smtp:
            if self.settings.smtp_use_tls:
                smtp.starttls()
            if self.settings.smtp_username or self.settings.smtp_password:
                smtp.login(self.settings.smtp_username, self.settings.smtp_password)
            smtp.send_message(message)

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

    def _status_body(self, order: dict[str, Any]) -> str:
        lines = [
            f"Hello {order['customer_name']},",
            "",
            f"Your order {order['order_number']} is now: {order['status']}.",
            f"Total: INR {order['total_inr']:,}",
        ]
        if order.get("courier_name") or order.get("tracking_number"):
            lines.extend(
                [
                    "",
                    "Tracking:",
                    f"Courier: {order.get('courier_name') or 'To be updated'}",
                    f"Tracking number: {order.get('tracking_number') or 'To be updated'}",
                ]
            )
        if order.get("tracking_url"):
            lines.append(f"Tracking link: {order['tracking_url']}")
        lines.extend(["", "Thank you,", "The Scentist"])
        return "\n".join(lines)
