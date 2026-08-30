"""
Nivara API load test (dependency-free).

Sends parallel GET requests to a running Nivara server and reports the
real measured throughput + latency percentiles. Pure stdlib (threading +
urllib) so it runs on any Python 3.11+ box — no Locust/k6 required.

Usage:
    # 1. Start the server separately:
    #    GROQ_API_KEY=... PYTHONPATH=. uvicorn backend.main:app --port 8000
    #
    # 2. Run this test:
    python3 scripts/load_test.py --base http://localhost:8000 \
        --duration 20 --concurrency 32 --warmup 3
"""

import argparse
import statistics
import threading
import time
import urllib.error
import urllib.request

CHECK_ENDPOINTS = (
    "/health",
    "/api/metrics",
    "/v1/jobs?page=1&page_size=10",
    "/",
)


def _fire(base: str, path: str) -> float:
    """Return latency in ms for one request (or raise on non-2xx)."""
    url = base.rstrip("/") + path
    start = time.monotonic()
    with urllib.request.urlopen(url, timeout=10) as resp:
        status = resp.status
    elapsed = (time.monotonic() - start) * 1000.0
    if status >= 400:
        raise RuntimeError(f"HTTP {status} -> {url}")
    return elapsed


def run_worker(
    base: str,
    duration: float,
    latencies: list,
    errors: list,
    endpoints: tuple,
    stop: threading.Event,
) -> None:
    while not stop.is_set():
        path = endpoints[id(threading.current_thread()) % len(endpoints)]
        try:
            latencies.append(_fire(base, path))
        except Exception as exc:  # noqa: BLE001 - count any failure
            errors.append(str(exc))


def pct(sorted_lat, p: float) -> float:
    if not sorted_lat:
        return 0.0
    idx = min(len(sorted_lat) - 1, int(len(sorted_lat) * p))
    return round(sorted_lat[idx], 1)


def main() -> None:
    ap = argparse.ArgumentParser(description="Nivara load test")
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--duration", type=int, default=15, help="measure window (s)")
    ap.add_argument("--concurrency", type=int, default=32)
    ap.add_argument("--warmup", type=int, default=2, help="quiet warmup (s)")
    args = ap.parse_args()

    # Warm up connection pools + caches
    stop = threading.Event()
    warm = threading.Event()
    for _ in range(args.concurrency):
        threading.Thread(
            target=run_worker,
            args=(args.base, args.warmup, [], [], CHECK_ENDPOINTS, warm),
            daemon=True,
        ).start()
    time.sleep(args.warmup)
    warm.set()

    latencies: list = []
    errors: list = []
    workers = [threading.Event() for _ in range(args.concurrency)]
    for e in workers:
        threading.Thread(
            target=run_worker,
            args=(args.base, args.duration, latencies, errors, CHECK_ENDPOINTS, e),
            daemon=True,
        ).start()

    time.sleep(args.duration)
    for e in workers:
        e.set()
    time.sleep(0.5)

    total = len(latencies)
    if total == 0:
        print("No requests completed — is the server running?")
        return

    sorted_lat = sorted(latencies)
    duration_s = args.duration

    print("=" * 52)
    print("Nivara load test results")
    print("=" * 52)
    print(f"endpoints      : {', '.join(CHECK_ENDPOINTS)}")
    print(f"concurrency    : {args.concurrency}")
    print(f"window         : {args.duration}s")
    print("-" * 52)
    print(f"total requests : {total}")
    print(f"requests/sec   : {total / duration_s:.1f}")
    print(f"errors         : {len(errors)}")
    print(f"error rate     : {len(errors) / total:.2%}")
    print(f"avg latency    : {statistics.fmean(sorted_lat):.1f} ms")
    print(f"p50            : {pct(sorted_lat, 0.50)} ms")
    print(f"p95            : {pct(sorted_lat, 0.95)} ms")
    print(f"p99            : {pct(sorted_lat, 0.99)} ms")
    print(f"max            : {sorted_lat[-1]:.1f} ms")


if __name__ == "__main__":
    main()