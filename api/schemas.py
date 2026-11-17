"""Pydantic request models for the machine API (/recommend, /feedback)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class GraduateProfile(BaseModel):
    profile_id: str = Field(min_length=1, max_length=120)
    skills: list[str] = Field(min_length=1, max_length=80)
    education: str = Field(default="", max_length=200)
    experience_years: float = Field(default=0, ge=0, le=60)
    interests: str = Field(default="", max_length=500)
    target_career_id: str | None = Field(default=None, max_length=10)
    top_n: int = Field(default=5, ge=1, le=9)

    @field_validator("skills")
    @classmethod
    def _strip(cls, v: list[str]) -> list[str]:
        out = [s.strip() for s in v if s and s.strip()]
        if not out:
            raise ValueError("at least one non-empty skill is required")
        return [s[:80] for s in out]


class FeedbackIn(BaseModel):
    profile_id: str = Field(min_length=1, max_length=120)
    career_id: str = Field(min_length=1, max_length=10)
    feedback_type: Literal["useful", "not_useful", "accepted", "rejected"]
    comment: str = Field(default="", max_length=1000)
