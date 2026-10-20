"""API client and presentation helpers for the Streamlit app (Phase 6).

Kept outside the Streamlit script so the logic can be tested without a browser. Uses
``requests`` (a Streamlit dependency).
"""
from __future__ import annotations

import re

import requests

MAX_SKILLS = 50


class ApiError(Exception):
    """A readable failure from the Ujuzify API, safe to show to a user."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.message, self.status = message, status


def _detail(resp: requests.Response) -> str:
    try:
        detail = resp.json().get("detail", "")
    except (ValueError, AttributeError):
        return f"HTTP {resp.status_code}"
    if isinstance(detail, list):   # pydantic validation errors
        return "; ".join(f"{'.'.join(str(p) for p in d.get('loc', [])[1:])}: {d.get('msg', '')}" for d in detail)
    return str(detail) or f"HTTP {resp.status_code}"


class ApiClient:
    def __init__(self, base_url: str, timeout: float = 10.0, session: requests.Session | None = None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()

    def _call(self, method: str, path: str, **kw):
        try:
            resp = self.session.request(method, self.base_url + path, timeout=self.timeout, **kw)
        except requests.exceptions.RequestException as e:
            raise ApiError(f"Cannot reach the Ujuzify API at {self.base_url} ({type(e).__name__}). "
                           "Start it with: uvicorn api.main:app --host 127.0.0.1 --port 8000") from e
        if resp.status_code != 200:
            raise ApiError(_detail(resp), resp.status_code)
        return resp.json()

    def health(self) -> dict:
        return self._call("GET", "/health")

    def skills(self) -> list[dict]:
        return self._call("GET", "/skills")

    def careers(self) -> list[dict]:
        return self._call("GET", "/careers")

    def recommend(self, payload: dict) -> dict:
        return self._call("POST", "/recommend", json=payload)


def build_payload(selected: list[str], extra_text: str, target_career: str | None,
                  free_only: bool, top: int = 5, profile_id: str = "web-user") -> dict:
    """Combine picked skills and free-text extras into an API request; raise ValueError if unusable."""
    extras = [s.strip() for s in re.split(r"[,;\n]", extra_text or "") if s.strip()]
    skills, seen = [], set()
    for s in [*selected, *extras]:
        if s.lower() not in seen:
            seen.add(s.lower())
            skills.append(s)
    if not skills:
        raise ValueError("Pick or type at least one skill.")
    if len(skills) > MAX_SKILLS:
        raise ValueError(f"Please list at most {MAX_SKILLS} skills.")
    if any(len(s) > 80 for s in skills):
        raise ValueError("Each skill name must be 80 characters or fewer.")
    payload = {"profile_id": profile_id, "skills": skills, "top": top, "free_only": free_only}
    if target_career:
        payload["target_career"] = target_career
    return payload


def percent(score: float) -> str:
    return f"{score:.0%}"


_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|>~<])")


def md_escape(text: str) -> str:
    """Escape Markdown so user-typed text is shown literally."""
    return _MD_SPECIAL.sub(r"\\\1", text)
