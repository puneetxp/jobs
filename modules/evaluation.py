"""
AI Evaluation & Scoring Module
===============================
Passes a scraped Job Description and base resume to the configured LLM,
returning a structured JSON assessment with match_score, missing_skills,
and readiness_assessment tailored for a 3-year professional transitioning
from a testing background into Data Science / ML.
"""

import json
import re
import time

from modules.llm import call_llm

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are an expert career-matching assistant specialising in Data Science
and Machine Learning roles.  You will receive two inputs:
1. A **Job Description**.
2. A **Base Resume**.

The candidate has **3 years of professional experience** and is
**transitioning from a software testing / QA background** into Data Science
and Machine Learning.

Analyse how well the resume matches the job and return **ONLY** a valid JSON
object (no markdown fences, no commentary) with exactly these keys:

{
  "match_score": <integer 1-100>,
  "missing_skills": [<list of skill strings the candidate lacks for THIS role>],
  "readiness_assessment": "<A brief 2-3 sentence note on how well a candidate
    with 3 years of professional experience, transitioning from a testing
    background, fits this specific role. Mention concrete strengths and gaps.>"
}

Rules:
- match_score must be an integer between 1 and 100.
- missing_skills must be a JSON array of strings (may be empty).
- readiness_assessment must be a single string, not a list.
- Do NOT include any text outside the JSON object.
"""


def _build_user_prompt(job_description: str, base_resume: str) -> str:
    return (
        f"### Job Description\n{job_description}\n\n"
        f"### Base Resume\n{base_resume}"
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def evaluate_job(
    job_description: str,
    base_resume: str,
    provider: str = "gemini",
) -> dict:
    """
    Send a job description + resume to the LLM and return a parsed dict.

    Parameters
    ----------
    provider : str
        ``"gemini"`` or ``"groq"``.

    Returns
    -------
    dict
        {
            "match_score": int,
            "missing_skills": list[str],
            "readiness_assessment": str
        }
    """
    delay = 3
    attempt = 1
    while True:
        try:
            raw_text = call_llm(
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=_build_user_prompt(job_description, base_resume),
                provider=provider,
            )
            parsed = _parse_json(raw_text)
            _validate(parsed)
            return parsed
        except Exception as e:
            # Check for an explicit required wait period from the Gemini API error
            match = re.search(r"Please retry in (\d+(?:\.\d+)?)s", str(e))
            if match:
                requested_delay = float(match.group(1)) + 1.5
                print(f"Gemini API rate limit matched! Requested wait: {match.group(1)}s. Sleeping for {requested_delay:.2f}s before retrying...")
                time.sleep(requested_delay)
            else:
                print(f"Gemini call failed (attempt {attempt}): {e}. Backing off for {delay} seconds...")
                time.sleep(delay)
                delay = min(60, delay * 2)  # Cap delay at 60s, but do NOT reset! Keep trying infinitely.
            attempt += 1


# ---------------------------------------------------------------------------
# Batch Evaluation Templates & Function
# ---------------------------------------------------------------------------

_BATCH_SYSTEM_PROMPT = """\
You are an expert career-matching assistant specialising in Data Science and Machine Learning roles.
You will receive:
1. A **Base Resume**.
2. A list of **Job Descriptions** to evaluate.

The candidate has **3 years of professional experience** and is **transitioning from a software testing / QA background** into Data Science and Machine Learning.

Analyse how well the resume matches EACH job description, and return **ONLY** a valid JSON array of objects (no markdown fences, no commentary, no backticks). The array must have exactly the same length as the list of job descriptions, containing objects in the same order, with exactly these keys for each job:

[
  {
    "match_score": <integer 1-100>,
    "missing_skills": [<list of skill strings the candidate lacks for THIS role>],
    "readiness_assessment": "<A brief 2-3 sentence note on how well a candidate with 3 years of professional experience, transitioning from a testing background, fits this specific role. Mention concrete strengths and gaps.>"
  },
  ...
]

Rules:
- match_score must be an integer between 1 and 100.
- missing_skills must be a JSON array of strings (may be empty).
- readiness_assessment must be a single string, not a list.
- Return ONLY the JSON array. Do NOT include any markdown or text outside the JSON block.
"""


def _build_batch_user_prompt(job_descriptions: list[str], base_resume: str) -> str:
    prompt = f"### Base Resume\n{base_resume}\n\n"
    for idx, jd in enumerate(job_descriptions):
        prompt += f"--- JOB #{idx + 1} ---\n{jd}\n\n"
    return prompt


def evaluate_jobs_batch(
    job_descriptions: list[str],
    base_resume: str,
    provider: str = "gemini",
) -> list[dict]:
    """
    Evaluate multiple job descriptions in a single API call to conserve API limits.
    """
    delay = 3
    attempt = 1
    while True:
        try:
            raw_text = call_llm(
                system_prompt=_BATCH_SYSTEM_PROMPT,
                user_prompt=_build_batch_user_prompt(job_descriptions, base_resume),
                provider=provider,
            )

            results = _parse_json(raw_text)
            if not isinstance(results, list):
                raise ValueError("Expected a JSON list response.")
            
            # Validate each item
            for r in results:
                _validate(r)
                
            # Ensure we have the same number of items
            while len(results) < len(job_descriptions):
                results.append({
                    "match_score": 0,
                    "missing_skills": [],
                    "readiness_assessment": "Parsing error: missing item in LLM response batch."
                })
            return results[:len(job_descriptions)]
        except Exception as e:
            # Check for an explicit required wait period from the Gemini API error
            match = re.search(r"Please retry in (\d+(?:\.\d+)?)s", str(e))
            if match:
                requested_delay = float(match.group(1)) + 1.5
                print(f"Gemini API rate limit matched! Requested wait: {match.group(1)}s. Sleeping for {requested_delay:.2f}s before retrying...")
                time.sleep(requested_delay)
            else:
                print(f"Gemini batch call failed (attempt {attempt}): {e}. Backing off for {delay} seconds...")
                time.sleep(delay)
                delay = min(60, delay * 2)  # Cap delay at 60s, but do NOT reset! Keep trying infinitely.
            attempt += 1



from typing import Any

# ---------------------------------------------------------------------------
# Draft Generation
# ---------------------------------------------------------------------------

_DRAFT_SYSTEM_PROMPT = """\
You are an expert career consultant. You will receive:
1. A **Job Description**.
2. A **Base Resume**.
3. (Optional) A list of **Screening Questions**.

The candidate has **3 years of professional experience** and is **transitioning from a software testing / QA background** into Data Science and Machine Learning.

Your task is to generate:
1. A professional, concise **Cover Letter** (max 250 words) that highlights how their testing experience (quality mindset, Python skills, automation) translates to Data Science/ML.
2. (If questions provided) Concise, persuasive **Screening Answers** for each question.

Return **ONLY** a valid JSON object with these keys:
{
  "cover_letter": "<the full cover letter text>",
  "screening_answers": {
    "Question Text 1": "Answer Text 1",
    ...
  }
}

Rules:
- Be honest but highly persuasive. Write answers in the first person ("I have...", "In my previous experience...").
- For open-ended text questions, write professional, compelling 1-2 sentence answers highlighting transferable skills (such as strong Python automation, data quality validation, systematic debugging, analytical rigor).
- For questions with a specified "Available Options" list, select the SINGLE most accurate and honest option text from that list as the answer value. Do NOT write full sentences or explain; just provide the exact option string.
- The cover letter should be ready to send.
- screening_answers keys must match the EXACT text of the question provided.
- Do NOT include any markdown formatting, backticks, or text outside the JSON object.
"""

def generate_draft_with_llm(
    job_description: str,
    base_resume: str,
    screening_questions: list[Any] | None = None,
    provider: str = "gemini",
) -> dict:
    """
    Generate a cover letter and screening answers using the LLM.
    """
    user_prompt = f"### Job Description\n{job_description}\n\n### Base Resume\n{base_resume}"
    if screening_questions:
        formatted_qs = []
        for q in screening_questions:
            if isinstance(q, dict):
                text = q.get("text") or q.get("questionName") or ""
                options = q.get("options")
                q_type = q.get("type")
                
                q_str = f"- Question: {text}"
                if q_type:
                    q_str += f" (Type: {q_type})"
                if options:
                    if isinstance(options, dict):
                        opt_list = list(options.values())
                    elif isinstance(options, list):
                        opt_list = options
                    else:
                        opt_list = []
                    if opt_list:
                        q_str += f"\n  Available Options: {opt_list}\n  [Rule: Select the single most accurate option from this list that best fits the candidate's resume/profile.]"
                formatted_qs.append(q_str)
            else:
                formatted_qs.append(f"- Question: {q}")
                
        user_prompt += "\n\n### Screening Questions\n" + "\n\n".join(formatted_qs)

    try:
        raw_text = call_llm(
            system_prompt=_DRAFT_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            provider=provider,
        )
        parsed = _parse_json(raw_text)
        
        # Ensure keys exist
        if "cover_letter" not in parsed:
            parsed["cover_letter"] = "Error generating cover letter."
        if "screening_answers" not in parsed:
            parsed["screening_answers"] = {}
            
        return parsed
    except Exception as e:
        print(f"Draft generation failed: {e}")
        return {
            "cover_letter": f"Error generating draft: {str(e)}",
            "screening_answers": {}
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_json(raw: str) -> dict:
    cleaned = raw.strip()
    
    # Remove markdown code blocks if present
    if cleaned.startswith("```"):
        first_line_end = cleaned.find("\n")
        if first_line_end != -1:
            cleaned = cleaned[first_line_end:].strip()
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3].strip()
        
    # Find the bounds of the JSON structure
    first_brace = cleaned.find("{")
    first_bracket = cleaned.find("[")
    
    start_idx = -1
    end_char = ""
    
    if first_brace != -1 and (first_bracket == -1 or first_brace < first_bracket):
        start_idx = first_brace
        end_char = "}"
    elif first_bracket != -1:
        start_idx = first_bracket
        end_char = "]"
        
    if start_idx != -1:
        end_idx = cleaned.rfind(end_char)
        if end_idx != -1:
            cleaned = cleaned[start_idx:end_idx + 1]
            
    try:
        return json.loads(cleaned, strict=False)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Could not parse LLM response as JSON.\nRaw:\n{raw}"
        ) from exc


def _validate(data: dict) -> None:
    required = {"match_score", "missing_skills", "readiness_assessment"}
    missing = required - set(data.keys())
    if missing:
        raise ValueError(f"Missing keys: {missing}\nReceived: {data}")

    if not isinstance(data["match_score"], (int, float)):
        raise ValueError(f"match_score must be a number, got {type(data['match_score'])}")
    if not isinstance(data["missing_skills"], list):
        raise ValueError(f"missing_skills must be a list, got {type(data['missing_skills'])}")
    if not isinstance(data["readiness_assessment"], str):
        raise ValueError(f"readiness_assessment must be str, got {type(data['readiness_assessment'])}")

    data["match_score"] = max(1, min(100, int(data["match_score"])))


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    sample_jd = (
        "We are hiring a Data Scientist with 2-4 years of experience. "
        "Must know Python, pandas, scikit-learn, SQL, and have experience "
        "with ML model deployment. Nice to have: deep learning, NLP, MLOps."
    )
    sample_resume = (
        "QA Engineer with 3 years of experience in automated testing using "
        "Python, Selenium, and Jenkins. Skilled in SQL, data analysis with "
        "pandas, and basic ML projects using scikit-learn. Completed "
        "certifications in Data Science and Machine Learning."
    )

    print("=" * 60)
    print("Evaluation Module — Smoke Test")
    print("=" * 60)

    result = evaluate_job(sample_jd, sample_resume, provider="groq")

    print(f"\nMatch Score        : {result['match_score']}")
    print(f"Missing Skills     : {result['missing_skills']}")
    print(f"Readiness          : {result['readiness_assessment']}")
    print("\n✅ Done.")
