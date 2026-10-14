"""Source registry (Phase 2).

Loads and validates ``data/external/source_registry.json``. A source may only be
ingested when it is registered, ``enabled`` and ``terms_reviewed``. This is how the
project's "APIs and permitted feeds only" principle is enforced in code.

Standard library only.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from src.ingestion.raw_schema import COUNTRY_RE, MANDATORY_MAPPINGS, MAPPABLE_FIELDS

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY = ROOT / "data" / "external" / "source_registry.json"

SOURCE_TYPES = ("fixture", "http_json")
SOURCE_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class SourceNotAllowed(RuntimeError):
    """The source exists but is not permitted (or not configured) to run."""


@dataclass(frozen=True)
class Source:
    source_id: str
    name: str
    type: str
    enabled: bool
    terms_reviewed: bool
    terms_url: str | None
    is_synthetic: bool
    redistribution_allowed: bool
    country: str
    config: dict = field(default_factory=dict)
    notes: str = ""


def validate_registry_data(data: dict) -> list[str]:
    """Return a list of problems; empty means the registry is valid."""
    errors: list[str] = []
    sources = data.get("sources")
    if not isinstance(sources, list) or not sources:
        return ["registry: 'sources' must be a non-empty list"]
    seen: set[str] = set()
    for i, s in enumerate(sources):
        sid = s.get("source_id", f"<index {i}>")
        if not isinstance(sid, str) or not SOURCE_ID_RE.match(sid):
            errors.append(f"{sid}: source_id must be lowercase snake_case")
        if sid in seen:
            errors.append(f"{sid}: duplicate source_id")
        seen.add(sid)
        for key in ("name", "type", "enabled", "terms_reviewed", "is_synthetic",
                    "redistribution_allowed", "country", "config"):
            if key not in s:
                errors.append(f"{sid}: missing '{key}'")
        if s.get("type") not in SOURCE_TYPES:
            errors.append(f"{sid}: type must be one of {SOURCE_TYPES}, got {s.get('type')!r}")
        if not COUNTRY_RE.match(str(s.get("country", ""))):
            errors.append(f"{sid}: country must be an ISO alpha-2 code like 'KE'")
        if s.get("enabled") and not s.get("terms_reviewed"):
            errors.append(f"{sid}: enabled=true requires terms_reviewed=true")
        if s.get("terms_reviewed") and not s.get("is_synthetic") and not s.get("terms_url"):
            errors.append(f"{sid}: a real source marked terms_reviewed needs a terms_url")

        cfg = s.get("config") or {}
        fmap = cfg.get("field_map")
        if not isinstance(fmap, dict):
            errors.append(f"{sid}: config.field_map is required")
        else:
            for k in MANDATORY_MAPPINGS:
                if k not in fmap:
                    errors.append(f"{sid}: field_map must map '{k}'")
            for k in fmap:
                if k not in MAPPABLE_FIELDS:
                    errors.append(f"{sid}: field_map key '{k}' is not a canonical field")
        if s.get("type") == "fixture" and not cfg.get("path"):
            errors.append(f"{sid}: fixture sources need config.path")
        if s.get("type") == "http_json":
            for k in ("base_url_env", "api_key_env", "path"):
                if not cfg.get(k):
                    errors.append(f"{sid}: http_json sources need config.{k}")
    return errors


def load_registry(path: Path | str | None = None) -> dict[str, Source]:
    """Load the registry, raising ValueError if it is invalid."""
    p = Path(path) if path else DEFAULT_REGISTRY
    data = json.loads(p.read_text(encoding="utf-8"))
    errors = validate_registry_data(data)
    if errors:
        raise ValueError("Invalid source registry:\n  - " + "\n  - ".join(errors))
    out: dict[str, Source] = {}
    for s in data["sources"]:
        out[s["source_id"]] = Source(
            source_id=s["source_id"], name=s["name"], type=s["type"],
            enabled=bool(s["enabled"]), terms_reviewed=bool(s["terms_reviewed"]),
            terms_url=s.get("terms_url"), is_synthetic=bool(s["is_synthetic"]),
            redistribution_allowed=bool(s["redistribution_allowed"]),
            country=s["country"], config=s["config"], notes=s.get("notes", ""),
        )
    return out


def get_source(registry: dict[str, Source], source_id: str) -> Source:
    if source_id not in registry:
        raise SourceNotAllowed(f"'{source_id}' is not in the source registry")
    return registry[source_id]


def assert_runnable(source: Source, env: dict[str, str] | None = None) -> None:
    """Raise SourceNotAllowed unless the source may run right now."""
    env = os.environ if env is None else env
    if not source.enabled:
        raise SourceNotAllowed(f"'{source.source_id}' is disabled in the registry")
    if not source.terms_reviewed:
        raise SourceNotAllowed(f"'{source.source_id}' has not had its terms reviewed")
    if source.type == "http_json":
        for key in ("base_url_env", "api_key_env"):
            var = source.config[key]
            if not env.get(var):
                raise SourceNotAllowed(f"'{source.source_id}' needs environment variable {var}")
