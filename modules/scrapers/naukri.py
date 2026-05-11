"""
Naukri Stealth Scraper — No Login Required
==========================================
Navigates to Naukri.com's **public** job search pages, scrapes job cards,
and extracts full details without any account authentication.
"""

from __future__ import annotations

import re
import urllib.parse

from modules.browser import (
    create_stealth_driver,
    dismiss_overlays,
    force_quit_driver,
    human_delay,
    is_login_required,
    scroll_into_view,
    scroll_page,
    wait_for_login,
)

SEARCH_URL = "https://www.naukri.com/{slug}-jobs-in-{location}"

def _build_search_url(job_title: str, location: str, past_24_hours: bool = True) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", job_title.lower()).strip("-")
    
    k_param = urllib.parse.quote(job_title)
    
    raw_locs = [l.strip() for l in location.split(",")]
    remote_aliases = ("remote", "work from home", "wfh")
    
    cities = [l for l in raw_locs if l.lower() not in remote_aliases]
    is_remote = any(l.lower() in remote_aliases for l in raw_locs)
    
    url = f"https://www.naukri.com/{slug}-jobs"
    
    if cities:
        first_city = cities[0]
        loc_slug = re.sub(r"[^a-z0-9]+", "-", first_city.lower()).strip("-")
        url += f"-in-{loc_slug}"
        
    url += f"?k={k_param}"
    
    if cities:
        l_param = urllib.parse.quote(", ".join(cities))
        url += f"&l={l_param}"
        
    if past_24_hours:
        url += "&jobAge=1"
    
    if is_remote:
        url += "&wfhType=2"
        
    return url

def _search_jobs(driver, job_title: str, location: str, max_jobs: int, past_24_hours: bool = True, progress_callback: callable = None) -> list[dict]:
    url = _build_search_url(job_title, location, past_24_hours)
    driver.page.goto(url)
    human_delay(3, 5)

    if is_login_required(driver):
        if not driver.headless:
            msg = "⚠️ Naukri login required! Please log in in the browser window..."
            print(msg)
            if progress_callback:
                progress_callback(msg)
            if not wait_for_login(driver):
                return []
        else:
            msg = "⚠️ Naukri is asking for login! Please run once with HEADLESS=false to log in manually."
            print(msg)
            if progress_callback:
                progress_callback(msg)
            return []

    dismiss_overlays(driver)

    jobs: list[dict] = []
    seen_links: set[str] = set()
    scroll_attempts = 0
    max_scroll_attempts = 20
    no_new_jobs_streak = 0

    while len(jobs) < max_jobs and scroll_attempts < max_scroll_attempts:
        previous_job_count = len(jobs)
        
        cards = driver.page.query_selector_all("article.jobTuple, div.srp-jobtuple-wrapper, div.cust-job-tuple, div.list > div.jobTuple")

        if not cards and scroll_attempts == 0:
            print("  [Naukri] No job cards found on page.")
            break

        for card in cards:
            if len(jobs) >= max_jobs:
                break

            try:
                job_data = _extract_card(driver, card, seen_links)
                if job_data:
                    jobs.append(job_data)
                    msg = f"  [{len(jobs)}/{max_jobs}] {job_data['job_title']} @ {job_data['company']}"
                    print(msg)
                    if progress_callback:
                        progress_callback(msg)
            except Exception as exc:
                print(f"  ⚠️  Skipping card — {exc}")

        if len(jobs) == previous_job_count:
            no_new_jobs_streak += 1
            if no_new_jobs_streak >= 2:
                print(f"  [Naukri] On portal there are fewer jobs ({len(jobs)}) than the max jobs ({max_jobs}) given in command. Moving to next pipeline.")
                break
        else:
            no_new_jobs_streak = 0

        scroll_page(driver, pixels=800)
        scroll_attempts += 1
        human_delay(1.5, 2.5)

        dismiss_overlays(driver)
        _click_show_more_or_next(driver)

    return jobs

def _extract_card(driver, card, seen_links: set) -> dict | None:
    link_el = card.query_selector("a.title, a[class*='title']")
    if not link_el:
        link_el = card.query_selector("a")
    if not link_el:
        return None

    job_link = link_el.get_attribute("href")
    if not job_link:
        return None

    job_link = job_link.split("?")[0]

    if job_link in seen_links:
        return None
    seen_links.add(job_link)

    job_title = _text_from(card, "a.title, .row1 a, .desig")
    company = _text_from(card, ".comp-name, .subTitle a, .companyInfo a")
    location = _text_from(card, ".loc, .locWdth, .location span, .loc-wrap span")

    job_description = _get_full_description(driver, job_link)

    return {
        "job_title": _clean(job_title),
        "company": _clean(company),
        "location": _clean(location),
        "job_link": job_link,
        "job_description": _clean(job_description),
    }

def _get_full_description(driver, job_link: str) -> str:
    try:
        new_page = driver.context.new_page()
        new_page.goto(job_link, wait_until="domcontentloaded", timeout=15000)
        human_delay(1.5, 3)

        dismiss_overlays(new_page)

        desc = _text_or_default(
            new_page,
            "div.job-desc, "
            "section.job-desc, "
            "div.styles_JDC__dang-inner-html, "
            "div[class*='dang-inner-html'], "
            "div.jd-desc",
            default="No description available.",
        )
        new_page.close()
        return desc

    except Exception:
        return "No description available."

def _click_show_more_or_next(driver) -> None:
    selectors = [
        "button[class*='show-more']",
        "button[class*='load-more']",
        "a.fright.fs14.btn-secondary.br2",
        "a[class*='btn-secondary'][href]",
        "a.styles_btn-secondary",
    ]
    for sel in selectors:
        try:
            btn = driver.page.query_selector(sel)
            if btn:
                scroll_into_view(driver, btn)
                btn.click()
                human_delay(1.5, 3.0)
                dismiss_overlays(driver)
                return
        except Exception:
            continue

def _text_from(parent, css: str) -> str:
    for sel in css.split(","):
        try:
            el = parent.query_selector(sel.strip())
            if el:
                text = el.inner_text().strip()
                if text:
                    return text
        except Exception:
            continue
    return "N/A"

def _text_or_default(page, css: str, default: str = "N/A") -> str:
    for sel in css.split(","):
        try:
            el = page.query_selector(sel.strip())
            if el:
                text = el.inner_text().strip()
                if text:
                    return text
        except Exception:
            continue
    return default

def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()

def scrape_naukri(
    job_title: str,
    location: str,
    max_jobs: int = 25,
    headless: bool = True,
    past_24_hours: bool = True,
    progress_callback: callable = None,
    driver=None,
) -> tuple[list[dict], object]:
    own_driver = driver is None
    if own_driver:
        driver = create_stealth_driver(headless=headless)

    print(f"\n🔍 [Naukri] Searching for '{job_title}' in '{location}' "
          f"(max {max_jobs}, headless={headless})…")

    try:
        jobs = _search_jobs(driver, job_title, location, max_jobs, past_24_hours, progress_callback)
    except Exception:
        if own_driver:
            force_quit_driver(driver)
        raise

    print(f"✅ [Naukri] Scraped {len(jobs)} job(s).")
    return jobs, driver
