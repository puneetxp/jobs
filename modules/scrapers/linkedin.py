"""
LinkedIn Stealth Scraper — No Login Required
=============================================
Navigates to LinkedIn's **public guest** job search, scrapes job cards,
and extracts full details without any account authentication.
"""

from __future__ import annotations

import re
import requests
from urllib.parse import quote

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

GUEST_SEARCH_URL = (
    "https://www.linkedin.com/jobs/search/"
    "?keywords={keywords}&location={location}"
)

def _search_jobs(driver, job_title: str, location: str, max_jobs: int, past_24_hours: bool = True, progress_callback: callable = None) -> list[dict]:
    url = GUEST_SEARCH_URL.format(
        keywords=quote(job_title),
        location=quote(location),
    )
    if past_24_hours:
        url += "&f_TPR=r86400"
        
    driver.page.goto(url)
    human_delay(3, 5)

    if is_login_required(driver):
        if not driver.headless:
            msg = "⚠️ LinkedIn login required! Please log in in the browser window..."
            print(msg)
            if progress_callback:
                progress_callback(msg)
            if not wait_for_login(driver):
                return []
        else:
            msg = "⚠️ LinkedIn is asking for login! Please run once with HEADLESS=false to log in manually."
            print(msg)
            if progress_callback:
                progress_callback(msg)
            return []

    dismiss_overlays(driver)

    jobs: list[dict] = []
    seen_links: set[str] = set()
    scroll_attempts = 0
    max_scroll_attempts = 15
    no_new_jobs_streak = 0

    while len(jobs) < max_jobs and scroll_attempts < max_scroll_attempts:
        previous_job_count = len(jobs)
        
        cards = driver.page.query_selector_all(
            "ul.jobs-search__results-list li, "
            "div.base-card, "
            "div.job-search-card, "
            "li.jobs-search-results__list-item, "
            "ul.scaffold-layout__list-container li, "
            "div.job-card-container, "
            "div[data-job-id]"
        )

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
            if no_new_jobs_streak >= 3:
                print(f"  [LinkedIn] On portal there are fewer jobs ({len(jobs)}) than the max jobs ({max_jobs}) given in command. Moving to next pipeline.")
                break
        else:
            no_new_jobs_streak = 0

        # Scroll page or results list panel
        list_container = driver.page.query_selector(".jobs-search-results-list, div[class*='results-list']")
        if list_container:
            list_container.evaluate("el => el.scrollBy(0, 800);")
        else:
            scroll_page(driver, pixels=800)
            
        scroll_attempts += 1
        human_delay(1.5, 2.5)

        dismiss_overlays(driver)
        _click_show_more(driver)

    return jobs

def _extract_card(driver, card, seen_links: set) -> dict | None:
    link_el = card.query_selector("a.base-card__full-link, a.job-card-container__link, a.job-card-list__title, a.disabled.ember-view")
    if not link_el:
        link_el = card.query_selector("a")
    if not link_el:
        return None

    job_link = link_el.get_attribute("href")
    if not job_link:
        return None

    job_link = job_link.split("?")[0]
    job_link = re.sub(r"https?://[a-z]{2,3}\.linkedin\.com", "https://www.linkedin.com", job_link)
    
    if job_link.startswith("/"):
        job_link = "https://www.linkedin.com" + job_link

    if job_link in seen_links:
        return None
    seen_links.add(job_link)

    job_title = _text_from(
        card, 
        "h3.base-search-card__title, "
        "span.sr-only, "
        "h3.job-search-card__title, "
        ".artdeco-entity-lockup__title, "
        "a.job-card-list__title, "
        ".job-card-list__title, "
        "span[id*='job-title']"
    )
    
    company = _text_from(
        card, 
        "h4.base-search-card__subtitle, "
        "a.hidden-nested-link, "
        "h4.job-search-card__company-name, "
        ".job-card-container__primary-description, "
        "a.job-card-container__company-name, "
        ".artdeco-entity-lockup__subtitle, "
        ".job-card-container__company-link, "
        ".job-card-container__company-name, "
        "div.artdeco-entity-lockup__subtitle"
    )
    
    location = _text_from(
        card, 
        "span.job-search-card__location, "
        "span.base-search-card__metadata, "
        "li.job-card-container__metadata-item, "
        ".job-card-container__metadata-item, "
        ".job-card-container__metadata-wrapper li, "
        "div.artdeco-entity-lockup__caption, "
        "span.job-card-container__metadata-item"
    )

    job_description = _get_full_description(driver, job_link)

    return {
        "job_title": _clean(job_title),
        "company": _clean(company),
        "location": _clean(location),
        "job_link": job_link,
        "job_description": _clean(job_description),
    }

def _get_full_description(driver, job_link: str) -> str:
    job_id = None
    match = re.search(r'(?:view/|currentJobId=|jobs/view/.*?)(-?\d+)', job_link)
    if match:
        job_id = match.group(1)
    else:
        match = re.search(r'\b\d{10}\b', job_link)
        if match:
            job_id = match.group(0)

    if job_id:
        # Try fetching via requests first (MUCH faster)
        target_url = f"https://www.linkedin.com/jobs-guest/jobs/api/jobListing/{job_id}"
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
            }
            resp = requests.get(target_url, headers=headers, timeout=5)
            if resp.status_code == 200:
                # Basic cleaning of HTML
                text = re.sub(r'<[^>]+>', ' ', resp.text)
                text = re.sub(r'\s+', ' ', text).strip()
                if text and len(text) > 100:
                    return text
        except Exception:
            pass

    # Fallback to Playwright if requests fail or no job_id
    try:
        new_page = driver.context.new_page()
        new_page.goto(job_link, wait_until="domcontentloaded", timeout=15000)
        human_delay(1.5, 3)

        dismiss_overlays(new_page)
        _click_show_more_description(new_page)

        desc = _get_expanded_description_text(new_page)
        new_page.close()
        return desc if desc else "No description available."
    except Exception:
        try:
            new_page.close()
        except:
            pass
        return "No description available."

def _click_show_more_description(page) -> None:
    show_more_selectors = [
        "button.show-more-less-html__button--more",
        "button[aria-label='Show more']",
        "button.show-more-less-html__button",
    ]
    for sel in show_more_selectors:
        try:
            btn = page.query_selector(sel)
            if btn:
                btn.click()
                human_delay(1.0, 2.0)
                return
        except Exception:
            continue

def _get_expanded_description_text(page) -> str:
    desc_selectors = [
        "div.show-more-less-html__markup",
        "div.description__text",
        "section.show-more-less-html",
        "div.jobs-description__content",
    ]
    for sel in desc_selectors:
        try:
            el = page.query_selector(sel)
            if el:
                text = el.inner_text() or ""
                text = text.strip()
                if text:
                    return text
        except Exception:
            continue
    return "No description available."

def _click_show_more(driver) -> None:
    selectors = [
        "button.infinite-scroller__show-more-button",
        "button[aria-label='See more jobs']",
        "button.see-more-jobs",
    ]
    for sel in selectors:
        try:
            btn = driver.page.query_selector(sel)
            if btn:
                scroll_into_view(driver, btn)
                btn.click()
                human_delay(1.5, 3.0)
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

def _clean(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    # Remove common badges/verification suffixes
    text = re.sub(r"\bwith verification\b", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"\bactively hiring\b", "", text, flags=re.IGNORECASE).strip()
    
    # De-duplicate repeating titles (e.g. "Title Title")
    words = text.split()
    if len(words) >= 4 and len(words) % 2 == 0:
        half = len(words) // 2
        if words[:half] == words[half:]:
            text = " ".join(words[:half])
    else:
        # Check by characters as well (ignoring case/whitespace)
        chars_clean = "".join(words).lower()
        half_chars = len(chars_clean) // 2
        if len(chars_clean) > 4 and len(chars_clean) % 2 == 0 and chars_clean[:half_chars] == chars_clean[half_chars:]:
            text = text[:len(text)//2].strip()
            
    return text.strip()

def scrape_linkedin(
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

    print(f"\n🔍 [LinkedIn] Searching for '{job_title}' in '{location}' "
          f"(max {max_jobs}, headless={headless})…")

    try:
        jobs = _search_jobs(driver, job_title, location, max_jobs, past_24_hours, progress_callback)
    except Exception:
        if own_driver:
            force_quit_driver(driver)
        raise

    print(f"✅ [LinkedIn] Scraped {len(jobs)} job(s).")
    return jobs, driver

def accept_linkedin_invitations(headless: bool = False, progress_callback: callable = None) -> dict:
    """
    Opens LinkedIn, navigates to the invitation manager page, 
    and automatically accepts all pending connection requests.
    """
    from modules.browser import (
        create_stealth_driver,
        is_login_required,
        wait_for_login,
        force_quit_driver,
        human_delay,
        dismiss_overlays,
    )
    import time
    
    driver = create_stealth_driver(headless=headless)
    try:
        msg = "Navigating to LinkedIn Invitation Manager..."
        print(msg)
        if progress_callback:
            progress_callback(msg)
            
        driver.page.goto("https://www.linkedin.com/mynetwork/invitation-manager/")
        time.sleep(4)
        
        # Check login
        if is_login_required(driver):
            msg = "⚠️ LinkedIn login required! Waiting for manual login..."
            print(msg)
            if progress_callback:
                progress_callback(msg)
            if not wait_for_login(driver, timeout_minutes=3):
                return {"success": False, "message": "Login timeout or failed."}
        
        dismiss_overlays(driver)
        driver.page.goto("https://www.linkedin.com/mynetwork/invitation-manager/")
        time.sleep(4)
        
        # We find accept buttons
        accept_selectors = [
            "button:has-text('Accept')",
            "button[aria-label*='Accept invitation from']",
            "button.invitation-card__action-btn:has-text('Accept')",
            "button.artdeco-button:has-text('Accept')",
            ".artdeco-button__text:text-is('Accept')"
        ]
        
        accepted_count = 0
        max_attempts = 3
        
        for attempt in range(max_attempts):
            dismiss_overlays(driver)
            
            # Find visible accept buttons
            buttons = []
            for sel in accept_selectors:
                try:
                    found = driver.page.query_selector_all(sel)
                    for btn in found:
                        if btn.is_visible() and btn not in buttons:
                            buttons.append(btn)
                except:
                    pass
            
            if not buttons:
                # No buttons found, try scrolling once to see if more load
                driver.page.evaluate("window.scrollBy(0, 500);")
                time.sleep(2)
                
                # Check again
                for sel in accept_selectors:
                    try:
                        found = driver.page.query_selector_all(sel)
                        for btn in found:
                            if btn.is_visible() and btn not in buttons:
                                buttons.append(btn)
                    except:
                        pass
                        
                if not buttons:
                    break
            
            msg = f"Found {len(buttons)} pending invitation(s) to accept."
            print(msg)
            if progress_callback:
                progress_callback(msg)
                
            for btn in buttons:
                try:
                    # Let's get parent name if possible for nice logs
                    card_text = ""
                    try:
                        parent = btn.evaluate_handle("el => el.closest('.invitation-card')")
                        if parent:
                            card_text = parent.evaluate("el => el.innerText") or ""
                            # Extract name (usually first non-empty line of the card text)
                            lines = [l.strip() for l in card_text.split("\n") if l.strip()]
                            if lines:
                                card_text = lines[0]
                    except:
                        pass
                    
                    # Highlight the button or scroll to it
                    try:
                        btn.scroll_into_view_if_needed()
                    except:
                        pass
                        
                    btn.click()
                    accepted_count += 1
                    
                    person_info = f" from '{card_text}'" if card_text else ""
                    msg = f"  [{accepted_count}] Accepted invitation{person_info}!"
                    print(msg)
                    if progress_callback:
                        progress_callback(msg)
                        
                    human_delay(1.5, 3.0) # Safe human-like delay
                except Exception as click_err:
                    print(f"Error clicking accept button: {click_err}")
                    
            # Wait a bit after batch
            time.sleep(2)
            
        return {
            "success": True,
            "message": f"Successfully accepted {accepted_count} LinkedIn invitation(s)!",
            "count": accepted_count
        }
        
    except Exception as e:
        msg = f"Error accepting invitations: {str(e)}"
        print(msg)
        if progress_callback:
            progress_callback(msg)
        return {"success": False, "message": msg}
    finally:
        force_quit_driver(driver)
