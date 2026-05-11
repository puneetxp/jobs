import concurrent.futures
import hashlib
import json
import os
import re
import time
from datetime import datetime
from typing import Dict, List, Optional

import feedparser
import requests
from PyPDF2 import PdfReader

from app.settings import settings
from modules.browser import force_quit_driver
from modules.evaluation import evaluate_job
from modules.scrapers.linkedin import scrape_linkedin
from modules.scrapers.naukri import scrape_naukri
from modules.vector_store import (
    bulk_score_against_profile,
    clear_collection,
    upsert_job,
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

import threading

class SearchControl:
    def __init__(self):
        self.paused = False
        self.stopped = False
        self.running = False
        self.progress = "Idle"
        self.evaluated_count = 0
        self.total_evaluated = 0
        self.lock = threading.Lock()

    def check_state(self):
        with self.lock:
            if self.stopped:
                raise Exception("Search stopped by user")
            return self.paused

    def wait_if_paused(self):
        while True:
            with self.lock:
                if self.stopped:
                    raise Exception("Search stopped by user")
                if not self.paused:
                    break
            time.sleep(1)

search_control = SearchControl()


# ── helpers ──────────────────────────────────────────────────────────────────
def _s(value: object, default: str = "") -> str:
    """Safely coerce a feedparser attribute value to str."""
    if value is None:
        return default
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(str(v) for v in value)
    return str(value)


# ── INDEED (public RSS, no login) ─────────────────────────────────────────────
def search_indeed(query: str, location: str = "Remote", max_results: int = 25) -> List[Dict]:
    url = "https://www.indeed.com/rss"
    params = {"q": query, "l": location, "sort": "date", "limit": max_results}
    jobs = []
    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=15)
        feed = feedparser.parse(resp.text)
        for entry in feed.entries[:max_results]:
            title_full = _s(entry.get("title", ""))
            parts = title_full.split(" - ")
            title = parts[0].strip()
            company = parts[1].strip() if len(parts) >= 2 else ""
            summary = _s(entry.get("summary", ""))
            job_id = _s(entry.get("id") or getattr(entry, "link", ""))[-80:]
            jobs.append({
                "platform": "indeed",
                "job_id": job_id,
                "title": title,
                "company": company,
                "location": location,
                "remote": "remote" in (title + summary).lower(),
                "url": _s(entry.get("link", "#")),
                "posted_raw": _s(entry.get("published", "")),
                "description": re.sub(r"<[^>]+>", " ", summary).strip(),
                "skills_required": _extract_skills(summary + title),
            })
    except Exception as e:
        print(f"[Indeed] Error: {e}")
    return jobs


# ── LINKEDIN (Stealth Selenium) ────────────────────────────────────────────────
def search_linkedin(query: str, location: str = "India", max_results: int = 25) -> List[Dict]:
    jobs = []
    try:
        def _progress(msg):
            with search_control.lock:
                search_control.progress = f"🔍 LinkedIn: {msg}"

        scraped, driver = scrape_linkedin(
            job_title=query,
            location=location,
            max_jobs=max_results,
            headless=settings.headless,
            past_24_hours=settings.past_24_hours,
            progress_callback=_progress
        )
        force_quit_driver(driver)
        
        for job in scraped:
            jobs.append({
                "platform": "linkedin",
                "job_id": (
                    (m := re.search(r"(?:view/|jobs/view/.*?|currentJobId=)(-?\d+)", job["job_link"])) and m.group(1)
                    or (m2 := re.search(r"\b\d{8,15}\b", job["job_link"])) and m2.group(0)
                    or job["job_link"][-50:]
                ),
                "title": job["job_title"],
                "company": job["company"],
                "location": job["location"],
                "remote": "remote" in (job["job_title"] + job["location"]).lower(),
                "url": job["job_link"],
                "posted_raw": "Last 24h" if settings.past_24_hours else "",
                "description": job["job_description"],
                "skills_required": _extract_skills(job["job_description"] + job["job_title"]),
            })
    except Exception as e:
        print(f"[LinkedIn] Error: {e}")
    return jobs


# ── NAUKRI (Stealth Selenium) ──────────────────────────────────────────────────
def search_naukri(query: str, location: str = "india", max_results: int = 25) -> List[Dict]:
    jobs = []
    try:
        def _progress(msg):
            with search_control.lock:
                search_control.progress = f"🇮🇳 Naukri: {msg}"

        scraped, driver = scrape_naukri(
            job_title=query,
            location=location,
            max_jobs=max_results,
            headless=settings.headless,
            past_24_hours=settings.past_24_hours,
            progress_callback=_progress
        )
        force_quit_driver(driver)
        
        for job in scraped:
            jobs.append({
                "platform": "naukri",
                "job_id": job["job_link"].split("/")[-1] or job["job_link"][-50:],
                "title": job["job_title"],
                "company": job["company"],
                "location": job["location"],
                "remote": "remote" in (job["job_title"] + job["location"]).lower(),
                "url": job["job_link"],
                "posted_raw": "Last 24h" if settings.past_24_hours else "",
                "description": job["job_description"],
                "skills_required": _extract_skills(job["job_description"] + job["job_title"]),
            })
    except Exception as e:
        print(f"[Naukri] Error: {e}")
    return jobs


# ── HELPERS ───────────────────────────────────────────────────────────────────
def _extract_skills(text: str) -> List[str]:
    text_lower = text.lower()
    return [skill for skill in settings.skills if skill.lower() in text_lower]

def _load_resume_text() -> str:
    path = settings.base_resume_path
    if not os.path.exists(path):
        return ""
    try:
        if path.endswith(".pdf"):
            reader = PdfReader(path)
            return "\n".join(p.extract_text() for p in reader.pages)
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        print(f"Error loading resume: {e}")
        return ""

def _make_job_id(platform: str, job_link: str) -> str:
    raw = f"{platform}_{job_link}"
    return hashlib.md5(raw.encode()).hexdigest()

# ── MAIN RUNNER ───────────────────────────────────────────────────────────────
def _search_single(platform: str, tag: str, loc: str) -> List[Dict]:
    try:
        search_control.wait_if_paused()
        msg = f"🔍 Searching [{tag}] on {platform.upper()} in {loc}..."
        with search_control.lock:
            search_control.progress = msg
        print(f"  [Parallel] Searching [{tag}] @ {loc} on {platform}...")
        if platform == "indeed":
            return search_indeed(tag, loc, max_results=settings.max_jobs)
        elif platform == "linkedin":
            return search_linkedin(tag, loc, max_results=settings.max_jobs)
        elif platform == "naukri":
            return search_naukri(tag, loc, max_results=settings.max_jobs)
    except Exception as e:
        print(f"Error searching {platform} for {tag} @ {loc}: {e}")
    return []


def run_search(tags: Optional[List[str]] = None, platforms: Optional[List[str]] = None) -> Dict:
    # Reset search control flags at startup
    with search_control.lock:
        search_control.paused = False
        search_control.stopped = False

    tags = tags or settings.job_titles
    platforms = platforms or ["indeed", "linkedin", "naukri"]
    locations = settings.locations

    all_jobs = []
    stats = {p: 0 for p in platforms}

    # Generate combination tasks
    tasks = []
    for tag in tags:
        for loc in locations:
            for platform in platforms:
                tasks.append((platform, tag, loc))

    # Since we use a persistent profile (.playwright_data) to save login sessions,
    # we must use a single worker (concurrency: 1) to avoid SingletonLock conflicts.
    max_search_workers = 1
    print(f"Starting parallel job search (concurrency: {max_search_workers})...")

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_search_workers) as executor:
        futures = {
            executor.submit(_search_single, p, t, l): p
            for p, t, l in tasks
        }
        for future in concurrent.futures.as_completed(futures):
            # Check stopped state before gathering next result
            with search_control.lock:
                if search_control.stopped:
                    print("Search cancelled. Halting executor master loop.")
                    break
            p = futures[future]
            try:
                r = future.result()
                all_jobs.extend(r)
                stats[p] += len(r)
            except Exception as e:
                print(f"Search task failed for {p}: {e}")

    # Pre-load all successfully evaluated job IDs from database to skip reprocessing them entirely
    from app.models import SessionLocal, Job as DBJob
    db = SessionLocal()
    db_successful_ids = set()
    try:
        successful_db_jobs = db.query(DBJob).filter(DBJob.match_score > 0).all()
        for db_job in successful_db_jobs:
            assessment = (db_job.readiness_assessment or "").strip().lower()
            is_failed = (
                not assessment 
                or "429" in assessment 
                or "quota" in assessment 
                or "failed" in assessment 
                or "error" in assessment 
                or "limit" in assessment
                or "no assessment available" in assessment
            )
            if not is_failed:
                db_successful_ids.add(f"{db_job.platform}:{db_job.job_id}")
    except Exception as e:
        print(f"Error pre-loading database successful job IDs: {e}")
    finally:
        db.close()

    # De-duplicate by both ID and Title/Company combination
    seen_ids = set()
    seen_roles = set()
    unique = []
    for job in all_jobs:
        id_key = f"{job['platform']}:{job['job_id']}"
        
        # If this job is already successfully evaluated in our database, IGNORE IT COMPLETELY!
        if id_key in db_successful_ids:
            continue
            
        title_clean = job.get('title', '').strip().lower()
        company_clean = job.get('company', '').strip().lower()
        role_key = f"{title_clean}::{company_clean}"
        
        # Keep job if neither the ID nor the exact Title+Company combo has been seen
        if id_key not in seen_ids and role_key not in seen_roles:
            seen_ids.add(id_key)
            if company_clean:  # Only block duplicates if company name is actually known
                seen_roles.add(role_key)
            unique.append(job)

    # Load any previously failed jobs from the database to retry them
    from app.models import SessionLocal, Job as DBJob
    db = SessionLocal()
    try:
        db_failed_jobs = db.query(DBJob).all()
        added_retries = 0
        for db_job in db_failed_jobs:
            assessment = (db_job.readiness_assessment or "").strip().lower()
            is_failed = (
                not assessment 
                or "429" in assessment 
                or "quota" in assessment 
                or "failed" in assessment 
                or "error" in assessment 
                or "limit" in assessment
                or "no assessment available" in assessment
            )
            if is_failed:
                # Check if it's already in unique
                already_in_unique = any(
                    j["platform"] == db_job.platform and j["job_id"] == db_job.job_id
                    for j in unique
                )
                if not already_in_unique:
                    unique.append({
                        "platform": db_job.platform,
                        "job_id": db_job.job_id,
                        "title": db_job.title,
                        "company": db_job.company,
                        "location": db_job.location,
                        "remote": db_job.remote,
                        "url": db_job.url,
                        "posted_raw": db_job.posted_raw,
                        "description": db_job.description or "",
                        "skills_required": db_job.skills_required or []
                    })
                    added_retries += 1
        if added_retries > 0:
            print(f"Added {added_retries} previously failed jobs from the database to retry evaluation.")
    except Exception as e:
        print(f"Error loading failed jobs for retry: {e}")
    finally:
        db.close()

    # AI Evaluation & Vector Scoring
    msg = f"Evaluating {len(unique)} jobs with AI (Parallel)..."
    with search_control.lock:
        search_control.progress = msg
        search_control.total_evaluated = len(unique)
        search_control.evaluated_count = 0
    print(msg)
    resume_text = _load_resume_text()
    
    # Vector store setup
    try:
        clear_collection()
        for job in unique:
            upsert_job(
                job_id=_make_job_id(job["platform"], job["url"]),
                description=job["description"],
                metadata={"title": job["title"], "company": job["company"]}
            )
        
        v_scores = {}
        if settings.profile_text:
            v_scores = bulk_score_against_profile(settings.profile_text)
    except Exception as e:
        print(f"Vector store error: {e}")
        v_scores = {}

    # Query database first to identify cached vs uncached jobs
    from app.models import SessionLocal, Job as DBJob
    db = SessionLocal()
    to_evaluate = []
    try:
        for job in unique:
            v_score = v_scores.get(_make_job_id(job["platform"], job["url"]), 0.0)
            job["vector_score"] = v_score

            db_job = db.query(DBJob).filter(DBJob.platform == job["platform"], DBJob.job_id == job["job_id"]).first()
            if db_job and db_job.match_score > 0:
                assessment = (db_job.readiness_assessment or "").strip().lower()
                is_failed = (
                    not assessment 
                    or "429" in assessment 
                    or "quota" in assessment 
                    or "failed" in assessment 
                    or "error" in assessment 
                    or "limit" in assessment
                    or "no assessment available" in assessment
                )
                if not is_failed:
                    print(f"Skipping AI evaluation; preserving successful cached score for: {job['title']}")
                    job["match_score"] = db_job.match_score
                    job["missing_skills"] = db_job.missing_skills or []
                    job["readiness_assessment"] = db_job.readiness_assessment
                    
                    with search_control.lock:
                        search_control.evaluated_count += 1
                        search_control.progress = f"🤖 AI evaluating: {search_control.evaluated_count} of {search_control.total_evaluated}..."
                    continue
            
            to_evaluate.append(job)
    except Exception as e:
        print(f"Database lookup error in cache step: {e}")
    finally:
        db.close()

    # Import batch evaluation tools
    from modules.evaluation import evaluate_jobs_batch, evaluate_job

    # Process remaining jobs in batches of 3
    batch_size = 3
    for i in range(0, len(to_evaluate), batch_size):
        chunk = to_evaluate[i:i + batch_size]
        
        # Throttling delay between batch requests to stay 100% under 15 RPM limits
        time.sleep(4.5)
        
        try:
            jds = [job["description"] or "" for job in chunk]
            print(f"Evaluating batch of {len(chunk)} jobs with {settings.llm_provider.capitalize()}...")
            
            results = evaluate_jobs_batch(jds, resume_text, provider=settings.llm_provider)
            
            for job, res in zip(chunk, results):
                job["match_score"] = res["match_score"]
                job["missing_skills"] = res["missing_skills"]
                job["readiness_assessment"] = res["readiness_assessment"]
                
                v_id = _make_job_id(job["platform"], job["url"])
                v_score = v_scores.get(v_id, 0.0)
                job["vector_score"] = v_score
                if v_score > 0:
                    job["match_score"] = round(0.7 * job["match_score"] + 0.3 * v_score)
                    
                with search_control.lock:
                    search_control.evaluated_count += 1
                    search_control.progress = f"🤖 AI evaluating: {search_control.evaluated_count} of {search_control.total_evaluated}..."
                    
        except Exception as batch_err:
            print(f"Batch evaluation failed ({batch_err}), falling back to sequential execution...")
            # Fallback to sequential single evaluations for this chunk
            for job in chunk:
                v_id = _make_job_id(job["platform"], job["url"])
                v_score = v_scores.get(v_id, 0.0)
                job["vector_score"] = v_score
                
                try:
                    time.sleep(4.5)
                    res = evaluate_job(job["description"] or "", resume_text, provider=settings.llm_provider)
                    job["match_score"] = res["match_score"]
                    job["missing_skills"] = res["missing_skills"]
                    job["readiness_assessment"] = res["readiness_assessment"]
                    if v_score > 0:
                        job["match_score"] = round(0.7 * job["match_score"] + 0.3 * v_score)
                except Exception as single_err:
                    print(f"Fallback eval error for {job['title']}: {single_err}")
                    job["match_score"] = 0
                    job["missing_skills"] = []
                    job["readiness_assessment"] = str(single_err)
                finally:
                    with search_control.lock:
                        search_control.evaluated_count += 1
                        search_control.progress = f"🤖 AI evaluating: {search_control.evaluated_count} of {search_control.total_evaluated}..."

    print(f"Found {len(unique)} unique jobs — {stats}")
    return {"jobs": unique, "stats": stats, "total": len(unique)}


def ingest_to_db(jobs: List[Dict]) -> Dict:
    base = "http://localhost:8000"
    success = failed = 0
    for job in jobs:
        try:
            r = requests.post(f"{base}/api/jobs/ingest", json=job, timeout=5)
            if r.status_code == 200:
                success += 1
            else:
                failed += 1
        except Exception:
            failed += 1
    return {"ingested": success, "failed": failed}


def retry_failed_evaluations_pipeline():
    global search_control
    with search_control.lock:
        # State already initialized synchronously by the endpoint to prevent race conditions
        search_control.running = True
        search_control.stopped = False
        search_control.paused = False

    try:
        from app.models import SessionLocal, Job as DBJob
        db = SessionLocal()
        try:
            db_failed_jobs = db.query(DBJob).all()
            failed_jobs = []
            for db_job in db_failed_jobs:
                assessment = (db_job.readiness_assessment or "").strip().lower()
                is_failed = (
                    not assessment 
                    or "429" in assessment 
                    or "quota" in assessment 
                    or "failed" in assessment 
                    or "error" in assessment 
                    or "limit" in assessment
                    or "no assessment available" in assessment
                )
                if is_failed:
                    failed_jobs.append({
                        "id": db_job.id,
                        "platform": db_job.platform,
                        "job_id": db_job.job_id,
                        "title": db_job.title,
                        "company": db_job.company,
                        "location": db_job.location,
                        "remote": db_job.remote,
                        "url": db_job.url,
                        "posted_raw": db_job.posted_raw,
                        "description": db_job.description or "",
                        "skills_required": db_job.skills_required or []
                    })
        except Exception as db_err:
            print(f"Error reading failed jobs: {db_err}")
            failed_jobs = []
        finally:
            db.close()

        if not failed_jobs:
            with search_control.lock:
                search_control.progress = "Finished: No failed evaluations found in database."
                search_control.running = False
            return

        with search_control.lock:
            search_control.total_evaluated = len(failed_jobs)
            search_control.progress = f"🤖 AI retrying failed evaluations: {search_control.evaluated_count} of {search_control.total_evaluated}..."

        # Run vector store scoring
        resume_text = _load_resume_text()
        v_scores = {}
        try:
            if settings.profile_text:
                v_scores = bulk_score_against_profile(settings.profile_text)
        except Exception as ve:
            print(f"Vector score error in retry failed: {ve}")

        # Import batch evaluation tools
        from modules.evaluation import evaluate_jobs_batch, evaluate_job

        # Process in batches of 3
        batch_size = 3
        for i in range(0, len(failed_jobs), batch_size):
            # ⏸️ Real-time Pause & Stop Handler
            while True:
                with search_control.lock:
                    if search_control.stopped:
                        print("Stop requested. Cancelling evaluation retry loop.")
                        break
                    if not search_control.paused:
                        break
                time.sleep(1)
                
            with search_control.lock:
                if search_control.stopped:
                    break

            chunk = failed_jobs[i:i + batch_size]
            
            # Throttling delay between batch requests to stay 100% safe from rate limits
            time.sleep(4.5)
            
            try:
                jds = [job_dict["description"] or "" for job_dict in chunk]
                print(f"Retrying batch of {len(chunk)} failed jobs with {settings.llm_provider.capitalize()}...")
                
                results = evaluate_jobs_batch(jds, resume_text, provider=settings.llm_provider)
                
                db_inst = SessionLocal()
                try:
                    for job_dict, res in zip(chunk, results):
                        job_dict["match_score"] = res["match_score"]
                        job_dict["missing_skills"] = res["missing_skills"]
                        job_dict["readiness_assessment"] = res["readiness_assessment"]
                        
                        v_id = _make_job_id(job_dict["platform"], job_dict["url"])
                        v_score = v_scores.get(v_id, 0.0)
                        job_dict["vector_score"] = v_score
                        if v_score > 0:
                            job_dict["match_score"] = round(0.7 * job_dict["match_score"] + 0.3 * v_score)
                        
                        db_job = db_inst.query(DBJob).get(job_dict["id"])
                        if db_job:
                            db_job.match_score = job_dict["match_score"]
                            db_job.missing_skills = job_dict["missing_skills"]
                            db_job.readiness_assessment = job_dict["readiness_assessment"]
                        
                        with search_control.lock:
                            search_control.evaluated_count += 1
                            search_control.progress = f"🤖 AI retrying failed evaluations: {search_control.evaluated_count} of {search_control.total_evaluated}..."
                    db_inst.commit()
                except Exception as db_err:
                    print(f"Error saving batch results to DB: {db_err}")
                    db_inst.rollback()
                finally:
                    db_inst.close()
                    
            except Exception as batch_err:
                print(f"Retry batch failed ({batch_err}), falling back to sequential retry...")
                # Fallback to sequential retry for this chunk
                for job_dict in chunk:
                    # Check stopped again inside sequential retry loop
                    with search_control.lock:
                        if search_control.stopped:
                            break
                            
                    v_id = _make_job_id(job_dict["platform"], job_dict["url"])
                    v_score = v_scores.get(v_id, 0.0)
                    job_dict["vector_score"] = v_score
                    
                    try:
                        time.sleep(4.5)
                        res = evaluate_job(job_dict["description"] or "", resume_text, provider=settings.llm_provider)
                        job_dict["match_score"] = res["match_score"]
                        job_dict["missing_skills"] = res["missing_skills"]
                        job_dict["readiness_assessment"] = res["readiness_assessment"]
                        if v_score > 0:
                            job_dict["match_score"] = round(0.7 * job_dict["match_score"] + 0.3 * v_score)
                    except Exception as single_err:
                        print(f"Fallback retry failed for {job_dict['title']}: {single_err}")
                        job_dict["match_score"] = 0
                        job_dict["missing_skills"] = []
                        job_dict["readiness_assessment"] = str(single_err)
                        
                    db_inst = SessionLocal()
                    try:
                        db_job = db_inst.query(DBJob).get(job_dict["id"])
                        if db_job:
                            db_job.match_score = job_dict["match_score"]
                            db_job.missing_skills = job_dict["missing_skills"]
                            db_job.readiness_assessment = job_dict["readiness_assessment"]
                            db_inst.commit()
                    except Exception as db_single_err:
                        print(f"Error saving fallback job: {db_single_err}")
                        db_inst.rollback()
                    finally:
                        db_inst.close()
                        
                    with search_control.lock:
                        search_control.evaluated_count += 1
                        search_control.progress = f"🤖 AI retrying failed evaluations: {search_control.evaluated_count} of {search_control.total_evaluated}..."

        with search_control.lock:
            if search_control.stopped:
                search_control.progress = f"Stopped: Evaluation retry cancelled by user. Completed {search_control.evaluated_count} of {search_control.total_evaluated}."
            else:
                search_control.progress = f"Finished: Completed evaluation retry of {len(failed_jobs)} jobs."
            search_control.running = False

    except Exception as pipeline_err:
        print(f"Error in retry failed pipeline: {pipeline_err}")
        with search_control.lock:
            search_control.progress = f"Error: failed during retry: {pipeline_err}"
            search_control.running = False


def check_jobs_availability():
    """
    Checks all 'new' jobs in the database to see if they are still active.
    Updates status to 'expired' if they are no longer available.
    """
    from app.models import SessionLocal, Job as DBJob
    db = SessionLocal()
    try:
        jobs = db.query(DBJob).filter(DBJob.status == "new").all()
        total = len(jobs)
        print(f"Checking availability for {total} jobs...")
        
        with search_control.lock:
            search_control.running = True
            search_control.total_evaluated = total
            search_control.evaluated_count = 0
            search_control.progress = f"Checking availability of {total} jobs..."

        for i, job in enumerate(jobs):
            # Check stopped state
            with search_control.lock:
                if search_control.stopped:
                    break
            
            is_active = True
            try:
                # Use requests for speed, but with browser-like headers
                headers = {
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                }
                resp = requests.get(job.url, headers=headers, timeout=10, allow_redirects=True)
                
                if resp.status_code == 404:
                    is_active = False
                else:
                    text = resp.text.lower()
                    if job.platform == "linkedin":
                        if "no longer accepting applications" in text or "job no longer available" in text:
                            is_active = False
                    elif job.platform == "naukri":
                        if "this job has expired" in text or "job you are looking for is no longer available" in text:
                            is_active = False
                    elif job.platform == "indeed":
                        if "job no longer available" in text or "this job has expired" in text:
                            is_active = False
            except Exception as e:
                print(f"Error checking {job.url}: {e}")
                # On timeout or other errors, we assume it's still active to be safe
                pass

            if not is_active:
                print(f"  [Expired] {job.title} @ {job.company}")
                job.status = "expired"
            
            with search_control.lock:
                search_control.evaluated_count += 1
                search_control.progress = f"Checking availability: {search_control.evaluated_count} of {total}..."
            
            # Commit every 5 jobs to save progress
            if i % 5 == 0:
                db.commit()

        db.commit()
        with search_control.lock:
            search_control.progress = f"Finished checking availability. Marked {len([j for j in jobs if j.status == 'expired'])} jobs as expired."
            search_control.running = False
    except Exception as e:
        print(f"Error in availability check: {e}")
        db.rollback()
        with search_control.lock:
            search_control.progress = f"Error during availability check: {e}"
            search_control.running = False
    finally:
        db.close()



# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Job Finder — search by your tags")
    parser.add_argument("--tags", nargs="+", help="Override tags")
    parser.add_argument("--platforms", nargs="+", default=["indeed", "linkedin", "naukri"])
    parser.add_argument("--ingest", action="store_true", help="Ingest results into dashboard DB")
    parser.add_argument("--dry-run", action="store_true", help="Preview without ingesting")
    args = parser.parse_args()

    print(f"Job Finder — Puneet Sharma")
    print(f"Tags: {args.tags or settings.job_titles}")
    print(f"Platforms: {args.platforms}")
    print(f"Locations: {settings.locations}\n")

    result = run_search(tags=args.tags, platforms=args.platforms)

    if args.dry_run:
        for job in result["jobs"][:10]:
            print(f"[{job['platform'].upper()}] {job['title']} @ {job['company']} | {job['location']} | Skills: {job['skills_required']}")
    elif args.ingest:
        print("Ingesting into dashboard...")
        res = ingest_to_db(result["jobs"])
        print(f"Done — Ingested: {res['ingested']}  Failed: {res['failed']}")
        print("Open http://localhost:8000 to see jobs!")
    else:
        out = f"jobs_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
        with open(out, "w") as f:
            json.dump(result["jobs"], f, indent=2, default=str)
        print(f"Saved to {out} — run with --ingest to push to dashboard")
