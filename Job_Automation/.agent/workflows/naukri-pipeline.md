---
description: Run the full Naukri job scraping and evaluation pipeline
---

# Naukri Pipeline Workflow

## Prerequisites
- Chrome browser installed
- Python dependencies installed: `pip install -r requirements.txt`
- `GEMINI_API_KEY` set in `.env` file
- `resume.txt` updated with your actual resume content

## Steps

// turbo-all

1. **Run the full pipeline** (scrape + evaluate in one command):
   ```powershell
   cd c:\Users\karti\job_auto
   python main.py --platform naukri --title "Data Scientist" --location "Bangalore" --max-jobs 5 --no-headless --resume resume.txt
   ```

   This will automatically:
   - **Phase 1**: Open Chrome, navigate to Naukri public job search (e.g. `naukri.com/data-scientist-jobs-in-bangalore`), scroll to collect job cards, open each in a new tab for full description, save all to `data/job_applications.xlsx`, then close the browser.
   - **Phase 2**: Read the Excel, send each job description + your resume to Gemini for scoring, generate tailored resume PDFs for jobs scoring ≥ 75, and update the Excel with scores and PDF paths.

2. **Review results** — open the Excel file:
   ```powershell
   start data\job_applications.xlsx
   ```

3. **Review tailored resumes** — check generated PDFs:
   ```powershell
   dir output\resumes\
   ```

## Customisation

| Flag | Default | Description |
|---|---|---|
| `--title` | `"Data Scientist"` | Job title to search |
| `--location` | `"Bangalore"` | City / region |
| `--max-jobs` | `25` | Max jobs to scrape |
| `--resume` | hardcoded default | Path to your `.txt` resume |
| `--threshold` | `75` | Min score to generate tailored PDF |
| `--headless` | on | Run browser hidden |
| `--no-headless` | — | Show the browser (for debugging / CAPTCHA) |

## Troubleshooting

- **No jobs found**: Use `--no-headless` to see the browser. Check if Naukri is showing popups.
- **Scores are 0**: Check that `GEMINI_API_KEY` is valid in `.env`.
- **Browser crashes**: Ensure Chrome is up to date.

## How It Differs from LinkedIn

Both portals follow the **exact same scraping pattern**:
1. Navigate to public search URL (no login)
2. Scroll to load cards + dismiss overlays
3. Extract card-level metadata (title, company, location, link)
4. Open each job in a new tab for full description
5. Paginate via scroll + show-more / next-page buttons

The only difference is the **CSS selectors** and **URL format** (Naukri uses slug-based URLs like `data-scientist-jobs-in-bangalore`).
