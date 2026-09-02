#!/usr/bin/env python3
from __future__ import annotations

import argparse
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.request import Request, urlopen


PATHS = ("/", "/catalog", "/api/fragrances?per_page=24", "/readyz")


def fetch(base_url: str, index: int) -> tuple[int, float]:
    path = PATHS[index % len(PATHS)]
    started = time.perf_counter()
    request = Request(f"{base_url.rstrip('/')}{path}", headers={"User-Agent": "the-scentist-load-smoke/1.0"})
    with urlopen(request, timeout=15) as response:
        response.read()
        status = response.status
    return status, (time.perf_counter() - started) * 1000


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a safe read-only storefront concurrency smoke test.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8780")
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--concurrency", type=int, default=20)
    args = parser.parse_args()

    request_count = max(1, min(args.requests, 10_000))
    concurrency = max(1, min(args.concurrency, 200))
    latencies: list[float] = []
    failures: list[int] = []
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(fetch, args.base_url, index) for index in range(request_count)]
        for future in as_completed(futures):
            try:
                status, latency = future.result()
                latencies.append(latency)
                if status >= 400:
                    failures.append(status)
            except Exception:
                failures.append(0)

    elapsed = time.perf_counter() - started
    ordered = sorted(latencies)
    p95_index = max(0, int(len(ordered) * 0.95) - 1)
    print(f"requests={request_count} concurrency={concurrency} failures={len(failures)}")
    print(f"throughput={request_count / elapsed:.1f} req/s elapsed={elapsed:.2f}s")
    if ordered:
        print(f"mean={statistics.mean(ordered):.1f}ms p95={ordered[p95_index]:.1f}ms max={max(ordered):.1f}ms")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
