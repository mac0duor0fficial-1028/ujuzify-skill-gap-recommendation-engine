"""Job ingestion (Phase 2): fetch from a registered source, normalize, write raw JSONL.

Run from the project root:

    python src/ingestion/job_api.py                      # default: sample_fixture (offline)
    python src/ingestion/job_api.py --list-sources
    python src/ingestion/job_api.py --source job_opportunities_api --limit 200

Output (git-ignored): ``data/raw/jobs_<source_id>_<UTC timestamp>.jsonl`` plus a
sibling ``.manifest.json`` with counts and a sha256 of the data file.

Only sources that are registered, enabled and terms-reviewed can run. Standard
library only (urllib), so no extra dependencies.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:  # allow `python src/ingestion/job_api.py`
    sys.path.insert(0, str(ROOT))

from src.ingestion.raw_schema import SCHEMA_VERSION, RecordError, build_record  # noqa: E402
from src.ingestion.source_registry import (  # noqa: E402
    DEFAULT_REGISTRY, Source, SourceNotAllowed, assert_runnable, get_source, load_registry,
)

DEFAULT_OUT_DIR = ROOT / "data" / "raw"
USER_AGENT = "ujuzify-ingestion/0.2"
RETRY_STATUS = {429, 500, 502, 503, 504}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class FetchError(RuntimeError):
    """A source request failed after retries."""


def get_path(obj: object, dotted: str) -> object:
    """Read 'a.b.c' from nested dicts; None if any step is missing."""
    cur = obj
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


# ---------------------------------------------------------------- adapters
def iter_fixture(source: Source) -> Iterator[dict]:
    path = ROOT / source.config["path"]
    data = json.loads(path.read_text(encoding="utf-8"))
    yield from data["jobs"]


def _check_url(url: str) -> None:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" and not (parts.scheme == "http" and parts.hostname in LOCAL_HOSTS):
        raise FetchError(f"refusing non-HTTPS URL: {parts.scheme}://{parts.hostname}")


def _get_json(url: str, headers: dict, timeout: float, max_retries: int,
              sleep: Callable[[float], None]) -> object:
    attempt = 0
    while True:
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code not in RETRY_STATUS or attempt >= max_retries:
                raise FetchError(f"HTTP {e.code} from source after {attempt + 1} attempt(s)") from e
            retry_after = e.headers.get("Retry-After") if e.headers else None
            delay = float(retry_after) if retry_after and retry_after.isdigit() else 2.0 ** attempt
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            if attempt >= max_retries:
                raise FetchError(f"request failed after {attempt + 1} attempt(s): {type(e).__name__}") from e
            delay = 2.0 ** attempt
        attempt += 1
        sleep(delay)


def iter_http_json(source: Source, env: dict[str, str] | None = None,
                   sleep: Callable[[float], None] = time.sleep) -> Iterator[dict]:
    """Paginated JSON API. All endpoint details come from the registry, none from code."""
    env = os.environ if env is None else env
    cfg = source.config
    base = env[cfg["base_url_env"]].rstrip("/")
    key = env[cfg["api_key_env"]]
    pg = cfg.get("pagination", {})
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if cfg.get("auth_header"):
        scheme = cfg.get("auth_scheme", "")
        headers[cfg["auth_header"]] = f"{scheme} {key}".strip()
    timeout = float(cfg.get("timeout_s", 30))
    retries = int(cfg.get("max_retries", 3))
    min_interval = float(cfg.get("min_interval_s", 0))
    page = int(pg.get("start_page", 1))
    max_pages = int(pg.get("max_pages", 1))
    first = True
    for _ in range(max_pages):
        params = dict(cfg.get("params", {}))
        if pg.get("page_param"):
            params[pg["page_param"]] = page
        if pg.get("size_param"):
            params[pg["size_param"]] = pg.get("page_size", 50)
        url = f"{base}{cfg['path']}?{urllib.parse.urlencode(params)}"
        _check_url(url)
        if not first and min_interval:
            sleep(min_interval)
        first = False
        payload = _get_json(url, headers, timeout, retries, sleep)
        records = get_path(payload, cfg["records_path"]) if cfg.get("records_path") else payload
        if not isinstance(records, list):
            raise FetchError(f"expected a list at records_path '{cfg.get('records_path', '')}'")
        if not records:
            return
        yield from records
        page += 1


# ---------------------------------------------------------------- normalization
def normalize_native(native: dict, source: Source, retrieved_at: str) -> dict:
    """Map one native record through the source's field_map to the canonical schema."""
    fmap = source.config["field_map"]
    values = {canon: get_path(native, native_key) for canon, native_key in fmap.items()}
    return build_record(
        source_id=source.source_id, country=source.country, retrieved_at=retrieved_at,
        is_synthetic=source.is_synthetic, **values,
    )


@dataclass
class RunResult:
    data_path: Path
    manifest_path: Path
    manifest: dict


def run(source_id: str, *, registry_path: Path | str | None = None,
        out_dir: Path | str | None = None, limit: int | None = None,
        now: datetime | None = None, env: dict[str, str] | None = None,
        sleep: Callable[[float], None] = time.sleep) -> RunResult:
    registry = load_registry(registry_path)
    source = get_source(registry, source_id)
    assert_runnable(source, env)

    started = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    retrieved_at = started.strftime("%Y-%m-%dT%H:%M:%SZ")
    natives = iter_fixture(source) if source.type == "fixture" else iter_http_json(source, env, sleep)

    seen_ids: set[str] = set()
    seen_hashes: set[str] = set()
    records: list[dict] = []
    counts: Counter = Counter()
    skip_reasons: Counter = Counter()
    for native in natives:
        if limit is not None and counts["fetched"] >= limit:
            break
        counts["fetched"] += 1
        try:
            rec = normalize_native(native, source, retrieved_at)
        except RecordError as e:
            counts["skipped_invalid"] += 1
            skip_reasons[str(e)] += 1
            continue
        if rec["date_posted"] is None:
            counts["date_unparsed_or_missing"] += 1
        if rec["job_id"] in seen_ids:
            counts["duplicates_dropped_same_id"] += 1
            continue
        if rec["content_hash"] in seen_hashes:
            counts["duplicates_dropped_same_content"] += 1
            continue
        seen_ids.add(rec["job_id"])
        seen_hashes.add(rec["content_hash"])
        records.append(rec)
    counts["written"] = len(records)

    out = Path(out_dir) if out_dir else DEFAULT_OUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    data_path = out / f"jobs_{source_id}_{stamp}.jsonl"
    manifest_path = out / f"jobs_{source_id}_{stamp}.manifest.json"
    body = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in records)
    tmp = data_path.with_suffix(".jsonl.tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(data_path)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "source_id": source_id,
        "is_synthetic": source.is_synthetic,
        "retrieved_at": retrieved_at,
        "data_file": data_path.name,
        "data_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "counts": dict(counts),
        "skip_reasons": dict(skip_reasons),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return RunResult(data_path, manifest_path, manifest)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Ingest job postings from a registered source.")
    ap.add_argument("--source", default="sample_fixture", help="source_id from the registry")
    ap.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    ap.add_argument("--limit", type=int, default=None, help="max native records to fetch")
    ap.add_argument("--list-sources", action="store_true")
    args = ap.parse_args(argv)

    if args.list_sources:
        for s in load_registry(args.registry).values():
            state = "ready" if (s.enabled and s.terms_reviewed) else "blocked"
            print(f"{s.source_id:24s} {s.type:10s} {state:8s} synthetic={s.is_synthetic}")
        return 0
    try:
        res = run(args.source, registry_path=args.registry, out_dir=args.out_dir, limit=args.limit)
    except (SourceNotAllowed, FetchError, ValueError, OSError) as e:
        print(f"INGESTION FAILED: {e}")
        return 1
    m = res.manifest
    print("INGESTION COMPLETE")
    print(f"  source:  {m['source_id']}" + ("  (SYNTHETIC: not market evidence)" if m["is_synthetic"] else ""))
    print(f"  output:  {res.data_path}")
    for k, v in sorted(m["counts"].items()):
        print(f"    {k}: {v}")
    for reason, n in m["skip_reasons"].items():
        print(f"    skipped because '{reason}': {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
