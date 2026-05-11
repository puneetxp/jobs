from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, ConfigDict


# ── Job ───────────────────────────────────────────────────────────────────────
class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    platform: str
    job_id: str
    title: str
    company: str | None = None
    location: str | None = None
    remote: bool = False
    url: str
    posted_at: datetime | None = None
    match_score: float = 0.0
    missing_skills: list[str] | None = None
    readiness_assessment: str | None = None
    resume_pdf_path: str | None = None
    tailored_match_score: float | None = None
    vector_score: float | None = None
    freshness_bucket: str | None = None
    status: str = "new"
    skills_required: list[str] | None = None


class JobIngest(BaseModel):
    platform: str
    job_id: str
    title: str
    company: str | None = None
    location: str | None = None
    remote: bool = False
    url: str
    posted_raw: str | None = None
    description: str | None = None
    skills_required: list[str] | None = None
    missing_skills: list[str] | None = None
    readiness_assessment: str | None = None
    resume_pdf_path: str | None = None
    tailored_match_score: float | None = None
    vector_score: float | None = None
    match_score: float | None = None


class JobIngestOut(BaseModel):
    id: int
    match_score: float
    freshness: str | None = None


# ── Search ────────────────────────────────────────────────────────────────────
class SearchRunRequest(BaseModel):
    tags: list[str] | None = None
    platforms: list[str] | None = None


class SearchRunOut(BaseModel):
    searched: int
    stats: dict[str, int]
    ingested: int
    failed: int


class SearchStatusOut(BaseModel):
    running: bool
    paused: bool
    stopped: bool
    progress: str
    evaluated_count: int
    total_evaluated: int


class TagsOut(BaseModel):
    job_titles: list[str]
    skills: list[str]
    locations: list[str]


from typing import Any

# ── Draft ─────────────────────────────────────────────────────────────────────
class DraftRequest(BaseModel):
    questions: list[Any] | None = None

class DraftOut(BaseModel):
    resume_version: str
    cover_letter: str
    screening_answers: dict[str, str]

class NaukriAnswer(BaseModel):
    id: str
    text: str

class NaukriSubmitRequest(BaseModel):
    answers: list[NaukriAnswer]
    force: bool = False


# ── Pagination ────────────────────────────────────────────────────────────────
class PaginatedJobsOut(BaseModel):
    jobs: list[JobOut]
    total_count: int
    today_count: int
    week_count: int
    top_matches_count: int
    page: int
    page_size: int
    total_pages: int
