# AI Sync Document
**Project Name**: Stealth Job Pipeline (Job Automation)
**Last Updated**: March 2026

## Purpose of this File
This file serves as the "brain dump" and entry point for any AI assistant (like Gemini or others) opening this project on a new machine. Read this file to instantly understand the project's goal, architecture, and current status without needing the user to explain everything from scratch.

## Project Overview
The "Stealth Job Pipeline" is a fully automated Python tool that streamlines the job application process. With a single command, it performs two main phases:
1. **Phase 1 (Scraping)**: Scrapes job postings from job boards (currently LinkedIn and Naukri) using `undetected_chromedriver` to bypass bot detection. It saves the raw job data to an Excel file inside a dynamically generated timestamped folder (e.g., `output/run_YYYYMMDD_HHMMSS/job_applications.xlsx`).
2. **Phase 2 (Evaluation & Tailoring)**: Uses Large Language Models (Gemini or Groq) to evaluate the scraped job descriptions against the user's base resume. It generates a match score, identifies missing skills, and assesses readiness. For jobs scoring above a certain threshold, the AI dynamically tailors the resume and generates a custom PDF using an HTML template (`templates/resume.html`) rendered via a headless browser. The Excel file is then updated with scores and links to the generated PDFs, which are also saved within the same run folder.

## System Architecture
- **Entry Point**: `main.py` handles the CLI inputs and orchestrates Phase 1 and Phase 2.
- **Modules (`modules/`)**:
  - `scraper.py` & `scrapers/`: Contains platform-specific scraping logic (LinkedIn/Naukri).
  - `data_manager.py`: Handles reading and writing to the Excel file using `pandas` and `openpyxl`.
  - `evaluation.py` & `llm.py`: Interfaces with the LLM API (Gemini/Groq) to score and evaluate the compatibility between the job description and the resume.
  - `resume_tailor.py`: Uses the LLM to rewrite parts of the resume, fills a Jinja2 template (`templates/resume.html`), and converts it to a PDF using a headless Chrome driver.
  - `browser.py`: Manages the lifecycle of the `undetected_chromedriver` instances to ensure stability.

## Key Technical Decisions & History
- **Browser Automation**: Uses `undetected_chromedriver` instead of standard Selenium. Recent fixes stabilized the driver lifecycle on Windows to handle `NoSuchWindowException` and `WinError 6` by properly managing and force-quitting browser processes.
- **Scraping Filters**: Implemented a configurable `past_24_hours` toggle in `config.yaml` that intelligently appends `&jobAge=1` (Naukri) or `&f_TPR=r86400` (LinkedIn).
- **Naukri Router Fix**: Fixed a critical React-router bug in Naukri where mismatched URL slugs triggered redirects that stripped query parameters. Slugs are now dynamically built from the full title array.
- **Location Conflict Resolution**: The orchestrator in `main.py` detects mixed "Remote" + Physical city searches and prioritizes Remote to prevent aggressive hybrid-role narrowing.
- **Safety & Cleanup**: Added a `try...finally` block in `main.py` that automatically deletes the timestamped run folder if it's empty (e.g., on failure or user interruption).
- **Deterministic profile injection**: To prevent LLM hallucination of personal links, the logic now injects LinkedIn/GitHub URLs from `config.yaml` directly into the final resume object after tailoring.
- **ATS Score Calculated critically**: Shattered prompt-anchoring by forcing LLMs to critically evaluate resumes and deduct points, preventing the "static 92 score" bug.
- **Resume Output Constraints**: The final PDF *must* strictly be a one-pager, and the LLM handles this by shortening text, never by dropping experience/project sections.
- **Clickable Links**: Profile links (LinkedIn, GitHub) in the final PDF must be neatly formatted as single clickable labels rather than duplicate URLs.
- **LLM Integration**: Supports both Google Gemini and Groq for fast and cheap inference.
- **Templating**: A custom HTML/CSS resume template is used with Jinja2. This allows for beautiful, dynamic PDF generation instead of rigid Word document manipulation.

## How to Proceed
Whenever you (the AI) resume work on this project:
1. Read this `AI_Sync.md` to get context.
2. Check `task.md` for current progress and active to-do items.
3. Check `implementation_plan.md` for architectural guidelines and rules.
4. Execute any tests or run `main.py` (with headless mode) to verify that the environment is set up correctly.
