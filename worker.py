from __future__ import annotations

import logging
import os
import signal
import threading
import time
from pathlib import Path

from dotenv import load_dotenv

from perfumery_app.config import load_settings
from perfumery_app.database import Database
from perfumery_app.notifications import OrderNotifier


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
settings = load_settings()
configured_path = Path(settings.sqlite_database_path or "data/perfumery.sqlite3")
database_path = configured_path if configured_path.is_absolute() else BASE_DIR / configured_path
db = Database(database_path, settings)
notifier = OrderNotifier(settings)
logger = logging.getLogger("the_scentist.worker")
stop_event = threading.Event()


def stop_worker(_signum, _frame) -> None:
    stop_event.set()


def process_notification() -> bool:
    event = db.claim_notification()
    if event is None:
        return False
    order = event.get("order")
    try:
        if order is None:
            raise RuntimeError("The queued order no longer exists.")
        if event["event_type"] == "order_received":
            delivered = notifier.send_order_received(order)
        else:
            delivered = notifier.send_order_status_update(order)
        if not delivered:
            raise RuntimeError("SMTP delivery was not completed.")
        db.complete_notification(event["id"], event["order_number"], event["event_type"])
    except Exception as exc:
        logger.exception("Notification %s failed", event["id"])
        db.retry_notification(event["id"], int(event["attempts"]) + 1, str(exc))
    return True


def process_email_verification() -> bool:
    event = db.claim_email_verification()
    if event is None:
        return False
    try:
        delivered = notifier.send_email_verification(
            {"full_name": event["full_name"], "email": event["recipient"]},
            event["verification_url"],
        )
        if not delivered:
            raise RuntimeError("SMTP verification delivery was not completed.")
        db.complete_email_verification(event["id"])
    except Exception as exc:
        logger.exception("Verification email %s failed", event["id"])
        db.retry_email_verification(event["id"], int(event["attempts"]) + 1, str(exc))
    return True


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(asctime)s %(levelname)s %(message)s")
    signal.signal(signal.SIGTERM, stop_worker)
    signal.signal(signal.SIGINT, stop_worker)
    db.initialize()
    last_maintenance = 0.0
    logger.info("Notification worker started")
    try:
        while not stop_event.is_set():
            now = time.monotonic()
            if now - last_maintenance >= 30:
                db.expire_stale_reservations()
                last_maintenance = now
            processed = process_notification()
            processed = process_email_verification() or processed
            if not processed:
                stop_event.wait(2)
    finally:
        db.close()


if __name__ == "__main__":
    main()
