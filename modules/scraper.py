"""
Web Scraper Module — Dispatcher
================================
Routes scrape requests to the appropriate platform-specific stealth scraper.
No login is performed — all scraping uses public URLs.
"""

from __future__ import annotations

from modules.scrapers.linkedin import scrape_linkedin
from modules.scrapers.naukri import scrape_naukri

_SCRAPERS = {
    "linkedin": scrape_linkedin,
    "naukri": scrape_naukri,
}


def scrape_jobs(
    platform: str,
    job_title: str,
    location: str,
    max_jobs: int = 25,
    headless: bool = True,
    past_24_hours: bool = True,
    driver=None,
) -> tuple[list[dict], object]:
    """
    Scrape job listings from a public job search page.

    Parameters
    ----------
    platform : str
        ``"linkedin"`` or ``"naukri"``.
    job_title : str
        The job title to search for (e.g. "Data Scientist").
    location : str
        Target city / region.
    max_jobs : int
        Cap on the number of jobs to return.
    headless : bool
        Run Chrome without a visible window.
    driver
        Optional pre-existing driver instance.

    Returns
    -------
    tuple[list[dict], driver]
        Each dict: ``{job_title, company, location, job_link, job_description}``.
        The driver is returned so the caller can reuse or quit it.
    """
    scraper_fn = _SCRAPERS.get(platform.lower())
    if scraper_fn is None:
        raise ValueError(
            f"Unknown platform '{platform}'. "
            f"Supported: {', '.join(_SCRAPERS.keys())}"
        )

    return scraper_fn(
        job_title=job_title,
        location=location,
        max_jobs=max_jobs,
        headless=headless,
        past_24_hours=past_24_hours,
        driver=driver,
    )
