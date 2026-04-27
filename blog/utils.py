"""Shared helpers for the blog app."""

import json
import math
import re
import urllib.error
import urllib.request

from django.conf import settings
from django.utils.html import strip_tags
from django.utils.text import slugify


GROQ_CHAT_COMPLETIONS_URL = "https://api.groq.com/openai/v1/chat/completions"
# Default matches Groq production models; override with env `GROQ_MODEL` if needed.
_DEFAULT_GROQ_MODEL = "llama-3.1-8b-instant"


class GroqGenerationError(Exception):
    """Raised when Groq returns an error or the response cannot be parsed."""


def _groq_chat_completion(messages: list, response_format: dict | None = None) -> dict:
    """
    Call Groq OpenAI-compatible chat completions API.
    Returns the parsed JSON body (dict).
    """
    api_key = (getattr(settings, "GROQ_API_KEY", "") or "").strip()
    if not api_key:
        raise GroqGenerationError("GROQ_API_KEY is not configured.")

    model = (getattr(settings, "GROQ_MODEL", "") or "").strip() or _DEFAULT_GROQ_MODEL

    body: dict = {
        "model": model,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 1200,
    }
    if response_format:
        body["response_format"] = response_format

    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        GROQ_CHAT_COMPLETIONS_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            # Some edge networks block urllib default UA; Groq/Cloudflare expect a normal client UA.
            "User-Agent": "HowToEarningMoneyBlog/1.0 (Django; +https://api.groq.com/)",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        err_text = e.read().decode("utf-8", errors="replace")
        try:
            err_json = json.loads(err_text)
            msg = err_json.get("error", {})
            if isinstance(msg, dict):
                msg = msg.get("message", err_text[:800])
            else:
                msg = str(msg)
        except json.JSONDecodeError:
            msg = err_text[:800] or str(e)
        raise GroqGenerationError(f"Groq API error ({e.code}): {msg}") from e
    except urllib.error.URLError as e:
        raise GroqGenerationError(f"Could not reach Groq API: {e}") from e

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise GroqGenerationError("Invalid JSON from Groq API.") from e


def _extract_message_text(completion: dict) -> str:
    try:
        return (completion["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError) as e:
        raise GroqGenerationError("Unexpected Groq response shape.") from e


def _parse_json_from_model_text(text: str) -> dict:
    """Parse JSON from model output; strip ```json fences if present."""
    t = (text or "").strip()
    if not t:
        raise GroqGenerationError("Empty model response.")
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.IGNORECASE).strip()
        t = re.sub(r"\s*```\s*$", "", t).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError as e:
        raise GroqGenerationError("Model did not return valid JSON.") from e


def _generate_blog_payload(title: str) -> dict:
    """
    Ask Groq for JSON: content (HTML), summary, faqs (3 strings "question|||answer").
    """
    title = (title or "").strip()
    if not title:
        raise GroqGenerationError("Title is required.")

    system = (
        "You are an expert SEO content writer. "
        "You write original, helpful blog articles in valid HTML for a rich text editor. "
        "Use semantic HTML: a short meta-oriented intro paragraph, then <h2>/<h3> headings, "
        "<p>, <ul>/<li> where useful, <strong> for emphasis. "
        "Include: introduction, multiple substantive sections with headings, a conclusion, "
        "and an FAQ section with exactly 3 question/answer pairs (visible in the HTML). "
        "Target the user-provided title; do not invent a different topic. "
        "Return ONLY a JSON object with keys: "
        '"content" (string, full article HTML), '
        '"summary" (string, 1-3 sentences meta description), '
        '"faqs" (array of exactly 3 strings; each string must be "question|||answer" with no newlines in the question).'
    )
    user = f'Blog title: "{title}"'

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    data = None
    last_err: GroqGenerationError | None = None
    for use_json_object in (True, False):
        try:
            completion = _groq_chat_completion(
                messages,
                response_format={"type": "json_object"} if use_json_object else None,
            )
            text = _extract_message_text(completion)
            data = _parse_json_from_model_text(text)
            break
        except GroqGenerationError as exc:
            last_err = exc
            if use_json_object:
                continue
            raise
    if data is None:
        raise last_err or GroqGenerationError("Groq generation failed.")

    content = (data.get("content") or "").strip()
    summary = (data.get("summary") or "").strip()
    faqs = data.get("faqs")

    if not content:
        raise GroqGenerationError("Generated content was empty.")
    if not isinstance(faqs, list):
        faqs = []
    faqs_out = []
    for item in faqs[:3]:
        if isinstance(item, str) and item.strip():
            faqs_out.append(item.strip())
    while len(faqs_out) < 3:
        faqs_out.append("|||")

    return {"content": content, "summary": summary, "faqs": faqs_out[:3]}


def generate_blog_content(title: str) -> str:
    """
    Generate SEO-oriented blog HTML from a title via Groq (Llama 3).
    Returns only the main article HTML (no wrapper JSON).
    """
    return _generate_blog_payload(title)["content"]


def generate_blog_structured(title: str) -> dict:
    """
    Same generation as generate_blog_content, but returns structured fields:
    { "content": str, "summary": str, "faqs": [str, str, str] }  (faqs: "question|||answer")
    """
    return _generate_blog_payload(title)


def ensure_unique_slug(model_class, base: str, instance=None) -> str:
    """
    Return a slug unique for `model_class` (checks the `slug` field).
    """
    root = slugify(base) or "item"
    candidate = root
    n = 1
    while True:
        qs = model_class.objects.filter(slug=candidate)
        if instance is not None and getattr(instance, "pk", None):
            qs = qs.exclude(pk=instance.pk)
        if not qs.exists():
            return candidate
        candidate = f"{root}-{n}"
        n += 1


def calculate_read_time(text: str, words_per_minute: int = 160) -> int:
    """
    Estimate read time from text using 160 WPM.
    Supports HTML content by stripping tags first.
    Always returns at least 1 minute.
    """
    if not text:
        return 1
    plain_text = strip_tags(text)
    words = re.findall(r"\w+", plain_text)
    total_words = len(words)
    minutes = math.ceil(total_words / words_per_minute)
    # Real-world reading includes pauses/skimming, so medium+ posts need a small buffer.
    if total_words >= 120:
        minutes += 1
    return max(1, minutes)


def calculate_read_time_minutes(content: str, words_per_minute: int = 160) -> int:
    """Backward-compatible wrapper; prefer calculate_read_time()."""
    return calculate_read_time(content, words_per_minute=words_per_minute)
