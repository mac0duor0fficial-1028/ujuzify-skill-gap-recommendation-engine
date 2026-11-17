"""Tiny HTTP health probe used by Docker HEALTHCHECK: exits 0 when /health returns 200."""
import os
import sys
import urllib.request

url = sys.argv[1] if len(sys.argv) > 1 else f"http://127.0.0.1:{os.getenv('PORT', '8000')}/health"
try:
    with urllib.request.urlopen(url, timeout=4) as r:  # noqa: S310 - fixed local URL
        sys.exit(0 if r.status == 200 else 1)
except Exception as exc:  # noqa: BLE001
    print(f"unhealthy: {exc}", file=sys.stderr)
    sys.exit(1)
