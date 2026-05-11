# Implementation Plan

## Goal Description
Build and maintain a fully automated, stealthy job application pipeline that dynamically scrapes, evaluates, and tailors resumes based on job descriptions using LLMs.

## Current Architecture
The application is structured into a Phase 1 (Data Gathering) and Phase 2 (AI Processing).

### Components

#### 1. Core Orchestrator (`main.py`)
- Provides CLI arguments for configuring the platform, title, location, max_jobs, headless mode, etc.
- Executes Phase 1 and Phase 2 sequentially or independently if a scraped Excel file is provided.

#### 2. Web Scraping (`modules/scraper.py`, `modules/scrapers/`)
- Relies on `undetected_chromedriver` to avoid anti-bot mechanisms.
- **LinkedIn**: Custom scraper mimicking human behavior.
- **Naukri**: Custom scraper.
- Must ensure that browser instances are properly cleaned up to avoid memory leaks or locked files on Windows.

#### 3. LLM Integration (`modules/llm.py`, `modules/evaluation.py`)
- Interfaces with Gemini API and Groq API.
- Generates structured JSON or expected text formats detailing `match_score`, `missing_skills`, and `readiness_assessment`.

#### 4. Resume Generation (`modules/resume_tailor.py`, `templates/resume.html`)
- Ingests the base resume (`resume.txt` or `resume.pdf`) and the LLM's tailored sections.
- Fills an HTML template using `jinja2`.
- Uses a stealth driver in headless mode to render the HTML and print it to PDF (via Chrome DevTools Protocol `Page.printToPDF`).

#### 5. Data Management (`modules/data_manager.py`)
- Manages `job_applications.xlsx` inside dynamically generated `output/run_YYYYMMDD_HHMMSS/` folders.
- Handles reading URLs, avoiding duplicate scraping, and appending results (scores, feedback, PDF paths).

## Rules & Best Practices
- **Resume Constraints**: The generated PDF resume MUST strictly fit onto a single page. The LLM must not drop projects or experiences to achieve this; it must shorten the bullets.
- **Clickable Links**: Ensure that the resulting PDF preserves clickable profile links. The template should render a single clickable text (e.g. `<a>LinkedIn</a>`) rather than duplicate raw URLs.
- **Naukri Scraper**: Must apply a 24-hour filter to fetch only the latest jobs.
- **Browser Lifecycle**: Always use the context managers or the custom `force_quit_driver` in `browser.py` to prevent zombie Chrome processes.
- **LLM Prompts**: Ensure prompts explicitly ask for the correct output format (e.g., Markdown or JSON) and parse robustly to handle API quirks.
- **Extensibility**: When adding a new job platform, create a new file in `modules/scrapers/` and register it in `modules/scraper.py`. Ensure it adheres to the same return structure.
