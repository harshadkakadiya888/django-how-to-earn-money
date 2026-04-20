"""Shared helpers for the blog app."""

import math
import re

from django.utils.html import strip_tags
from django.utils.text import slugify


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
