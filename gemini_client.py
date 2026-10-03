import os
import random
import time
from threading import Lock

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()
API_KEY = os.getenv("GEMINI_API_KEY")
if not API_KEY:
    raise ValueError("GEMINI_API_KEY not found in .env")

# Disable SDK automatic retries so one failure cannot silently multiply calls.
client = genai.Client(
    api_key=API_KEY,
    http_options=types.HttpOptions(
        retry_options=types.HttpRetryOptions(attempts=1)
    ),
)

_LOCK = Lock()
_LAST_REQUEST = 0.0
MIN_INTERVAL = float(os.getenv("GEMINI_MIN_INTERVAL", "3.0"))
MAX_RETRIES_PER_MODEL = int(os.getenv("GEMINI_RETRIES", "3"))


def _pace():
    global _LAST_REQUEST
    with _LOCK:
        now = time.monotonic()
        wait = MIN_INTERVAL - (now - _LAST_REQUEST)
        if wait > 0:
            time.sleep(wait)
        _LAST_REQUEST = time.monotonic()


def is_retryable(exc):
    code = getattr(exc, "code", None)
    if code in (408, 429, 499, 500, 502, 503, 504):
        return True
    text = str(exc).upper()
    return any(x in text for x in (
        "429", "500", "502", "503", "504", "UNAVAILABLE",
        "RESOURCE_EXHAUSTED", "TIMEOUT", "TIMED OUT",
    ))


def looks_like_daily_quota(exc):
    text = str(exc).lower()
    return any(x in text for x in (
        "daily quota", "quota exceeded", "quota_exceeded",
        "per day", "daily limit", "limit: 0",
    ))


def call_with_retry(fn, operation="Gemini request", models=None):
    """Run fn(model) with bounded retries, jitter and compatible-model fallback."""
    models = list(models or [])
    if not models:
        raise ValueError("At least one Gemini model is required.")

    last_exc = None
    for model in models:
        for attempt in range(MAX_RETRIES_PER_MODEL + 1):
            try:
                _pace()
                return fn(model)
            except Exception as exc:
                last_exc = exc
                if not is_retryable(exc):
                    raise RuntimeError(f"{operation} failed on {model}: {exc}") from exc

                # A true daily quota cannot be fixed by sleeping for seconds.
                if looks_like_daily_quota(exc):
                    print(f"⚠️ {operation}: quota hit on {model}; trying fallback model.")
                    break

                if attempt >= MAX_RETRIES_PER_MODEL:
                    print(f"⚠️ {operation}: {model} unavailable; trying fallback model.")
                    break

                delay = min(45.0, 3.0 * (2 ** attempt)) + random.uniform(0, 2)
                print(
                    f"⚠️ {operation}: transient error on {model}; "
                    f"retry {attempt + 1}/{MAX_RETRIES_PER_MODEL + 1} in {delay:.1f}s"
                )
                time.sleep(delay)

    raise RuntimeError(
        f"{operation} failed after all configured models. Last error: {last_exc}"
    ) from last_exc
