"""Search metadata excerpts; full editorial titles and content stay intact."""

from __future__ import annotations

import re


BRAND = "Aperture Intelligence"
TITLE_LIMIT = 65
DESCRIPTION_LIMIT = 160


def _plain_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    excerpt = text[: limit - 1]
    # Prefer a complete sentence or clause when it retains enough context.
    boundaries = list(re.finditer(r"[。！？!?；;]|[.](?=\s|$)", excerpt))
    if boundaries and boundaries[-1].end() >= limit // 2:
        return excerpt[: boundaries[-1].end()].strip()
    # Avoid cutting an English word; CJK text can be shortened by code point.
    if text[limit - 2].isascii() and text[limit - 1].isascii():
        space = excerpt.rfind(" ")
        if space >= limit // 2:
            excerpt = excerpt[:space]
    return excerpt.rstrip(" ，,、：:；;") + "…"


def search_title(value: object) -> str:
    title = _plain_text(value)
    suffix = f" · {BRAND}"
    if title.endswith(suffix):
        headline = title[: -len(suffix)]
        if len(title) > TITLE_LIMIT or BRAND in headline:
            title = headline
    return _clip(title, TITLE_LIMIT)


def search_description(value: object) -> str:
    # Citation IDs are useful in the article, but not as search snippet text.
    text = re.sub(r"\[S\d+\]", "", str(value or ""))
    return _clip(_plain_text(text), DESCRIPTION_LIMIT)
