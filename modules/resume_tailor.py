"""
Resume Tailoring Module
=======================
When a job scores above the threshold, calls the configured LLM to
rewrite the resume, then saves the result as a PDF using fpdf2.
"""

from __future__ import annotations

import base64
import json
import os
import re
from datetime import datetime

from jinja2 import Environment, FileSystemLoader
from modules.browser import create_stealth_driver, force_quit_driver
from modules.llm import call_llm

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCORE_THRESHOLD = 50  # minimum match_score to trigger tailoring
BASE_DIR = os.path.dirname(os.path.dirname(__file__))
TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")

# ---------------------------------------------------------------------------
# LLM prompt for resume rewriting
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are an expert resume writer specialising in Data Science and Machine Learning.

You will receive:
1. A **Job Description** the candidate wants to apply for.
2. A **Base Resume** of the candidate.

**Rewrite** the resume to align perfectly with the job description while maintaining the candidate's core identity. 
Produce **ONLY** a valid JSON object with exactly these keys:

{
  "name": "Candidate Name",
  "phone": "Phone Number",
  "email": "Email Address",
  "location": "City, Country",
  "linkedin": "LinkedIn URL",
  "portfolio": "Portfolio/Website URL",
  "github": "GitHub URL",
  "professional_summary": "A 2-3 sentence summary tailored to the JD. Be extremely concise.",
  "domains": ["Domain 1", "Domain 2"],
  "key_skills": {
    "Programming": "Python, SQL, etc.",
    "ML Frameworks": "PyTorch, Scikit-learn, etc.",
    "Tools": "Git, Docker, etc."
  },
  "experience": [
    {
      "company": "Company Name",
      "location": "Location",
      "title": "Job Title",
      "dates": "Start - End",
      "bullets": ["Bullet 1", "Bullet 2"],
      "skills_used": "List of skills used in this role"
    }
  ],
  "projects": [
    {
      "name": "Project Name",
      "dates": "Date/Duration",
      "tech_stack": "Tech 1, Tech 2",
      "primary_goal": "What was the goal?",
      "solution": "How did you solve it?",
      "result": "What was the outcome?"
    }
  ],
  "certifications": [
    {
      "name": "Cert Name",
      "issuer": "Issuer",
      "date": "Month Year"
    }
  ],
  "education": {
    "college": "University Name",
    "location": "City, State",
    "degree": "Degree Name",
    "score": "GPA/Percentage",
    "dates": "Start - End"
  },
  "post_tailor_match_score": "<integer 1-100, be highly critical and realistic>"
}

Rules:
- THE ENTIRE RESUME MUST FIT ON A SINGLE PAGE. Balance detail with spatial constraints.
- DO NOT DELETE OR OMIT ANY PROJECTS OR EXPERIENCE ENTRIES from the base resume. Shrink the bullet points organically rather than removing an entire entry.
- Bullet points MUST be highly detailed and professional. Follow a strict "Problem Statement -> Solution -> Impact" structure. DO NOT over-simplify the language; retain the technical depth, complexity, and specific methodologies from the base resume.
- CRITICAL: Quantify achievements using metrics and digits/numbers (e.g., "Improved efficiency by 20%", "Reduced latency by 15ms").
- Limit experience bullets to a maximum of 3-4 highly detailed, impactful points per role.
- Reframe all bullets and summaries to emphasize Data Science/ML skills mentioned in the JD, integrating them naturally.
- Keep contact info truthful from the base resume.
- Evaluate your own newly tailored resume against the JD, and predict its ATS match score (1-100). Output this integer as `post_tailor_match_score`. YOU MUST calculate this dynamically. Be highly critical. Deduct points for any JD requirements still missing from the tailored resume. 
- Return ONLY valid JSON, no markdown fences.
"""


def _build_prompt(job_description: str, base_resume: str) -> str:
    return (
        f"### Job Description\n{job_description}\n\n"
        f"### Base Resume\n{base_resume}"
    )


# ---------------------------------------------------------------------------
def _generate_pdf(content: dict, company: str, job_title: str, driver: any = None, output_dir: str = ".") -> str:
    """
    Build a PDF by rendering the Jinja2 HTML template and using
    Chrome headless to print it to a pixel-perfect PDF.
    """
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(TEMPLATE_DIR, exist_ok=True)

    # 1. Setup Jinja2 and render HTML
    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR))
    try:
        template = env.get_template("resume.html")
    except Exception as exc:
        raise FileNotFoundError(f"Missing templates/resume.html: {exc}")

    rendered_html = template.render(**content)

    # Save to a temporary HTML file
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_company = re.sub(r"[^a-zA-Z0-9]+", "_", company)[:30]
    safe_title = re.sub(r"[^a-zA-Z0-9]+", "_", job_title)[:30]
    
    filename_base = f"{safe_company}_{safe_title}_{timestamp}"
    temp_html_path = os.path.join(output_dir, f"{filename_base}.html")
    pdf_path = os.path.join(output_dir, f"{filename_base}.pdf")

    with open(temp_html_path, "w", encoding="utf-8") as f:
        f.write(rendered_html)

    # 2. Print to PDF via Chrome Headless
    local_driver = False
    if driver is None:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--allow-file-access-from-files")
        driver = webdriver.Chrome(options=options)
        local_driver = True
    
    try:
        # Load local HTML file safely escaping Windows backslashes
        import pathlib
        abs_html_url = pathlib.Path(temp_html_path).absolute().as_uri()
        driver.get(abs_html_url)
        
        # Give it a tiny moment to render
        import time
        time.sleep(1)

        # Use Chrome DevTools Protocol to generate PDF
        print_options = {
            "landscape": False,
            "displayHeaderFooter": False,
            "printBackground": True,
            "preferCSSPageSize": True,
            "marginTop": 0, "marginBottom": 0, "marginLeft": 0, "marginRight": 0
        }
        
        result = driver.execute_cdp_cmd("Page.printToPDF", print_options)
        
        # Decode and save the PDF
        with open(pdf_path, "wb") as f:
            f.write(base64.b64decode(result['data']))
            
    finally:
        if local_driver and driver:
            try:
                driver.quit()
            except:
                pass
            
        # Optional: clean up the temp HTML file
        if os.path.exists(temp_html_path):
            try:
                os.remove(temp_html_path)
            except Exception:
                pass

    return os.path.abspath(pdf_path)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def tailor_resume(
    job_description: str,
    base_resume: str,
    company: str,
    job_title: str,
    match_score: int,
    threshold: int = SCORE_THRESHOLD,
    provider: str = "gemini",
    driver: any = None,
    output_dir: str = ".",
    profile_links: dict = None,
) -> tuple[str, int] | tuple[None, None]:
    """
    If the match_score meets the threshold, call the LLM to rewrite the
    resume and save it as a PDF.

    Parameters
    ----------
    provider : str
        ``"gemini"`` or ``"groq"``.
    driver : uc.Chrome, optional
        An existing driver instance to reuse for printing.

    Returns
    -------
    tuple[str, int] | tuple[None, None]
        (Absolute path to the generated PDF, post tailor score), or ``(None, None)`` 
        if the score was below the threshold or tailoring failed.
    """
    if match_score < threshold:
        return None, None

    try:
        raw = call_llm(
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=_build_prompt(job_description, base_resume),
            provider=provider,
        )
    except Exception as exc:
        print(f"  ⚠️  Resume tailoring API call failed: {exc}")
        return None, None

    # Parse JSON (strip possible markdown fences)
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        content = json.loads(cleaned)
    except json.JSONDecodeError:
        print(f"  ⚠️  Could not parse tailored resume JSON:\n{raw[:200]}")
        return None, None

    # Deterministic Profile Link Injection
    if profile_links:
        for k in ["linkedin", "github", "portfolio"]:
            if profile_links.get(k):
                content[k] = profile_links[k]

    post_score = content.get("post_tailor_match_score", match_score)
    pdf_path = _generate_pdf(content, company, job_title, driver=driver, output_dir=output_dir)
    return pdf_path, post_score
