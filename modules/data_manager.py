"""
Data Management Module
======================
Functions for saving raw scraped jobs, loading the Excel for evaluation,
and updating individual rows with AI results.
"""

from __future__ import annotations

import os
from datetime import datetime

import pandas as pd

# Cleaned up static DEFAULT_EXCEL_PATH. Path must be explicitly provided.

# ---------------------------------------------------------------------------
# Column definitions
# ---------------------------------------------------------------------------

# Written during the SCRAPE phase
_SCRAPE_COLUMNS = [
    "Timestamp",
    "Job Title",
    "Company",
    "Location",
    "Job Link",
    "Job Description",
]

# Added during the EVALUATE phase
_EVAL_COLUMNS = [
    "Match Score",
    "Missing Skills",
    "Readiness Assessment",
    "Resume PDF Path",
]

ALL_COLUMNS = _SCRAPE_COLUMNS + _EVAL_COLUMNS

# ---------------------------------------------------------------------------
# Phase A — SCRAPE: bulk-save raw jobs
# ---------------------------------------------------------------------------


def save_jobs_to_excel(
    jobs: list[dict],
    filepath: str,
) -> str:
    """
    Write all scraped jobs to a **new** Excel file (overwrites if exists).

    Parameters
    ----------
    jobs : list[dict]
        Each dict must have: job_title, company, location, job_link,
        job_description.

    Returns
    -------
    str   Absolute path to the saved file.
    """
    os.makedirs(os.path.dirname(filepath), exist_ok=True)

    rows = []
    ts = datetime.now().isoformat(timespec="seconds")
    for j in jobs:
        rows.append({
            "Timestamp": ts,
            "Job Title": j.get("job_title", ""),
            "Company": j.get("company", ""),
            "Location": j.get("location", ""),
            "Job Link": j.get("job_link", ""),
            "Job Description": j.get("job_description", ""),
            # Evaluation columns — blank for now
            "Match Score": "",
            "Missing Skills": "",
            "Readiness Assessment": "",
            "Resume PDF Path": "",
        })

    df = pd.DataFrame(rows, columns=ALL_COLUMNS)
    return _safe_write(df, filepath)


# ---------------------------------------------------------------------------
# Phase B — EVALUATE: load, update, save
# ---------------------------------------------------------------------------


def load_jobs_from_excel(filepath: str) -> pd.DataFrame:
    """Read the job Excel into a DataFrame."""
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Excel file not found: {filepath}")
    return pd.read_excel(filepath, engine="openpyxl")


def update_excel(df: pd.DataFrame, filepath: str) -> str:
    """Overwrite the Excel file with the updated DataFrame."""
    return _safe_write(df, filepath)


def _safe_write(df: pd.DataFrame, filepath: str) -> str:
    """
    Try to write the DataFrame to *filepath*. If the file is locked
    (PermissionError), create a new file with a timestamp suffix instead.
    """
    os.makedirs(os.path.dirname(filepath), exist_ok=True)

    try:
        df.to_excel(filepath, index=False, engine="openpyxl")
        return os.path.abspath(filepath)
    except PermissionError:
        base, ext = os.path.splitext(filepath)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fallback = f"{base}_{ts}{ext}"
        print(f"  ⚠️  Cannot write to {filepath} (file locked). "
              f"Saving to: {fallback}")
        df.to_excel(fallback, index=False, engine="openpyxl")
        return os.path.abspath(fallback)
