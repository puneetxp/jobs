from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean, DateTime, Float, Integer,
    JSON, String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy import create_engine

from app.settings import settings


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    job_id: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    company: Mapped[str | None] = mapped_column(String(200))
    location: Mapped[str | None] = mapped_column(String(200))
    remote: Mapped[bool] = mapped_column(Boolean, default=False)
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    posted_raw: Mapped[str | None] = mapped_column(String(100))
    posted_at: Mapped[datetime | None] = mapped_column(DateTime)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime, default=func.now())
    salary_min: Mapped[int | None] = mapped_column(Integer)
    salary_max: Mapped[int | None] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(Text)
    skills_required: Mapped[list[str] | None] = mapped_column(JSON)
    match_score: Mapped[float] = mapped_column(Float, default=0.0)
    missing_skills: Mapped[list[str] | None] = mapped_column(JSON)
    readiness_assessment: Mapped[str | None] = mapped_column(Text)
    resume_pdf_path: Mapped[str | None] = mapped_column(String(500))
    tailored_match_score: Mapped[float | None] = mapped_column(Float)
    vector_score: Mapped[float | None] = mapped_column(Float)
    freshness_bucket: Mapped[str | None] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20), default="new")
    screening_questions: Mapped[list[dict] | None] = mapped_column(JSON)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=func.now())

    __table_args__ = (UniqueConstraint("platform", "job_id"),)


class SearchProfile(Base):
    __tablename__ = "search_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    platform: Mapped[str | None] = mapped_column(String(20))
    keywords: Mapped[list[str] | None] = mapped_column(JSON)
    locations: Mapped[list[str] | None] = mapped_column(JSON)
    remote: Mapped[bool | None] = mapped_column(Boolean)
    min_salary: Mapped[int | None] = mapped_column(Integer)
    max_experience: Mapped[int | None] = mapped_column(Integer)
    date_window: Mapped[str | None] = mapped_column(String(10))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=func.now())


class AppDraft(Base):
    __tablename__ = "app_drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int | None] = mapped_column(Integer)
    resume_version: Mapped[str | None] = mapped_column(String(100))
    cover_letter: Mapped[str | None] = mapped_column(Text)
    screening_answers: Mapped[dict[str, str] | None] = mapped_column(JSON)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=func.now())


engine_args = {}
if settings.database_url.startswith("sqlite"):
    engine_args["connect_args"] = {"check_same_thread": False}

engine = create_engine(
    settings.database_url,
    **engine_args
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base.metadata.create_all(bind=engine)

# Auto-migrate: Add screening_questions column if it doesn't exist
from sqlalchemy import text
with engine.connect() as conn:
    try:
        conn.execute(text("ALTER TABLE jobs ADD COLUMN screening_questions JSON;"))
        conn.commit()
        print("Schema Migration: Added screening_questions column to jobs table.")
    except Exception:
        pass
