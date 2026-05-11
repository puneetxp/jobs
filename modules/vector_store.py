"""
Vector Store Module — ChromaDB + Semantic Job Matching
=======================================================
Embeds job descriptions and scores them against a candidate profile
using cosine similarity via ChromaDB's built-in embedding function.

No external embedding API needed — uses all-MiniLM-L6-v2 locally
(downloaded once by ChromaDB on first use, ~80MB).

Usage
-----
    from modules.vector_store import upsert_job, vector_score_job, clear_collection

    # After scraping each job:
    upsert_job(job_id="linkedin_abc123", description="Senior Python Engineer...", metadata={...})

    # To get a 0–100 similarity score against your profile:
    score = vector_score_job(job_id="linkedin_abc123", profile_text="FastAPI, Python, AI...")
"""

from __future__ import annotations

import hashlib
import os

import chromadb
from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Persist DB inside the project root (next to main.py)
_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "chroma_db")
_COLLECTION_NAME = "job_descriptions"

# ---------------------------------------------------------------------------
# Lazy singletons — initialised once on first call
# ---------------------------------------------------------------------------

_client: chromadb.PersistentClient | None = None
_collection = None


def _get_collection():
    """Lazily initialise ChromaDB client and collection."""
    global _client, _collection

    if _collection is not None:
        return _collection

    from chromadb.config import Settings

    _client = chromadb.PersistentClient(
        path=_DB_PATH,
        settings=Settings(anonymized_telemetry=False)
    )
    _collection = _client.get_or_create_collection(
        name=_COLLECTION_NAME,
        embedding_function=DefaultEmbeddingFunction(),  # all-MiniLM-L6-v2
        metadata={"hnsw:space": "cosine"},              # cosine similarity
    )
    return _collection


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def upsert_job(job_id: str, description: str, metadata: dict | None = None) -> None:
    """
    Embed a job description and store (or update) it in the vector DB.

    Parameters
    ----------
    job_id : str
        Unique identifier for the job (e.g. "linkedin_abc123" or the job URL).
    description : str
        Full job description text.
    metadata : dict, optional
        Extra fields stored alongside the vector (title, company, platform, etc.).
        Must contain only str / int / float / bool values — no lists or dicts.
    """
    if not description or len(description.strip()) < 20:
        return  # skip empty/useless descriptions

    collection = _get_collection()
    safe_id = _safe_id(job_id)

    collection.upsert(
        ids=[safe_id],
        documents=[description[:8000]],  # trim to avoid token limits
        metadatas=[metadata or {}],
    )


def vector_score_job(job_id: str, profile_text: str, top_k: int = 300) -> float:
    """
    Compute a 0–100 semantic similarity score for a single job vs. your profile.

    Works by querying the top_k most similar jobs and finding where this job
    ranks. Jobs not in the top_k get a score of 0.

    Parameters
    ----------
    job_id : str
        The same ID used in upsert_job().
    profile_text : str
        Your profile/skills summary text to compare against.
    top_k : int
        How many results to retrieve in the query (larger = more accurate ranking).

    Returns
    -------
    float
        Score between 0.0 and 100.0.
    """
    collection = _get_collection()
    safe_id = _safe_id(job_id)

    n_items = collection.count()
    if n_items == 0:
        return 0.0

    n_results = min(top_k, n_items)

    try:
        results = collection.query(
            query_texts=[profile_text],
            n_results=n_results,
            include=["distances"],
        )
    except Exception:
        return 0.0

    ids = results["ids"][0]
    distances = results["distances"][0]  # cosine distance: 0 = identical, 2 = opposite

    if safe_id not in ids:
        return 0.0

    idx = ids.index(safe_id)
    cosine_distance = distances[idx]

    # Convert cosine distance [0, 2] → similarity [0, 100]
    # distance 0 → score 100 | distance 1 → score 50 | distance 2 → score 0
    similarity = max(0.0, 1.0 - (cosine_distance / 2.0))
    return round(similarity * 100, 2)


def bulk_score_against_profile(profile_text: str, top_k: int = 300) -> dict[str, float]:
    """
    Score ALL jobs currently in the vector DB against a profile.

    Returns
    -------
    dict[str, float]
        Mapping of {job_id → score}. Only jobs in the top_k are returned;
        the rest have an implicit score of 0.
    """
    collection = _get_collection()
    n_items = collection.count()

    if n_items == 0:
        return {}

    n_results = min(top_k, n_items)

    try:
        results = collection.query(
            query_texts=[profile_text],
            n_results=n_results,
            include=["distances"],
        )
    except Exception:
        return {}

    scores: dict[str, float] = {}
    for job_id, distance in zip(results["ids"][0], results["distances"][0]):
        similarity = max(0.0, 1.0 - (distance / 2.0))
        scores[job_id] = round(similarity * 100, 2)

    return scores


def clear_collection() -> None:
    """
    Delete and recreate the collection (wipe all stored embeddings).
    Useful at the start of a fresh scrape run to avoid stale data.
    """
    global _collection

    if _client is None:
        _get_collection()

    try:
        _client.delete_collection(_COLLECTION_NAME)
    except Exception:
        pass

    _collection = _client.get_or_create_collection(
        name=_COLLECTION_NAME,
        embedding_function=DefaultEmbeddingFunction(),
        metadata={"hnsw:space": "cosine"},
    )
    print("🗑️  Vector store cleared.")


def collection_size() -> int:
    """Return the number of embeddings currently stored."""
    return _get_collection().count()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_id(job_id: str) -> str:
    """
    ChromaDB requires IDs to be non-empty strings with no special chars.
    Hash long or complex IDs to a safe 16-char hex string.
    """
    if len(job_id) <= 64 and job_id.replace("-", "").replace("_", "").isalnum():
        return job_id
    return hashlib.md5(job_id.encode()).hexdigest()
