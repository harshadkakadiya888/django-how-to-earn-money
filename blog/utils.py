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


def _groq_chat_completion(
    messages: list,
    response_format: dict | None = None,
    max_tokens: int = 1200,
) -> dict:
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
        "max_tokens": max_tokens,
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


# Cap article text sent to tag generation to stay within context limits.
_TAG_SOURCE_MAX_CHARS = 12_000


def _content_word_set(text: str) -> set[str]:
    return {w for w in re.findall(r"\w+", (text or "").lower()) if w}


def _tag_tokens(tag: str) -> list[str]:
    """Words in a tag (splits on whitespace and common separators)."""
    s = re.sub(r"[-_/]+", " ", (tag or "").lower().strip())
    return [p for p in s.split() if p]


def _clean_seo_tags(raw_tags, title: str) -> list[str]:
    """
    Lowercase, drop empties, enforce max 2 words per tag, no duplicates in order,
    and drop any tag that shares a word with the title.
    """
    if not isinstance(raw_tags, list):
        return []
    title_words = _content_word_set((title or "").lower())
    seen: set[str] = set()
    out: list[str] = []
    for t in raw_tags:
        s = re.sub(r"\s+", " ", str(t).strip().lower())
        if not s:
            continue
        words = s.split()
        if len(words) > 2:
            s = " ".join(words[:2])
        if not s or s in seen:
            continue
        token_words = _tag_tokens(s)
        if title_words and any(w in title_words for w in token_words):
            continue
        if len(token_words) > 2:
            s = " ".join(token_words[:2])
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def generate_tags_from_content(content: str, title: str = "") -> dict:
    """
    Ask Groq for 5-8 SEO tags from the article body (not the title), tuned for
    common search phrasing, natural language, specificity, and real-world topics.

    Returns: { "tags": [str, ...] } with tags cleaned per app rules.
    """
    text = (content or "").strip()
    if not text:
        return {"tags": []}
    title = (title or "").strip()
    plain = re.sub(r"\s+", " ", strip_tags(text)).strip()
    if not plain:
        return {"tags": []}
    snippet = plain[:_TAG_SOURCE_MAX_CHARS]

    system = (
        "You extract SEO topic tags for a blog post from the ARTICLE TEXT only.\n"
        "Ignore any blog title; do not use words that appear in the given title list.\n\n"
        "Every tag should read like a phrase people commonly search for on Google:\n"
        "- Use natural, human keywords the way real readers and searchers phrase them, not robot-like labels or marketing filler.\n"
        "- Be clear and specific to this article: name real topics, problems, tools, or ideas from the text, not vague words in isolation (e.g. do not use bare 'tips' or 'guide' unless part of a concrete 2-word phrase that matches the content).\n"
        "- Stay grounded in real-world subjects that the article actually discusses; prefer terms that appear in or are clearly implied by the text.\n\n"
        "Technical rules:\n"
        "- 5 to 8 tags.\n"
        "- Do NOT use or repeat any word from the blog title (for exclusion; title is provided only so you can avoid its words).\n"
        "- At most 2 words per tag, lowercase, spaces between words, no duplicate tags.\n"
        "- Avoid odd jargon, unnecessary buzzword stacks, or synthetic-sounding coinages.\n\n"
        "Return ONLY valid JSON: { \"tags\": [ \"first tag\", \"second tag\" ] }"
    )
    user = f'Blog title (for exclusion, do not use these words in tags): "{title}"\n\nArticle text:\n{snippet}'

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
                max_tokens=500,
            )
            model_text = _extract_message_text(completion)
            data = _parse_json_from_model_text(model_text)
            break
        except GroqGenerationError as exc:
            last_err = exc
            if use_json_object:
                continue
            raise
    if data is None:
        raise last_err or GroqGenerationError("Groq tag generation failed.")

    raw = data.get("tags", [])
    return {"tags": _clean_seo_tags(raw, title)}


def _normalize_faq_to_pipe_string(item) -> str | None:
    """
    Turn one FAQ from Groq (string or dict) into 'question|||answer'.
    Returns None if the item is empty; returns the string unchanged if already pipe-shaped.
    """
    if item is None:
        return None
    if isinstance(item, str):
        t = item.strip()
        if not t:
            return None
        return t
    if isinstance(item, dict):
        q = (item.get("question") or item.get("q") or "").strip()
        a = (item.get("answer") or item.get("a") or "").strip()
        if not q and not a:
            return None
        return f"{q}|||{a}"
    return None


def _generate_blog_payload(title: str) -> dict:
    """
    Ask Groq for JSON: content (HTML), summary, faqs (3 strings "question|||answer").
    SEO tags are generated in a follow-up call from the body text.
    """
    title = (title or "").strip()
    if not title:
        raise GroqGenerationError("Title is required.")

    system = (
    "You are an expert SEO content writer.\n\n"
    "Write a high-quality blog article in HTML format.\n"
    "Include exactly 3 useful FAQ items about the article topic.\n\n"
    "Format for 'faqs': a JSON array of 3 items. Each item is EITHER:\n"
    "- A string: the question, then the three characters |||, then the answer, e.g. "
    '"What is X?|||X is ..."\n'
    "- Or an object: { \"question\": \"...\", \"answer\": \"...\" }\n\n"
    "Return ONLY JSON:\n"
    '{ "content": "...", "summary": "...", "faqs": [] }'
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
    for item in faqs:
        if len(faqs_out) >= 3:
            break
        pipe = _normalize_faq_to_pipe_string(item)
        if pipe:
            faqs_out.append(pipe)
    while len(faqs_out) < 3:
        faqs_out.append("|||")

    try:
        tag_payload = generate_tags_from_content(content, title=title)
        tags = tag_payload.get("tags", [])
    except GroqGenerationError:
        tags = []
    if not isinstance(tags, list):
        tags = []

    return {
    "content": content,
    "summary": summary,
    "faqs": faqs_out[:3],
    "tags": tags
    }


def generate_blog_content(title: str) -> str:
    """
    Generate SEO-oriented blog HTML from a title via Groq (Llama 3).
    Returns only the main article HTML (no wrapper JSON).
    """
    return _generate_blog_payload(title)["content"]


def generate_blog_structured(title: str) -> dict:
    """
    Same generation as generate_blog_content, but returns structured fields:
    { "content": str, "summary": str, "faqs": [str, str, str], "tags": [str, ...] }
    (faqs: "question|||answer"; tags are derived from the generated body, not the title)
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
