"""
LLM Provider Module
====================
Centralised interface for calling LLMs.  Supports **Gemini** and **Groq**.

Provider is selected by the ``provider`` argument or auto-detected from
environment variables (``GEMINI_API_KEY`` → Gemini, ``GROQ_API_KEY`` → Groq).
"""

from __future__ import annotations

import os
import re
import time

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Supported providers & models
# ---------------------------------------------------------------------------

_GEMINI_MODEL = "gemini-3-flash-preview"
_GROQ_MODEL = "llama-3.3-70b-versatile"

# ---------------------------------------------------------------------------
# Lazy singletons (configured once on first use)
# ---------------------------------------------------------------------------

_gemini_client = None
_groq_client = None


def _init_gemini():
    global _gemini_client
    if _gemini_client is not None:
        return _gemini_client

    from google import genai

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "GEMINI_API_KEY is not set. "
            "Add it to your .env file or use --llm groq instead."
        )
    _gemini_client = genai.Client(api_key=api_key)
    return _gemini_client


def _init_groq():
    global _groq_client
    if _groq_client is not None:
        return _groq_client
    from groq import Groq

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "GROQ_API_KEY is not set. "
            "Get a free key at https://console.groq.com and add it to .env."
        )
    _groq_client = Groq(api_key=api_key)
    return _groq_client


# ---------------------------------------------------------------------------
# Auto-detect provider
# ---------------------------------------------------------------------------


def detect_provider() -> str:
    """Return 'gemini', 'groq', or 'bedrock' based on which API key is available."""
    if os.getenv("GEMINI_API_KEY"):
        return "gemini"
    if os.getenv("GROQ_API_KEY"):
        return "groq"
    if os.getenv("AWS_ACCESS_KEY_ID"):
        return "bedrock"
    raise EnvironmentError(
        "No LLM API key found. Set GEMINI_API_KEY, GROQ_API_KEY, or AWS_ACCESS_KEY_ID in .env."
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def call_llm(
    system_prompt: str,
    user_prompt: str,
    provider: str = "gemini",
) -> str:
    """
    Send a system + user prompt to the selected LLM and return the raw text.

    Parameters
    ----------
    system_prompt : str
        System / instruction prompt.
    user_prompt : str
        User message content.
    provider : str
        ``"gemini"``, ``"groq"``, or ``"bedrock"``.

    Returns
    -------
    str
        The model's response text.
    """
    provider = provider.lower()

    if provider == "gemini":
        return _call_gemini(system_prompt, user_prompt)
    elif provider == "groq":
        return _call_groq(system_prompt, user_prompt)
    elif provider == "bedrock":
        return _call_bedrock(system_prompt, user_prompt)
    else:
        raise ValueError(f"Unknown LLM provider '{provider}'. Use 'gemini', 'groq', or 'bedrock'.")


# ---------------------------------------------------------------------------
# Provider implementations
# ---------------------------------------------------------------------------


_GEMINI_FALLBACK_MODELS = [
    "gemini-2.0-flash",
    "gemini-2.5-flash",
    "gemini-2.0-flash-lite",
    "gemini-2.5-pro",
]


def _call_gemini(system_prompt: str, user_prompt: str) -> str:
    from google.genai import types

    client = _init_gemini()

    # Try main model first, followed by fallbacks on rate-limit/exhaustion
    models_to_try = [_GEMINI_MODEL] + [m for m in _GEMINI_FALLBACK_MODELS if m != _GEMINI_MODEL]
    
    last_err = None
    for model_name in models_to_try:
        delay = 3
        max_model_attempts = 3
        
        for attempt in range(max_model_attempts):
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=user_prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=system_prompt,
                    ),
                )
                return response.text.strip()
            except Exception as exc:
                err_str = str(exc).lower()
                last_err = exc
                
                # Detect hard daily limit (limit: 20 or GenerateRequestsPerDay) vs temporary per-minute rate limit (RPM)
                is_hard_limit = "limit: 20" in err_str or "day" in err_str or "daily" in err_str or "generaterequestsperday" in err_str
                
                if "quota" in err_str or "exhausted" in err_str or "limit" in err_str or "429" in err_str:
                    if is_hard_limit:
                        print(f"⚠️ Model '{model_name}' hit hard daily limit. Switching to next fallback model immediately...")
                        break  # Break retry loop to proceed to next model in the outer list
                        
                    # It is a temporary per-minute rate limit. Backoff and retry ON THE SAME MODEL!
                    if attempt < max_model_attempts - 1:
                        # Parse API recommended retry duration if available
                        match = re.search(r"Please retry in (\d+(?:\.\d+)?)s", str(exc))
                        requested_delay = float(match.group(1)) + 1.5 if match else delay
                        
                        print(f"⏳ Model '{model_name}' rate limited (attempt {attempt+1}/{max_model_attempts}). Sleeping {requested_delay:.2f}s before retry...")
                        time.sleep(requested_delay)
                        delay = min(60, delay * 2)
                    else:
                        print(f"⚠️ Model '{model_name}' exhausted temporary retries. Switching to next fallback...")
                else:
                    # Generic structural error, raise immediately
                    raise RuntimeError(f"Gemini API call failed for {model_name}: {exc}") from exc

    # If all models have been exhausted
    raise RuntimeError(f"All Gemini models exhausted. Last model error: {last_err}")


def _call_groq(system_prompt: str, user_prompt: str) -> str:
    client = _init_groq()

    try:
        response = client.chat.completions.create(
            model=_GROQ_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.7,
            max_tokens=4096,
        )
    except Exception as exc:
        raise RuntimeError(f"Groq API call failed: {exc}") from exc

    return response.choices[0].message.content.strip()


def _call_bedrock(system_prompt: str, user_prompt: str) -> str:
    import boto3

    aws_key = os.getenv("AWS_ACCESS_KEY_ID")
    aws_secret = os.getenv("AWS_SECRET_ACCESS_KEY")
    aws_region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"

    if aws_key and aws_secret:
        client = boto3.client(
            "bedrock-runtime",
            aws_access_key_id=aws_key,
            aws_secret_access_key=aws_secret,
            region_name=aws_region,
        )
    else:
        client = boto3.client("bedrock-runtime", region_name=aws_region)

    model_id = os.getenv("BEDROCK_MODEL_ID", "amazon.nova-lite-v1:0")

    # Amazon Nova models require routing via Bedrock Cross-Region Inference (CRI) profiles
    # in non-US regions or specific accounts. We auto-prepend us., eu., or apac. prefixes.
    if "amazon.nova" in model_id and not any(model_id.startswith(pref) for pref in ["us.", "eu.", "apac."]):
        if aws_region.startswith("us-"):
            model_id = f"us.{model_id}"
        elif aws_region.startswith("eu-"):
            model_id = f"eu.{model_id}"
        elif aws_region.startswith("ap-"):
            model_id = f"apac.{model_id}"

    try:
        response = client.converse(
            modelId=model_id,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"text": user_prompt}
                    ]
                }
            ],
            system=[
                {"text": system_prompt}
            ],
            inferenceConfig={
                "temperature": 0.7,
                "maxTokens": 4096
            }
        )
        return response["output"]["message"]["content"][0]["text"].strip()
    except Exception as exc:
        raise RuntimeError(f"Amazon Bedrock API call failed for {model_id}: {exc}") from exc
