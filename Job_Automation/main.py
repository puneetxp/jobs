"""
Stealth Job Pipeline — Entry Point
====================================
Single command runs the full pipeline automatically:

  python main.py --platform linkedin --title "Data Scientist" --location "Bangalore" --max-jobs 5 --no-headless --resume resume.pdf

Flow:  Scrape → Save to Excel → Close browser → Gemini evaluation → Tailored PDFs → Update Excel
"""

from __future__ import annotations

import argparse
import os
import yaml

def load_config(config_path="config.yaml"):
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}

# ---------------------------------------------------------------------------
# Hardcoded resume path — place your resume PDF here
# ---------------------------------------------------------------------------

DEFAULT_RESUME_PATH = os.path.join(os.path.dirname(__file__), "resume.pdf")

# Removed static DEFAULT_EXCEL to enforce dynamic timestamped directories

# ---------------------------------------------------------------------------
# Resume loader (supports .pdf and .txt)
# ---------------------------------------------------------------------------


def _load_resume(filepath: str) -> str:
    """
    Load resume text from a file.
    Supports .pdf (extracts text via PyPDF2) and .txt (plain read).
    """
    ext = os.path.splitext(filepath)[1].lower()

    if ext == ".pdf":
        from PyPDF2 import PdfReader
        reader = PdfReader(filepath)
        pages = [page.extract_text() or "" for page in reader.pages]
        text = "\n".join(pages).strip()
        if not text:
            raise ValueError(f"Could not extract any text from PDF: {filepath}")
        return text

    else:
        # .txt or any other text file
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read().strip()


# ═══════════════════════════════════════════════════════════════════════════
# PHASE A — SCRAPE
# ═══════════════════════════════════════════════════════════════════════════


def phase_scrape(
    platform: str,
    title: str,
    location: str,
    max_jobs: int,
    headless: bool,
    excel_path: str,
    past_24_hours: bool = True,
) -> str | None:
    """Scrape jobs and save raw data to Excel. Returns saved filepath or None."""
    from modules.scraper import scrape_jobs
    from modules.data_manager import save_jobs_to_excel

    print(f"\n{'═' * 60}")
    print(f"  PHASE 1 — SCRAPING")
    print(f"{'═' * 60}")
    print(f"  Platform : {platform}")
    print(f"  Title    : {title}")
    print(f"  Location : {location}")
    print(f"  Max jobs : {max_jobs}")
    print(f"  Headless : {headless}\n")

    jobs, driver = scrape_jobs(
        platform=platform,
        job_title=title,
        location=location,
        max_jobs=max_jobs,
        headless=headless,
        past_24_hours=past_24_hours,
    )

    # Close browser immediately
    if driver:
        from modules.browser import force_quit_driver
        force_quit_driver(driver)
        print("\n🛑  Browser closed.")

    if not jobs:
        print("\n⚠️   No jobs found. Try different search terms or --no-headless.")
        return None

    # ── Vector DB: embed all scraped job descriptions ──────────────────────
    try:
        from modules.vector_store import upsert_job, clear_collection, collection_size
        clear_collection()  # fresh run = fresh embeddings
        for job in jobs:
            job_id = _make_job_id(platform, job.get("job_link", ""))
            upsert_job(
                job_id=job_id,
                description=job.get("job_description", ""),
                metadata={
                    "title": str(job.get("job_title", ""))[:500],
                    "company": str(job.get("company", ""))[:500],
                    "platform": platform,
                },
            )
        print(f"\n🧠  Vector DB: {collection_size()} job(s) embedded.")
    except Exception as ve:
        print(f"\n⚠️   Vector embedding skipped: {ve}")

    path = save_jobs_to_excel(jobs, filepath=excel_path)
    print(f"\n📊  {len(jobs)} job(s) saved to: {path}")
    return path


# ═══════════════════════════════════════════════════════════════════════════
# PHASE B — EVALUATE
# ═══════════════════════════════════════════════════════════════════════════


def _make_job_id(platform: str, job_link: str) -> str:
    """Generate a stable, safe vector DB ID from platform + job URL."""
    import hashlib
    raw = f"{platform}_{job_link}"
    return hashlib.md5(raw.encode()).hexdigest()


def phase_evaluate(
    excel_path: str,
    resume_text: str,
    threshold: int,
    llm_provider: str = "gemini",
    output_dir: str = None,
    profile_links: dict = None,
    profile_text: str = "",
    platform: str = "linkedin",
) -> None:
    """Read Excel, evaluate each job via LLM, generate PDFs, update Excel."""
    from modules.data_manager import load_jobs_from_excel, update_excel
    from modules.evaluation import evaluate_job
    from modules.resume_tailor import tailor_resume

    print(f"\n{'═' * 60}")
    print(f"  PHASE 2 — AI EVALUATION & RESUME TAILORING")
    print(f"{'═' * 60}")
    print(f"  Excel     : {excel_path}")
    print(f"  LLM       : {llm_provider}")
    print(f"  Threshold : {threshold}\n")

    df = load_jobs_from_excel(excel_path)

    if df.empty:
        print("⚠️   Excel is empty — nothing to evaluate.")
        return
        
    # Ensure columns exist and are cast to object to avoid FutureWarnings during assignment
    for col in ["Match Score", "Missing Skills", "Readiness Assessment", "Resume PDF Path", "Tailored Match Score", "Vector Score"]:
        if col not in df.columns:
            df[col] = None
        df[col] = df[col].astype(object)

    # ── Vector DB: pre-score all jobs against profile ──────────────────────
    vector_scores: dict[str, float] = {}
    if profile_text:
        try:
            from modules.vector_store import bulk_score_against_profile
            vector_scores = bulk_score_against_profile(profile_text)
            print(f"🧠  Vector pre-scoring complete: {len(vector_scores)} job(s) scored.")
        except Exception as ve:
            print(f"⚠️   Vector scoring skipped: {ve}")

    total = len(df)
    scored = 0
    pdfs = 0
    pdf_driver = None  # Lazily initialize if needed

    for idx in range(total):
        row = df.iloc[idx]
        title = row.get("Job Title", "N/A")
        company = row.get("Company", "N/A")
        jd = str(row.get("Job Description", ""))

        print(f"── [{idx + 1}/{total}] {title} @ {company}")

        if not jd or jd == "nan" or len(jd.strip()) < 20:
            print("   ⚠️  Skipping — no job description available.")
            continue

        # --- Vector score (semantic similarity) ---------------------------
        job_link = str(row.get("Job Link", ""))
        job_id = _make_job_id(platform, job_link)
        v_score = vector_scores.get(job_id, 0.0)
        df.at[idx, "Vector Score"] = v_score
        if v_score > 0:
            print(f"   🧠 Vector Score: {v_score}")

        # --- LLM evaluation ----------------------------------------------
        try:
            result = evaluate_job(jd, resume_text, provider=llm_provider)
            score = result["match_score"]
            missing = result["missing_skills"]
            readiness = result["readiness_assessment"]

            # Blend LLM score (70%) + vector score (30%) for final match score
            if v_score > 0:
                blended_score = round(0.7 * score + 0.3 * v_score)
                df.at[idx, "Match Score"] = blended_score
                print(f"   ✅ LLM: {score}  |  Vector: {v_score}  |  Blended: {blended_score}")
                score = blended_score  # use blended for threshold check
            else:
                df.at[idx, "Match Score"] = score
                print(f"   ✅ Score: {score}  |  Missing: {missing}")

            df.at[idx, "Missing Skills"] = ", ".join(missing) if missing else ""
            df.at[idx, "Readiness Assessment"] = readiness
            scored += 1

            print(f"   📝 {readiness[:120]}…" if len(readiness) > 120 else f"   📝 {readiness}")

        except Exception as exc:
            print(f"   ❌ Evaluation failed: {exc}")
            df.at[idx, "Match Score"] = 0
            df.at[idx, "Missing Skills"] = ""
            df.at[idx, "Readiness Assessment"] = f"ERROR: {exc}"
            continue

        # --- Resume tailoring (score ≥ threshold) ------------------------
        if score >= threshold:
            print(f"   📝 Score ≥ {threshold} — generating tailored resume PDF…")
            
            # Lazily initialize the PDF driver once
            if pdf_driver is None:
                from selenium import webdriver
                from selenium.webdriver.chrome.options import Options
                try:
                    options = Options()
                    options.add_argument("--headless=new")
                    options.add_argument("--disable-gpu")
                    options.add_argument("--no-sandbox")
                    options.add_argument("--allow-file-access-from-files")
                    pdf_driver = webdriver.Chrome(options=options)
                except Exception as e:
                    print(f"   ⚠️  Failed to start PDF driver: {e}")

            try:
                pdf_path, post_score = tailor_resume(
                    job_description=jd,
                    base_resume=resume_text,
                    company=str(company),
                    job_title=str(title),
                    match_score=score,
                    threshold=threshold,
                    provider=llm_provider,
                    driver=pdf_driver,
                    output_dir=output_dir,
                    profile_links=profile_links,
                )
                if pdf_path:
                    df.at[idx, "Resume PDF Path"] = pdf_path
                    df.at[idx, "Tailored Match Score"] = post_score
                    pdfs += 1
                    print(f"   📄 Saved: {pdf_path}  |  Tailored Score: {post_score}")
            except Exception as exc:
                print(f"   ⚠️  Resume tailoring failed: {exc}")

    # --- Cleanup PDF driver -----------------------------------------------
    if pdf_driver:
        try:
            pdf_driver.quit()
        except:
            pass

    # --- Save updated Excel -----------------------------------------------
    saved = update_excel(df, excel_path)
    print(f"\n{'═' * 60}")
    print(f"  ✅  PIPELINE COMPLETE")
    print(f"{'═' * 60}")
    print(f"  Jobs evaluated  : {scored}/{total}")
    print(f"  Resumes created : {pdfs}")
    print(f"  Excel updated   : {saved}")
    print(f"{'═' * 60}\n")


# ═══════════════════════════════════════════════════════════════════════════
# CLI — single command, both phases run automatically
# ═══════════════════════════════════════════════════════════════════════════


def main() -> None:
    config = load_config()
    search_cfg = config.get("search", {})
    eval_cfg = config.get("evaluation", {})
    profile_cfg = config.get("profile", {})
    
    # --- Search config ---
    platform = search_cfg.get("platform", "linkedin")
    
    titles = search_cfg.get("titles", [search_cfg.get("title", "Data Scientist")])
    if isinstance(titles, str): titles = [titles]
    
    locations = search_cfg.get("locations", [search_cfg.get("location", "Bangalore")])
    if isinstance(locations, str): locations = [locations]
    
    max_jobs = search_cfg.get("max_jobs", 25)
    past_24_hours = search_cfg.get("past_24_hours", True)
    headless = search_cfg.get("headless", True)
    
    # --- Evaluation config ---
    threshold = eval_cfg.get("threshold", 50)
    llm_provider = eval_cfg.get("llm_provider", "gemini")
    
    # --- Setup Dynamic Output Directory ---
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    print(111111)
    custom_out = eval_cfg.get("output_path", "")
    if custom_out and custom_out.strip():
        base_out = custom_out.strip()
    else:
        base_out = os.path.join(os.path.dirname(__file__), "output")
        
    run_dir = os.path.join(base_out, f"run_{timestamp}")
    active_output_dir = run_dir
    excel_path = os.path.join(run_dir, "job_applications.xlsx")

    try:
        os.makedirs(run_dir, exist_ok=True)

        # --- Load resume (hardcoded path or yaml override) -----------------
        resume_path = eval_cfg.get("base_resume_path", "")
        if not resume_path or not resume_path.strip():
            resume_path = DEFAULT_RESUME_PATH

        if not os.path.isfile(resume_path):
            print(f"❌  Resume file not found: {resume_path}")
            print("    Place your resume.pdf in the project root, or specify exactly in config.yaml")
            return

        resume_text = _load_resume(resume_path)
        print(f"📄  Resume loaded from: {resume_path}")
        print(f"    ({len(resume_text)} characters extracted)")

        # Format the lists into comma separated strings for native multi-search
        combined_title = ", ".join(titles)
        
        # Resolving Location Conflicts (Remote vs Hybrid Strictness)
        # If Remote is included, prune physical cities to prevent aggressive hybrid narrowing.
        locs_lower = [l.strip().lower() for l in locations]
        if any(l in ("remote", "work from home", "wfh") for l in locs_lower):
            print(f"🌍  Conflict override: 'Remote' detected. Ignoring physical cities for broader yield.")
            combined_location = "Remote"
        else:
            combined_location = ", ".join(locations)
        
        # --- Phase 1: Scrape Natively -----------------------------------------
        saved_excel = phase_scrape(
            platform=platform,
            title=combined_title,
            location=combined_location,
            max_jobs=max_jobs,
            headless=headless,
            excel_path=excel_path,
            past_24_hours=past_24_hours,
        )

        if not saved_excel:
            return

        # --- Phase 2: Evaluate (auto-triggered, uses the ACTUAL saved path) ---
        profile_text = profile_cfg.get("profile_text", "")
        phase_evaluate(
            excel_path=saved_excel,
            resume_text=resume_text,
            threshold=threshold,
            llm_provider=llm_provider,
            output_dir=active_output_dir,
            profile_links=profile_cfg,
            profile_text=profile_text,
            platform=platform,
        )
    finally:
        # Loophole fix: If the script failed or was interrupted and no files 
        # were created in the run directory, delete it to keep 'output' clean.
        if os.path.isdir(run_dir):
            if not os.listdir(run_dir):
                try:
                    os.rmdir(run_dir)
                    print(f"\n✨  Cleaned up empty output directory: {run_dir}")
                except:
                    pass


if __name__ == "__main__":
    main()
