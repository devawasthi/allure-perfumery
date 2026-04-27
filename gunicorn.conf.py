import multiprocessing
import os


bind = f"0.0.0.0:{os.getenv('PORT', '8780')}"
workers = int(os.getenv("WEB_CONCURRENCY", max(2, multiprocessing.cpu_count() * 2 + 1)))
threads = int(os.getenv("WEB_THREADS", "4"))
worker_class = os.getenv("GUNICORN_WORKER_CLASS", "gthread")
timeout = int(os.getenv("WEB_TIMEOUT_SECONDS", "60"))
graceful_timeout = int(os.getenv("WEB_GRACEFUL_TIMEOUT_SECONDS", "30"))
keepalive = int(os.getenv("WEB_KEEPALIVE_SECONDS", "5"))
max_requests = int(os.getenv("WEB_MAX_REQUESTS", "1000"))
max_requests_jitter = int(os.getenv("WEB_MAX_REQUESTS_JITTER", "100"))
accesslog = "-"
errorlog = "-"
loglevel = os.getenv("LOG_LEVEL", "info")
capture_output = True
preload_app = False
