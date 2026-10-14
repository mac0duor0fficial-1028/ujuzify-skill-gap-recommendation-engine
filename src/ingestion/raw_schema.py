"""Canonical raw job-posting schema (Phase 2).

Every source adapter maps its native records onto this schema before anything is
written to ``data/raw/``. The schema is deliberately close to the ``job_postings``
entity in ``docs/data_model.md`` plus the provenance fields that document promises
(source, source job id, retrieval time, content hash).

"Raw" here means *no analytical transformation*: no skill extraction, no career
labelling, no deduplication across runs. Only structural normalization (HTML to
plain text, whitespace, ISO timestamps) so downstream phases see one shape.

Standard library only.
"""
from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass
from datetime import datetime, timezone

SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class FieldSpec:
    name: str
    required: bool
    description: str


FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("schema_version", True, "Schema version this record conforms to."),
    FieldSpec("job_id", True, "Internal id: '<source_id>:<source_job_id>'. Unique within a corpus."),
    FieldSpec("source_id", True, "Registry id of the source (data/external/source_registry.json)."),
    FieldSpec("source_job_id", True, "The source's own identifier for the posting."),
    FieldSpec("source_url", False, "Public URL of the posting, if the source provides one."),
    FieldSpec("title", True, "Job title as published."),
    FieldSpec("company", False, "Employer name as published."),
    FieldSpec("location", False, "Location text as published."),
    FieldSpec("country", True, "ISO 3166-1 alpha-2 code (default from the source registry)."),
    FieldSpec("description", True, "Posting body as plain text (HTML stripped)."),
    FieldSpec("employment_type", False, "e.g. full-time, contract, internship (free text)."),
    FieldSpec("experience_required", False, "Experience requirement as published (free text)."),
    FieldSpec("education_required", False, "Education requirement as published (free text)."),
    FieldSpec("date_posted", False, "UTC ISO 8601 (YYYY-MM-DDTHH:MM:SSZ), or null if unknown."),
    FieldSpec("retrieved_at", True, "UTC ISO 8601 time this record was fetched."),
    FieldSpec("content_hash", True, "sha256 over normalized title, company, location, description."),
    FieldSpec("is_synthetic", True, "True for fixtures. Synthetic data is never market evidence."),
)

FIELD_NAMES: tuple[str, ...] = tuple(f.name for f in FIELDS)
REQUIRED_FIELDS: tuple[str, ...] = tuple(f.name for f in FIELDS if f.required)
OPTIONAL_TEXT_FIELDS: tuple[str, ...] = tuple(
    f.name for f in FIELDS if not f.required and f.name != "date_posted"
)

# Fields a source's field_map is allowed to populate.
MAPPABLE_FIELDS: tuple[str, ...] = (
    "source_job_id", "title", "company", "location", "description", "employment_type",
    "experience_required", "education_required", "date_posted", "source_url",
)
MANDATORY_MAPPINGS: tuple[str, ...] = ("source_job_id", "title", "description")

ISO_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
COUNTRY_RE = re.compile(r"^[A-Z]{2}$")

_TAG_RE = re.compile(r"<[^>]+>")
_BLOCK_TAG_RE = re.compile(r"</?(p|div|br|li|ul|ol|h[1-6]|tr|table)\b[^>]*>", re.IGNORECASE)
_WS_RE = re.compile(r"[ \t\r\f\v\u00a0]+")


class RecordError(ValueError):
    """A native record cannot be mapped to the canonical schema."""


def strip_html(text: str) -> str:
    """HTML to plain text. Block-level tags become newlines; entities are decoded."""
    t = _BLOCK_TAG_RE.sub("\n", text)
    t = _TAG_RE.sub("", t)
    t = html.unescape(t)
    t = _WS_RE.sub(" ", t)
    lines = [ln.strip() for ln in t.split("\n")]
    return "\n".join(ln for ln in lines if ln).strip()


def clean_text(value: object) -> str | None:
    """Normalize an optional text value; empty becomes None."""
    if value is None:
        return None
    t = strip_html(str(value))
    t = re.sub(r"\s+", " ", t).strip()
    return t or None


def parse_datetime(value: object) -> str | None:
    """Parse common date inputs to 'YYYY-MM-DDTHH:MM:SSZ' (UTC). None if unparseable.

    Accepts ISO 8601 (with or without time / offset), 'DD/MM/YYYY' (the Kenyan
    convention, never MM/DD), 'DD Mon YYYY', and epoch seconds. Naive values are
    assumed to be UTC.
    """
    if value is None or value == "":
        return None
    dt: datetime | None = None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    else:
        s = str(value).strip()
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            for fmt in ("%d/%m/%Y", "%d/%m/%Y %H:%M", "%d %b %Y", "%d %B %Y"):
                try:
                    dt = datetime.strptime(s, fmt)
                    break
                except ValueError:
                    continue
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hash_part(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def compute_content_hash(title: str, company: str | None, location: str | None, description: str) -> str:
    """sha256 over normalized content fields; identical postings share a hash across ids."""
    payload = "\x1f".join(_hash_part(p) for p in (title, company, location, description))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_record(
    *,
    source_id: str,
    source_job_id: str,
    title: str,
    description: str,
    country: str,
    retrieved_at: str,
    is_synthetic: bool,
    company: object = None,
    location: object = None,
    employment_type: object = None,
    experience_required: object = None,
    education_required: object = None,
    date_posted: object = None,
    source_url: object = None,
) -> dict:
    """Assemble a canonical record. Raises RecordError if mandatory content is missing."""
    sjid = clean_text(source_job_id)
    t = clean_text(title)
    d = strip_html(str(description)) if description is not None else ""
    if not sjid:
        raise RecordError("missing source_job_id")
    if not t:
        raise RecordError("missing title")
    if not d:
        raise RecordError("missing description")
    comp, loc = clean_text(company), clean_text(location)
    return {
        "schema_version": SCHEMA_VERSION,
        "job_id": f"{source_id}:{sjid}",
        "source_id": source_id,
        "source_job_id": sjid,
        "source_url": clean_text(source_url),
        "title": t,
        "company": comp,
        "location": loc,
        "country": country,
        "description": d,
        "employment_type": clean_text(employment_type),
        "experience_required": clean_text(experience_required),
        "education_required": clean_text(education_required),
        "date_posted": parse_datetime(date_posted),
        "retrieved_at": retrieved_at,
        "content_hash": compute_content_hash(t, comp, loc, d),
        "is_synthetic": bool(is_synthetic),
    }
