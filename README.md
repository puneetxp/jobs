# Job Assistant - Safe Job Application Dashboard

A semi-automated job assistant for Indeed, Naukri, and LinkedIn.

## Features
- Job search profile management
- Job ingestion with date posted normalization
- Skill-based matching + freshness scoring
- Review queue with tailored drafts
- Application tracking

## Quick Start

1. `cd job_assistant`
2. `pip install -r requirements.txt`
3. `cp .env.example .env` and fill values
4. `alembic upgrade head`
5. `uvicorn main:app --reload`
6. Open http://localhost:8000/admin

## Architecture

- FastAPI backend
- Postgres database
- Next.js dashboard (TBD)

## Date Posted Logic

Jobs are scored by freshness:
- 1h: +100 points
- 24h: +80
- 3d: +60
- 7d: +40
- 14d: +20
- 30d+: +0

Total score = 0.7 × skills_match + 0.3 × freshness