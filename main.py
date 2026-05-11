from __future__ import annotations
from typing import Optional, List

import threading
from fastapi import Depends, FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
import uvicorn

from app.crud import create_or_update_job
from app.models import Job, SessionLocal
from app.schemas import (
    DraftOut,
    DraftRequest,
    JobIngest,
    JobIngestOut,
    JobOut,
    NaukriSubmitRequest,
    PaginatedJobsOut,
    SearchRunOut,
    SearchRunRequest,
    SearchStatusOut,
    TagsOut,
)
from app.settings import settings
from job_finder import ingest_to_db, run_search
import math

app = FastAPI(title="Job Assistant")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ── DB dependency ─────────────────────────────────────────────────────────────
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Dashboard ─────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    db = SessionLocal()
    try:
        recent_jobs: list[Job] = (
            db.query(Job)
            .filter(Job.status == "new")
            .order_by(Job.match_score.desc(), Job.posted_at.desc())
            .limit(settings.page_size)
            .all()
        )
        today_count: int = (
            db.query(Job).filter(Job.freshness_bucket == "24h").count()
        )
        week_count: int = (
            db.query(Job)
            .filter(Job.freshness_bucket.in_(["24h", "3d", "7d"]))
            .count()
        )
        top_matches_count: int = db.query(Job).filter(Job.match_score >= 70, Job.status == "new").count()
        total_count = db.query(Job).filter(Job.status == "new").count()
        page_size = settings.page_size
        total_pages = math.ceil(total_count / page_size) if total_count > 0 else 0

        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "jobs": recent_jobs,
                "today_count": today_count,
                "week_count": week_count,
                "top_matches_count": top_matches_count,
                "total_count": total_count,
                "total_pages": total_pages,
                "current_page": 1,
                "page_size": page_size,
                "settings": {
                    "name": settings.name,
                    "skills": settings.skills,
                    "locations": settings.locations,
                    "remote_preferred": settings.remote_preferred,
                    "job_titles": settings.job_titles,
                },
            },
        )
    finally:
        db.close()


# ── Ingest single job ─────────────────────────────────────────────────────────
@app.post("/api/jobs/ingest", response_model=JobIngestOut)
def ingest_job(
    payload: JobIngest,
    db: Session = Depends(get_db),
) -> JobIngestOut:
    job = create_or_update_job(db, payload.model_dump())
    return JobIngestOut(
        id=job.id,
        match_score=job.match_score,
        freshness=job.freshness_bucket,
    )


# ── List jobs ─────────────────────────────────────────────────────────────────
@app.get("/api/jobs", response_model=PaginatedJobsOut)
def list_jobs(
    platform: str | None = None,
    freshness: str | None = None,
    status: str = "new",
    min_score: float = 0,
    page: int = 1,
    page_size: int = settings.page_size,
    db: Session = Depends(get_db),
) -> PaginatedJobsOut:
    if min_score > 0:
        q = db.query(Job).filter(
            Job.match_score >= min_score,
            Job.status == status,
        )
    else:
        q = db.query(Job).filter(
            Job.status == status,
        )
    
    if platform and platform != "all":
        q = q.filter(Job.platform == platform)
    if freshness and freshness != "all":
        q = q.filter(Job.freshness_bucket == freshness)
    
    total_count = q.count()
    # For global stats, we don't apply the platform/freshness filters
    today_count = db.query(Job).filter(Job.status == status, Job.freshness_bucket == "24h").count()
    week_count = db.query(Job).filter(Job.status == status, Job.freshness_bucket.in_(["24h", "3d", "7d"])).count()
    top_matches_count = db.query(Job).filter(Job.status == status, Job.match_score >= 70).count()
    
    total_pages = math.ceil(total_count / page_size) if total_count > 0 else 0
    
    jobs = (
        q.order_by(Job.match_score.desc(), Job.posted_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    
    return PaginatedJobsOut(
        jobs=[JobOut.model_validate(j) for j in jobs],
        total_count=total_count,
        today_count=today_count,
        week_count=week_count,
        top_matches_count=top_matches_count,
        page=page,
        page_size=page_size,
        total_pages=total_pages
    )


@app.get("/api/jobs/{job_id}", response_model=JobOut)
def get_job(
    job_id: int,
    db: Session = Depends(get_db),
) -> Job:
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


# ── Draft cover letter ────────────────────────────────────────────────────────
@app.post("/api/jobs/{job_id}/draft", response_model=DraftOut)
def create_draft(
    job_id: int,
    req: Optional[DraftRequest] = None,
    db: Session = Depends(get_db),
) -> DraftOut:
    from app.models import Job
    from modules.evaluation import generate_draft_with_llm
    from app.settings import settings
    
    job: Job | None = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Load resume text
    resume_text = ""
    try:
        from job_finder import _load_resume_text
        resume_text = _load_resume_text()
        if not resume_text:
            resume_text = settings.profile_text or "Candidate with 3 years of experience in testing and ML."
    except Exception as e:
        print(f"Error dynamically loading resume: {e}")
        resume_text = settings.profile_text or "Candidate with 3 years of experience in testing and ML."

    questions = req.questions if req else None
    
    result = generate_draft_with_llm(
        job_description=job.description or "",
        base_resume=resume_text,
        screening_questions=questions,
        provider=settings.llm_provider
    )
    
    return DraftOut(
        resume_version="puneet_v2.pdf",
        cover_letter=result["cover_letter"],
        screening_answers=result["screening_answers"],
    )


# ── Trigger full search + ingest ──────────────────────────────────────────────
@app.post("/api/search/run")
def trigger_search(
    body: SearchRunRequest,
):
    from job_finder import search_control

    with search_control.lock:
        if search_control.running:
            return {"status": "running", "message": "Search is already running."}
        search_control.running = True
        search_control.stopped = False
        search_control.paused = False
        search_control.progress = "Starting job search orchestrator..."
        search_control.evaluated_count = 0
        search_control.total_evaluated = 0

    def bg_search():
        try:
            result = run_search(tags=body.tags, platforms=body.platforms)
            jobs = result.get("jobs", [])
            with search_control.lock:
                search_control.progress = f"Pushing {len(jobs)} evaluated jobs to DB..."
            ingest_to_db(jobs)
            with search_control.lock:
                search_control.progress = f"Finished! Found {len(jobs)} jobs."
        except Exception as e:
            with search_control.lock:
                search_control.progress = f"Search failed: {str(e)}"
        finally:
            with search_control.lock:
                search_control.running = False

    threading.Thread(target=bg_search, daemon=True).start()
    return {"status": "started", "message": "Background search started."}


@app.post("/api/search/retry-failed")
def trigger_retry_failed():
    from job_finder import search_control, retry_failed_evaluations_pipeline

    with search_control.lock:
        if search_control.running:
            raise HTTPException(status_code=400, detail="Search or retry is already running.")
        # Synchronously initialize search control states to prevent race conditions on immediate status poll
        search_control.running = True
        search_control.stopped = False
        search_control.paused = False
        search_control.progress = "Analyzing database for failed evaluations..."
        search_control.evaluated_count = 0
        search_control.total_evaluated = 0

    threading.Thread(target=retry_failed_evaluations_pipeline, daemon=True).start()
    return {"status": "started", "message": "Evaluation retry started."}


@app.post("/api/jobs/check-availability")
def trigger_availability_check():
    from job_finder import search_control, check_jobs_availability

    with search_control.lock:
        if search_control.running:
            raise HTTPException(status_code=400, detail="Another background task is already running.")
        search_control.running = True
        search_control.stopped = False
        search_control.progress = "Starting job availability check..."

    threading.Thread(target=check_jobs_availability, daemon=True).start()
    return {"status": "started", "message": "Availability check started."}


@app.get("/api/jobs/{job_id}/naukri-questions")
def get_naukri_questions(job_id: int, force_refresh: bool = False, db: Session = Depends(get_db)):
    from app.models import Job
    from modules.scrapers.naukri_apply import NaukriApplyService
    from modules.browser import PlaywrightDriver, wait_for_login
    import time
    
    job = db.get(Job, job_id)
    if not job or job.platform != "naukri":
        raise HTTPException(status_code=404, detail="Naukri job not found")

    apply_service = NaukriApplyService({})
    naukri_job_id = apply_service.extract_job_id(job.url)

    # 1. Check if questions are already saved in DB (unless force_refresh is True)
    if not force_refresh and job.screening_questions is not None and len(job.screening_questions) > 0:
        print(f"Loading cached screening questions for job {job_id} from database.")
        return {
            "job_id": job.id,
            "naukri_job_id": naukri_job_id,
            "questions": job.screening_questions
        }

    driver = None
    questions = []
    
    try:
        # 2. If not saved, launch visible browser
        driver = PlaywrightDriver(headless=False)
        
        # Verify login first
        driver.page.goto("https://www.naukri.com/nlogin/login")
        wait_for_login(driver, timeout_minutes=2)
        
        # 3. Intercept network responses via route interception to capture questionnaire API payload reliably
        def handle_apply_route(route):
            print(f"DEBUG Interceptor (Route): Intercepting request to {route.request.url}")
            try:
                response = route.fetch()
                if response.status == 200:
                    try:
                        data = response.json()
                        print(f"DEBUG Interceptor (Route): Successfully retrieved JSON response with keys: {list(data.keys())}")
                        if "jobs" in data and len(data["jobs"]) > 0:
                            job_data = data["jobs"][0]
                            quest = job_data.get("questionnaire", [])
                            print(f"DEBUG Interceptor (Route): Found {len(quest)} questions in response.")
                            for q in quest:
                                # Avoid duplicates
                                existing_ids = {item["id"] for item in questions}
                                if q.get("questionId") not in existing_ids:
                                    questions.append({
                                        "id": q.get("questionId"),
                                        "text": q.get("questionName"),
                                        "type": q.get("questionType"),
                                        "mandatory": q.get("isMandatory"),
                                        "options": q.get("answerOption", {})
                                    })
                        else:
                            print("DEBUG Interceptor (Route): No 'jobs' key or empty jobs list in JSON.")
                    except Exception as json_err:
                        print(f"DEBUG Interceptor (Route): Error parsing response JSON: {json_err}")
                else:
                    print(f"DEBUG Interceptor (Route): Request failed with status code {response.status}")
                
                # Fulfill back to browser so page gets the response payload
                route.fulfill(response=response)
            except Exception as e:
                print(f"DEBUG Interceptor (Route) overall error: {e}")
                try:
                    route.continue_()
                except:
                    pass

        driver.page.route("**/apply-workflow/v1/apply", handle_apply_route)
        
        # Navigate directly to the job URL inside the browser
        driver.page.goto(job.url)
        driver.page.wait_for_timeout(3000)
        
        # Look for and click the "Apply" button to trigger the questionnaire network request
        apply_selectors = [
            "button:text-is('Apply')",
            "button:text-is('Apply Now')",
            "a:text-is('Apply')",
            "a:text-is('Apply Now')",
            "button:has-text('Apply'):not(:has-text('Applied'))", 
            "a:has-text('Apply'):not(:has-text('Applied'))",
            "#apply-button", 
            ".apply-button"
        ]
        apply_btn = None
        for sel in apply_selectors:
            try:
                apply_btn = driver.page.query_selector(sel)
                if apply_btn and apply_btn.is_visible():
                    print(f"DEBUG: Found visible Apply button using selector: '{sel}'")
                    break
            except:
                pass
                
        if apply_btn:
            apply_btn.click()
            print("Clicked Apply inside browser to trigger questionnaire.")
            # Wait up to 15 seconds for the network request to finish and be intercepted (non-blocking)
            for _ in range(30):
                if len(questions) > 0:
                    break
                driver.page.wait_for_timeout(500)
        else:
            print("Apply button not found, or already applied (button says 'Applied').")
            
        # 3. Save the fetched questions to the database (preserving existing cache if new crawl is empty)
        if len(questions) > 0:
            job.screening_questions = questions
            db.add(job)
            db.commit()
            print(f"Saved {len(questions)} screening questions for job {job_id} to database.")
        else:
            if job.screening_questions is not None and len(job.screening_questions) > 0:
                print(f"DEBUG: Preserving existing {len(job.screening_questions)} screening questions because no new questions were intercepted.")
                questions = job.screening_questions
            else:
                job.screening_questions = []
                db.add(job)
                db.commit()
        
        return {
            "job_id": job.id,
            "naukri_job_id": naukri_job_id,
            "questions": questions
        }
        
    except Exception as e:
        return {"error": f"Failed to fetch questions: {str(e)}"}
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass


@app.post("/api/jobs/{job_id}/naukri-submit")
def submit_naukri_answers(job_id: int, req: NaukriSubmitRequest, db: Session = Depends(get_db)):
    from app.models import Job
    from modules.browser import PlaywrightDriver, wait_for_login
    import time
    import re
    
    def extract_short_search_query(text: str, placeholder: str = "") -> str:
        text_clean = text.strip()
        placeholder_lower = placeholder.lower()
        
        # Check if this looks like an experience dropdown
        if "experience" in placeholder_lower or "year" in placeholder_lower:
            match = re.search(r'\d+', text_clean)
            if match:
                return match.group(0)
                
        # Check if this looks like a salary/CTC dropdown
        if "ctc" in placeholder_lower or "salary" in placeholder_lower or "annual" in placeholder_lower:
            # Find first number sequence which could represent CTC (e.g. 13 or 13,00,000)
            match = re.search(r'\d[\d,]*', text_clean)
            if match:
                return match.group(0)
                
        # Check if this looks like a notice period dropdown
        if "notice" in placeholder_lower or "period" in placeholder_lower:
            match = re.search(r'\d+', text_clean)
            if match:
                return match.group(0)
                
        # Default: Return first 2 words if it's a long sentence
        words = text_clean.split()
        if len(words) > 2:
            return " ".join(words[:2])
        return text_clean
    
    job = db.get(Job, job_id)
    if not job or job.platform != "naukri":
        raise HTTPException(status_code=404, detail="Naukri job not found")

    driver = None
    try:
        # 1. Start a visible browser
        driver = PlaywrightDriver(headless=False)
        
        # 2. Check login first (directly go to login page)
        driver.page.goto("https://www.naukri.com/nlogin/login")
        wait_for_login(driver, timeout_minutes=2)
        
        # 3. Navigate directly to the job URL
        driver.page.goto(job.url)
        driver.page.wait_for_timeout(3000)
        
        # Check if already applied
        page_text = driver.page.content().lower()
        if not req.force and "applied" in page_text:
            return {"status": "success", "message": "Already applied to this job."}
            
        # 4. Click the "Apply" button on the job page
        apply_selectors = [
            "button:has-text('Apply')", 
            "a:has-text('Apply')", 
            "#apply-button", 
            ".apply-button"
        ]
        apply_btn = None
        for sel in apply_selectors:
            try:
                apply_btn = driver.page.query_selector(sel)
                if apply_btn and apply_btn.is_visible():
                    break
            except:
                pass
                
        if apply_btn:
            apply_btn.click()
            print("Clicked Apply button on the page.")
            driver.page.wait_for_timeout(4000)
        else:
            if req.force:
                print("DEBUG: Apply button not found, but continuing screening questions loop because force=True")
            else:
                return {"error": "Could not find Apply button on page. You might have already applied. Use force=True to bypass."}
            
        # 5. Smart Sequential Answering Bot
        # This loop handles both static multi-question forms and conversational chatbots one-by-one!
        driver.page.wait_for_timeout(3000)
        
        js_click_option = """
        (aText, placeholderText) => {
            function cleanStr(s) {
                return (s || "").toLowerCase().replace(/[^a-z0-9 ]/g, "").replace(/\\s+/g, " ").trim();
            }
            const cleanAns = cleanStr(aText);
            const placeholder = (placeholderText || "").toLowerCase();
            if (!cleanAns) return null;
            
            function search(root) {
                if (!root) return null;
                
                // Potential option tags
                const selectors = ["li", "[role='option']", ".option", ".dropdown-item", "span", "div"];
                for (const sel of selectors) {
                    const els = root.querySelectorAll(sel);
                    for (const el of els) {
                        const rect = el.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) {
                            const elText = el.textContent || "";
                            const elTextClean = cleanStr(elText);
                            
                            if (elTextClean.length > 0 && elTextClean.length < 100) {
                                // 1. Verify parent container is dropdown/select/menu related
                                let parent = el;
                                let inDropdown = false;
                                for (let level = 0; level < 6; level++) {
                                    if (!parent) break;
                                    const id = (parent.id || "").toLowerCase();
                                    const cls = (parent.className || "").toString().toLowerCase();
                                    if (id.includes("select") || id.includes("dropdown") || id.includes("menu") || id.includes("suggest") || id.includes("popup") || id.includes("layer") || id.includes("options") || id.includes("list")) {
                                        inDropdown = true;
                                        break;
                                    }
                                    if (cls.includes("select") || cls.includes("dropdown") || cls.includes("menu") || cls.includes("suggest") || cls.includes("popup") || cls.includes("layer") || cls.includes("options") || cls.includes("list")) {
                                        inDropdown = true;
                                        break;
                                    }
                                    parent = parent.parentElement;
                                }
                                
                                if (!inDropdown) continue;
                                
                                // 2. Verify option matches the select field's context (experience, salary, notice period)
                                let matchesContext = true;
                                const optTextLower = elText.toLowerCase();
                                if (placeholder.includes("experience") || placeholder.includes("year")) {
                                    matchesContext = /\\d/.test(optTextLower) || optTextLower.includes("fresher");
                                } else if (placeholder.includes("ctc") || placeholder.includes("salary") || placeholder.includes("annual")) {
                                    matchesContext = /\\d/.test(optTextLower);
                                } else if (placeholder.includes("notice") || placeholder.includes("period")) {
                                    matchesContext = /\\d/.test(optTextLower) || optTextLower.includes("immediate") || optTextLower.includes("serving") || optTextLower.includes("day") || optTextLower.includes("month");
                                }
                                
                                if (!matchesContext) continue;
                                
                                // 3. Match answer substring safely
                                let isMatch = false;
                                if (cleanAns === elTextClean) {
                                    isMatch = true;
                                } else if (elTextClean.length > 2) {
                                    const hasNumbers = /\\d/.test(elTextClean);
                                    if (hasNumbers || elTextClean.length > 8) {
                                        isMatch = cleanAns.includes(elTextClean) || elTextClean.includes(cleanAns);
                                    } else {
                                        const words = cleanAns.split(" ");
                                        isMatch = words.includes(elTextClean);
                                    }
                                }
                                
                                if (isMatch) {
                                    el.click();
                                    return { tag: el.tagName, text: el.textContent };
                                }
                            }
                        }
                    }
                }
                
                const all = root.querySelectorAll("*");
                for (const el of all) {
                    if (el.shadowRoot) {
                        const found = search(el.shadowRoot);
                        if (found) return found;
                    }
                }
                
                const iframes = root.querySelectorAll("iframe, frame");
                for (const iframe of iframes) {
                    try {
                        if (iframe.contentDocument) {
                            const found = search(iframe.contentDocument);
                            if (found) return found;
                        }
                    } catch (e) {}
                }
                return null;
            }
            return search(document);
        }
        """
        
        db_questions = job.screening_questions or []
        answered_question_ids = set()
        
        # Max steps to prevent infinite loop (number of answers * 2 + 3 fallback)
        max_steps = len(req.answers) * 2 + 3
        
        if len(req.answers) > 0:
            print(f"Bot starting dynamic sequential application with {len(req.answers)} answers...")
            
            for step in range(max_steps):
                if len(answered_question_ids) >= len(req.answers):
                    print("All questions have been answered. Exiting dynamic conversational loop.")
                    break
                    
                # 1. Identify which of our unanswered questions is currently active/visible on the screen
                active_ans = None
                active_q_text = None
                
                for ans in req.answers:
                    if ans.id in answered_question_ids:
                        continue
                        
                    # Get question text from database cache
                    q_obj = None
                    for db_q in db_questions:
                        if str(db_q.get("id")) == str(ans.id):
                            q_obj = db_q
                            break
                            
                    if q_obj:
                        q_text = q_obj.get("text")
                        # Check if this question text is visible in the page DOM (deep search)
                        is_visible = False
                        try:
                            is_visible = driver.page.evaluate(f"""
                            (qText) => {{
                                function cleanStr(s) {{
                                    return (s || "").toLowerCase().replace(/[^a-z0-9]/g, "").trim();
                                }}
                                const cleanQ = cleanStr(qText);
                                if (!cleanQ) return false;
                                
                                function search(root) {{
                                    if (!root) return false;
                                    
                                    const allElements = root.querySelectorAll("*");
                                    for (const el of allElements) {{
                                        const rect = el.getBoundingClientRect();
                                        if (rect.width > 0 && rect.height > 0) {{
                                            const text = el.textContent || "";
                                            if (cleanStr(text).includes(cleanQ)) {{
                                                return true;
                                            }}
                                        }}
                                        if (el.shadowRoot) {{
                                            if (search(el.shadowRoot)) return true;
                                        }}
                                    }}
                                    
                                    const iframes = root.querySelectorAll("iframe, frame");
                                    for (const iframe of iframes) {{
                                        try {{
                                            if (iframe.contentDocument && search(iframe.contentDocument)) {{
                                                return true;
                                            }}
                                        }} catch (e) {{}}
                                    }}
                                    return false;
                                }}
                                return search(document);
                            }}
                            """, q_text)
                        except:
                            pass
                            
                        if is_visible:
                            active_ans = ans
                            active_q_text = q_text
                            break
                
                # Fallback: If no specific question matches or is visible yet, check if there's a visible generic input field to fill
                if not active_ans:
                    print("DEBUG Step: No specific unanswered question text detected on page yet. Waiting 2 seconds for animation...")
                    driver.page.wait_for_timeout(2000)
                    
                    # Try to see if we can locate an active chatbot text field and fill the next chronological unanswered answer
                    unanswered_list = [a for a in req.answers if a.id not in answered_question_ids]
                    if unanswered_list:
                        next_ans = unanswered_list[0]
                        print("DEBUG: Attempting direct focus of any active input box for sequential next answer...")
                        try:
                            js_focus_input = """
                            () => {
                                function getCandidates(root) {
                                    if (!root) return [];
                                    let list = [];
                                    
                                    const selectors = ["textarea", "input", "[contenteditable='true']"];
                                    for (const sel of selectors) {
                                        const els = root.querySelectorAll(sel);
                                        for (const el of els) {
                                            const rect = el.getBoundingClientRect();
                                            if (rect.width > 0 && rect.height > 0) {
                                                const type = (el.getAttribute("type") || "").toLowerCase();
                                                if (type === "hidden" || type === "radio" || type === "checkbox") continue;
                                                
                                                const placeholder = (el.getAttribute("placeholder") || "").toLowerCase();
                                                if (placeholder.includes("keyword") || placeholder.includes("company") || placeholder.includes("designation") || placeholder.includes("skills") || placeholder.includes("location") || placeholder.includes("search")) {
                                                    continue; // Skip main page header search bars
                                                }
                                                
                                                let score = 1;
                                                if (placeholder.includes("message") || placeholder.includes("type") || placeholder.includes("reply") || placeholder.includes("answer") || placeholder.includes("write")) {
                                                    score += 50;
                                                }
                                                
                                                let parent = el;
                                                for (let level = 0; level < 6; level++) {
                                                    if (!parent) break;
                                                    const id = (parent.id || "").toLowerCase();
                                                    const cls = (parent.className || "").toString().toLowerCase();
                                                    if (id.includes("chat") || id.includes("bot") || id.includes("drawer") || id.includes("apply") || id.includes("slider") || id.includes("container")) score += 10;
                                                    if (cls.includes("chat") || cls.includes("bot") || cls.includes("drawer") || cls.includes("apply") || cls.includes("slider") || cls.includes("container")) score += 10;
                                                    parent = parent.parentElement;
                                                }
                                                
                                                list.push({ element: el, score: score, tag: el.tagName, placeholder: el.getAttribute("placeholder") || "" });
                                            }
                                        }
                                    }
                                    
                                    const all = root.querySelectorAll("*");
                                    for (const el of all) {
                                        if (el.shadowRoot) {
                                            list = list.concat(getCandidates(el.shadowRoot));
                                        }
                                    }
                                    
                                    const iframes = root.querySelectorAll("iframe, frame");
                                    for (const iframe of iframes) {
                                        try {
                                            if (iframe.contentDocument) {
                                                list = list.concat(getCandidates(iframe.contentDocument));
                                            }
                                        } catch (e) {}
                                    }
                                    return list;
                                }
                                
                                const candidates = getCandidates(document);
                                if (candidates.length > 0) {
                                    candidates.sort((a, b) => b.score - a.score);
                                    const best = candidates[0];
                                    best.element.focus();
                                    return { tag: best.tag, placeholder: best.placeholder, score: best.score };
                                }
                                return null;
                            }
                            """
                            focused = driver.page.evaluate(f"({js_focus_input})()")
                            if focused:
                                placeholder = focused.get("placeholder", "")
                                is_dropdown = any(k in placeholder.lower() for k in ["select", "choose", "experience", "salary", "ctc", "notice"])
                                
                                clicked = False
                                if is_dropdown:
                                    try:
                                        clicked_res = driver.page.evaluate(f"({js_click_option})({repr(next_ans.text)}, {repr(placeholder)})")
                                        if clicked_res:
                                            print(f"DEBUG: Successfully clicked global matching option for fallback: {clicked_res}")
                                            clicked = True
                                    except Exception as click_err:
                                        print(f"DEBUG Fallback click option error: {click_err}")
                                        
                                if clicked:
                                    answered_question_ids.add(next_ans.id)
                                    driver.page.wait_for_timeout(4000)
                                    continue
                                    
                                typed_text = extract_short_search_query(next_ans.text, placeholder) if is_dropdown else next_ans.text
                                print(f"DEBUG: Active input found: {focused}. Typing: '{typed_text[:35]}...'")
                                driver.page.keyboard.press("Meta+A")
                                driver.page.keyboard.press("Control+A")
                                driver.page.keyboard.press("Backspace")
                                driver.page.wait_for_timeout(500)
                                
                                driver.page.keyboard.type(typed_text)
                                driver.page.wait_for_timeout(1000)
                                
                                if is_dropdown:
                                    driver.page.keyboard.press("ArrowDown")
                                    driver.page.wait_for_timeout(500)
                                    driver.page.keyboard.press("Enter")
                                else:
                                    driver.page.keyboard.press("Enter")
                                    
                                print(f"DEBUG: Filled active input and pressed Enter for: '{typed_text[:35]}...'")
                                answered_question_ids.add(next_ans.id)
                                driver.page.wait_for_timeout(4000)
                                continue
                        except Exception as focus_err:
                            print(f"DEBUG Error focused fallback: {focus_err}")
                            
                    print("DEBUG: No active input or visible question found. Continuing...")
                    continue
                
                print(f"Step {step + 1}: Detected active question on page: '{active_q_text}'")
                print(f"Answering with: '{active_ans.text}'")
                
                filled_successfully = False
                
                # Use Deep Context-Aware JS Filler inside shadow-DOMs and iframes
                try:
                    js_fill_func = """
                    (qText, aText) => {
                        function cleanStr(s) {
                            return (s || "").toLowerCase().replace(/[^a-z0-9]/g, "").trim();
                        }
                        const cleanQ = cleanStr(qText);
                        if (!cleanQ) return null;
                        
                        function search(root) {
                            if (!root) return null;
                            
                            const allElements = root.querySelectorAll("*");
                            let matchedQEl = null;
                            
                            for (const el of allElements) {
                                const rect = el.getBoundingClientRect();
                                if (rect.width > 0 && rect.height > 0) {
                                    const elTextClean = cleanStr(el.textContent);
                                    if (elTextClean === cleanQ || elTextClean.includes(cleanQ)) {
                                        if (!matchedQEl || el.textContent.length < matchedQEl.textContent.length) {
                                            matchedQEl = el;
                                        }
                                    }
                                }
                                if (el.shadowRoot) {
                                    const found = search(el.shadowRoot);
                                    if (found) return found;
                                }
                            }
                            
                            if (matchedQEl) {
                                let parent = matchedQEl;
                                for (let level = 0; level < 5; level++) {
                                    if (!parent) break;
                                    
                                    // 1. Scan for MCQ option clicks
                                    const options = parent.querySelectorAll("button, label, li, span, input[type='radio'], input[type='checkbox']");
                                    for (const opt of options) {
                                        const optTextClean = cleanStr(opt.textContent);
                                        const ansClean = cleanStr(aText);
                                        if (optTextClean === ansClean || (ansClean.length > 2 && optTextClean.includes(ansClean)) || (optTextClean.length > 2 && ansClean.includes(optTextClean))) {
                                            opt.click();
                                            return { status: "clicked", type: "option", text: opt.textContent };
                                        }
                                    }
                                    
                                    // 2. Scan for text inputs/textareas
                                    const inputs = parent.querySelectorAll("textarea, input, [contenteditable='true']");
                                    for (const input of inputs) {
                                        const type = (input.getAttribute("type") || "").toLowerCase();
                                        if (type === "hidden" || type === "radio" || type === "checkbox") continue;
                                        
                                        input.focus();
                                        if (input.tagName === "INPUT" || input.tagName === "TEXTAREA") {
                                            input.value = aText;
                                        } else {
                                            input.innerText = aText;
                                        }
                                        input.dispatchEvent(new Event('input', { bubbles: true }));
                                        input.dispatchEvent(new Event('change', { bubbles: true }));
                                        return { status: "filled", type: "text", tag: input.tagName };
                                    }
                                    
                                    parent = parent.parentElement;
                                }
                            }
                            
                            const iframes = root.querySelectorAll("iframe, frame");
                            for (const iframe of iframes) {
                                try {
                                    if (iframe.contentDocument) {
                                        const found = search(iframe.contentDocument);
                                        if (found) return found;
                                    }
                                } catch (e) {}
                            }
                            return null;
                        }
                        return search(document);
                    }
                    """
                    result = driver.page.evaluate(f"({js_fill_func})({repr(active_q_text)}, {repr(active_ans.text)})")
                    if result:
                        print(f"DEBUG Context-Aware Match success: {result}")
                        filled_successfully = True
                        driver.page.wait_for_timeout(2000)
                except Exception as js_err:
                    print(f"DEBUG Error running Context-Aware JS filler: {js_err}")
                    
                if not filled_successfully:
                    # Fallback: Chatbot structure might keep text input separate. Focus active chatbot input and type
                    print("DEBUG: Context container search failed. Targeting main chatbot input box via deep focus...")
                    try:
                        js_focus_input = """
                        () => {
                            function getCandidates(root) {
                                if (!root) return [];
                                let list = [];
                                
                                const selectors = ["textarea", "input", "[contenteditable='true']"];
                                for (const sel of selectors) {
                                    const els = root.querySelectorAll(sel);
                                    for (const el of els) {
                                        const rect = el.getBoundingClientRect();
                                        if (rect.width > 0 && rect.height > 0) {
                                            const type = (el.getAttribute("type") || "").toLowerCase();
                                            if (type === "hidden" || type === "radio" || type === "checkbox") continue;
                                            
                                            const placeholder = (el.getAttribute("placeholder") || "").toLowerCase();
                                            if (placeholder.includes("keyword") || placeholder.includes("company") || placeholder.includes("designation") || placeholder.includes("skills") || placeholder.includes("location") || placeholder.includes("search")) {
                                                continue; // Skip main page header search bars
                                            }
                                            
                                            let score = 1;
                                            if (placeholder.includes("message") || placeholder.includes("type") || placeholder.includes("reply") || placeholder.includes("answer") || placeholder.includes("write")) {
                                                score += 50;
                                            }
                                            
                                            let parent = el;
                                            for (let level = 0; level < 6; level++) {
                                                if (!parent) break;
                                                const id = (parent.id || "").toLowerCase();
                                                const cls = (parent.className || "").toString().toLowerCase();
                                                if (id.includes("chat") || id.includes("bot") || id.includes("drawer") || id.includes("apply") || id.includes("slider") || id.includes("container")) score += 10;
                                                if (cls.includes("chat") || cls.includes("bot") || cls.includes("drawer") || cls.includes("apply") || cls.includes("slider") || cls.includes("container")) score += 10;
                                                parent = parent.parentElement;
                                            }
                                            
                                            list.push({ element: el, score: score, tag: el.tagName, placeholder: el.getAttribute("placeholder") || "" });
                                        }
                                    }
                                }
                                
                                const all = root.querySelectorAll("*");
                                for (const el of all) {
                                    if (el.shadowRoot) {
                                        list = list.concat(getCandidates(el.shadowRoot));
                                    }
                                }
                                
                                const iframes = root.querySelectorAll("iframe, frame");
                                for (const iframe of iframes) {
                                    try {
                                        if (iframe.contentDocument) {
                                            list = list.concat(getCandidates(iframe.contentDocument));
                                        }
                                    } catch (e) {}
                                }
                                return list;
                            }
                            
                            const candidates = getCandidates(document);
                            if (candidates.length > 0) {
                                candidates.sort((a, b) => b.score - a.score);
                                const best = candidates[0];
                                best.element.focus();
                                return { tag: best.tag, placeholder: best.placeholder, score: best.score };
                            }
                            return null;
                        }
                        """
                        focused_input = driver.page.evaluate(f"({js_focus_input})()")
                        if focused_input:
                            placeholder = focused_input.get("placeholder", "")
                            is_dropdown = any(k in placeholder.lower() for k in ["select", "choose", "experience", "salary", "ctc", "notice"])
                            
                            clicked = False
                            if is_dropdown:
                                try:
                                    clicked_res = driver.page.evaluate(f"({js_click_option})({repr(active_ans.text)}, {repr(placeholder)})")
                                    if clicked_res:
                                        print(f"DEBUG: Successfully clicked global matching option: {clicked_res}")
                                        clicked = True
                                except Exception as click_err:
                                        print(f"DEBUG Click option error: {click_err}")
                                        
                            if clicked:
                                filled_successfully = True
                                driver.page.wait_for_timeout(4000)
                            else:
                                typed_text = extract_short_search_query(active_ans.text, placeholder) if is_dropdown else active_ans.text
                                print(f"DEBUG Focused chatbot input: {focused_input}. Typing: '{typed_text[:35]}...'")
                                driver.page.keyboard.press("Meta+A")
                                driver.page.keyboard.press("Control+A")
                                driver.page.keyboard.press("Backspace")
                                driver.page.wait_for_timeout(500)
                                
                                driver.page.keyboard.type(typed_text)
                                driver.page.wait_for_timeout(1000)
                                if is_dropdown:
                                    driver.page.keyboard.press("ArrowDown")
                                    driver.page.wait_for_timeout(500)
                                    driver.page.keyboard.press("Enter")
                                else:
                                    driver.page.keyboard.press("Enter")
                                    
                                print(f"DEBUG Fallback chatbot text fill success: '{typed_text[:30]}...'")
                                filled_successfully = True
                                driver.page.wait_for_timeout(4000) # Allow response animation
                    except Exception as fill_err:
                        print(f"DEBUG Fallback filling chatbot error: {fill_err}")
                            
                if filled_successfully:
                    answered_question_ids.add(active_ans.id)
                else:
                    print(f"WARNING: Could not fill or select option for active question: '{active_q_text}'")
                    driver.page.wait_for_timeout(2000)
            
            # 6. Final Submission
            # If it was a static form, we need to click the final submit button
            submit_selectors = [
                "button:has-text('Submit')",
                "button:has-text('Save & Continue')",
                "button:has-text('Continue')",
                "input[type='submit']",
                ".submit-btn"
            ]
            submit_btn = None
            for sel in submit_selectors:
                try:
                    submit_btn = driver.page.query_selector(sel)
                    if submit_btn and submit_btn.is_visible():
                        break
                except:
                    pass
                    
            if submit_btn:
                submit_btn.click()
                print("Clicked final Submit button.")
                driver.page.wait_for_timeout(4000)
                return {"status": "success", "message": "Application submitted successfully by the Bot!"}
                
            return {"status": "success", "message": "All answers filled/submitted by the Bot!"}
            
        return {"status": "success", "message": "Applied successfully (no questions required)!"}
        
    except Exception as e:
        return {"error": f"Failed to submit: {str(e)}"}
    finally:
        if driver:
            try:
                driver.page.wait_for_timeout(3000) # Let the user see the result
            except:
                pass
            try:
                driver.quit()
            except:
                pass



@app.get("/api/search/status", response_model=SearchStatusOut)
def get_search_status() -> SearchStatusOut:
    from job_finder import search_control
    with search_control.lock:
        return SearchStatusOut(
            running=search_control.running,
            paused=search_control.paused,
            stopped=search_control.stopped,
            progress=search_control.progress,
            evaluated_count=search_control.evaluated_count,
            total_evaluated=search_control.total_evaluated,
        )


# ── Search Control (Pause / Resume / Stop) ────────────────────────────────────
@app.post("/api/search/pause")
def pause_search():
    from job_finder import search_control
    with search_control.lock:
        search_control.paused = True
    return {"status": "paused"}


@app.post("/api/search/resume")
def resume_search():
    from job_finder import search_control
    with search_control.lock:
        search_control.paused = False
    return {"status": "running"}


@app.post("/api/search/stop")
def stop_search():
    from job_finder import search_control
    with search_control.lock:
        search_control.stopped = True
        search_control.paused = False  # Resume if paused so they can exit clean
    return {"status": "stopped"}


# ── Current tags ──────────────────────────────────────────────────────────────
@app.get("/api/search/tags", response_model=TagsOut)
def get_tags() -> TagsOut:
    return TagsOut(
        job_titles=settings.job_titles,
        skills=settings.skills,
        locations=settings.locations,
    )


# ── Clear All Data ────────────────────────────────────────────────────────────
@app.post("/api/jobs/clear")
def clear_jobs(db: Session = Depends(get_db)):
    try:
        # Clear SQLite DB
        db.query(Job).delete()
        db.commit()
        
        # Clear Vector DB
        from modules.vector_store import clear_collection
        clear_collection()
        
        return {"status": "success", "message": "All jobs and vector data cleared."}
    except Exception as e:
        db.rollback()
# ── Re-evaluate specific job ──────────────────────────────────────────────────
@app.post("/api/jobs/{job_id}/evaluate", response_model=JobOut)
def evaluate_single_job_api(
    job_id: int,
    db: Session = Depends(get_db),
) -> Job:
    from job_finder import _load_resume_text, _make_job_id
    from modules.evaluation import evaluate_job
    from modules.vector_store import vector_score_job

    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    resume_text = _load_resume_text()
    if not resume_text:
        raise HTTPException(status_code=400, detail="Base resume not found or empty")

    try:
        # Re-fetch/calculate vector score if it is None or 0
        v_score = job.vector_score or 0.0
        if v_score == 0.0 and settings.profile_text:
            v_id = _make_job_id(job.platform, job.url)
            v_score = vector_score_job(v_id, settings.profile_text)
            job.vector_score = v_score

        res = evaluate_job(job.description or "", resume_text, provider=settings.llm_provider)
        llm_score = res["match_score"]
        job.missing_skills = res["missing_skills"]
        job.readiness_assessment = res["readiness_assessment"]

        if v_score > 0:
            job.match_score = round(0.7 * llm_score + 0.3 * v_score)
        else:
            job.match_score = llm_score

        db.commit()
        db.refresh(job)
        return job
    except Exception as e:
        db.rollback()
        err_msg = str(e)
        if "429" in err_msg or "quota" in err_msg.lower() or "limit" in err_msg.lower():
            raise HTTPException(
                status_code=429,
                detail="Gemini API Rate Limit or Quota Exceeded. Please wait 30 seconds and try again!"
            )
        raise HTTPException(status_code=500, detail=f"AI Evaluation failed: {err_msg}")


# ── Mark Job as Applied ────────────────────────────────────────────────────────
@app.post("/api/jobs/{job_id}/apply")
def mark_job_applied_api(
    job_id: int,
    db: Session = Depends(get_db),
):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    try:
        job.status = "applied"
        db.commit()
        return {"status": "success", "message": f"Job {job_id} marked as applied."}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to update status: {str(e)}")


# ── Mark Job as Ignored ───────────────────────────────────────────────────────
@app.post("/api/jobs/{job_id}/ignore")
def mark_job_ignored_api(
    job_id: int,
    db: Session = Depends(get_db),
):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    try:
        job.status = "ignored"
        db.commit()
        return {"status": "success", "message": f"Job {job_id} marked as ignored."}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to update status: {str(e)}")


# ── LinkedIn Invitation Accept Bot ───────────────────────────────────────────
@app.post("/api/linkedin/accept-invitations")
def trigger_accept_linkedin_invitations_api(background_tasks: BackgroundTasks):
    from job_finder import search_control
    import time
    
    with search_control.lock:
        if search_control.running:
            raise HTTPException(status_code=400, detail="Another background task is currently running.")
        search_control.running = True
        search_control.stopped = False
        search_control.paused = False
        search_control.progress = "Starting LinkedIn Invitation Accept Bot..."
        
    def bg_task():
        from modules.scrapers import accept_linkedin_invitations
        
        def update_progress(msg: str):
            with search_control.lock:
                search_control.progress = msg
                
        try:
            # Run in non-headless mode so user can see or login if needed
            res = accept_linkedin_invitations(headless=False, progress_callback=update_progress)
            with search_control.lock:
                search_control.progress = f"Finished: {res.get('message', 'Completed')}"
        except Exception as e:
            with search_control.lock:
                search_control.progress = f"Error accepting invitations: {str(e)}"
        finally:
            time.sleep(5)
            with search_control.lock:
                search_control.running = False
                
    background_tasks.add_task(bg_task)
    return {"status": "started"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
