from __future__ import annotations

from datetime import datetime, timedelta

import dateutil.parser
from sqlalchemy.orm import Session

from app.models import Job
from app.settings import settings

FRESHNESS_BUCKETS: dict[str, int] = {
    "1h": 100,
    "24h": 80,
    "3d": 60,
    "7d": 40,
    "14d": 20,
    "30d+": 0,
}


def parse_posted_date(raw_date: str | None) -> datetime:
    if not raw_date:
        return datetime.now()
    raw = raw_date.lower()
    if any(k in raw for k in ("just posted", "today", "minute", "hour")):
        return datetime.now()
    if "day" in raw:
        days = int("".join(filter(str.isdigit, raw_date)) or "1")
        return datetime.now() - timedelta(days=days)
    if "week" in raw:
        weeks = int("".join(filter(str.isdigit, raw_date)) or "1")
        return datetime.now() - timedelta(weeks=weeks)
    try:
        return dateutil.parser.parse(raw_date)
    except Exception:
        return datetime.now() - timedelta(days=7)


def get_freshness_bucket(posted_at: datetime) -> str:
    delta = datetime.now() - posted_at
    if delta < timedelta(hours=1):
        return "1h"
    if delta < timedelta(days=1):
        return "24h"
    if delta < timedelta(days=3):
        return "3d"
    if delta < timedelta(days=7):
        return "7d"
    if delta < timedelta(days=14):
        return "14d"
    return "30d+"


def calculate_match_score(job: Job) -> float:
    freshness_score: int = FRESHNESS_BUCKETS.get(job.freshness_bucket or "", 0)
    skill_score = 0.0
    skills: list[str] = job.skills_required or []
    if skills:
        job_skills = {s.lower() for s in skills}
        my_skills = {s.lower() for s in settings.skills}
        matched = job_skills & my_skills
        skill_score = len(matched) / max(len(job_skills), 1) * 100
    if job.remote:
        skill_score = min(skill_score * 1.2, 100.0)
    return round(min(0.7 * skill_score + 0.3 * freshness_score, 100.0), 2)


def create_or_update_job(db: Session, payload: dict[str, object]) -> Job:
    posted_at = parse_posted_date(str(payload.get("posted_raw") or ""))
    freshness_bucket = get_freshness_bucket(posted_at)

    existing: Job | None = (
        db.query(Job)
        .filter(
            Job.platform == str(payload["platform"]),
            Job.job_id == str(payload["job_id"]),
        )
        .first()
    )

    if existing:
        for key, value in payload.items():
            if hasattr(existing, key):
                setattr(existing, key, value)
        existing.posted_at = posted_at
        existing.fetched_at = datetime.now()
        existing.freshness_bucket = freshness_bucket
        # If payload has match_score, it might be the AI score, so we preserve it
        if "match_score" not in payload:
            existing.match_score = calculate_match_score(existing)
        db.commit()
        db.refresh(existing)
        return existing

    job = Job(
        platform=str(payload.get("platform", "")),
        job_id=str(payload.get("job_id", "")),
        title=str(payload.get("title", "")),
        company=str(payload["company"]) if payload.get("company") else None,
        location=str(payload["location"]) if payload.get("location") else None,
        remote=bool(payload.get("remote", False)),
        url=str(payload.get("url", "")),
        posted_raw=str(payload["posted_raw"]) if payload.get("posted_raw") else None,
        posted_at=posted_at,
        fetched_at=datetime.now(),
        description=str(payload["description"]) if payload.get("description") else None,
        skills_required=list(payload["skills_required"]) if payload.get("skills_required") else None,  # type: ignore[arg-type]
        missing_skills=list(payload["missing_skills"]) if payload.get("missing_skills") else None,  # type: ignore[arg-type]
        readiness_assessment=str(payload["readiness_assessment"]) if payload.get("readiness_assessment") else None,
        resume_pdf_path=str(payload["resume_pdf_path"]) if payload.get("resume_pdf_path") else None,
        tailored_match_score=float(payload["tailored_match_score"]) if payload.get("tailored_match_score") is not None else None,
        vector_score=float(payload["vector_score"]) if payload.get("vector_score") is not None else None,
        freshness_bucket=freshness_bucket,
        status="new",
    )
    if "match_score" in payload:
        job.match_score = float(payload["match_score"]) # type: ignore
    
    db.add(job)
    db.commit()
    db.refresh(job)
    
    if "match_score" not in payload:
        job.match_score = calculate_match_score(job)
        db.commit()
    return job
