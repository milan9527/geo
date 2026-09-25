"""Language selection, server-rendered labels and article editions."""
from contextvars import ContextVar
from functools import lru_cache
from html.parser import HTMLParser
import html
import json
from pathlib import Path
import re
from urllib.parse import urlsplit, urlunsplit

from .research import read_research_rows

LANGUAGE = ContextVar("aperture_language", default="zh")
CACHE = ContextVar("aperture_language_cache", default=None)
connection_provider = None
PUBLIC_PATHS = ("/article/", "/category/", "/authors/")
PUBLIC_PAGES = {"/", "/about", "/methodology", "/editorial-policy", "/corrections", "/feed.xml"}


def begin_request(path, query):
    language = "zh" if path == "/zh" or path.startswith("/zh/") else "en"
    if path.startswith(("/api/", "/agent/")):
        language = "zh" if query.get("lang", ["en"])[0] == "zh" else "en"
    LANGUAGE.set(language)
    CACHE.set({})
    return (path[3:] or "/") if language == "zh" and (path == "/zh" or path.startswith("/zh/")) else path


def language():
    return LANGUAGE.get()


def path_for(path, locale=None):
    locale = locale or language()
    if path == "/zh" or path.startswith("/zh/"):
        path = path[3:] or "/"
    return ("/zh" + path) if locale == "zh" else path


@lru_cache(maxsize=1)
def labels():
    path = Path(__file__).parent / "locales/en.json"
    return json.loads(path.read_text()) if path.exists() else {}


@lru_cache(maxsize=1)
def label_pattern():
    keys = sorted(labels(), key=len, reverse=True)
    return re.compile("|".join(re.escape(key) for key in keys)) if keys else None


def text(value):
    if language() == "zh" or not isinstance(value, str) or not re.search(r"[\u3400-\u9fff]", value):
        return value
    stripped = value.strip()
    if stripped in labels():
        return value.replace(stripped, labels()[stripped])
    pattern = label_pattern()
    translated = pattern.sub(lambda match: labels()[match.group()], value) if pattern else value
    # Unknown prose is source content, not a collection of UI label fragments.
    return value if re.search(r"[\u3400-\u9fff]", translated) else translated


def _cache():
    result = CACHE.get()
    if result is None:
        result = {}
        CACHE.set(result)
    return result


def english_headers():
    cache = _cache()
    if "headers" not in cache:
        if not connection_provider:
            return {}
        with connection_provider() as conn:
            rows = read_research_rows(conn, """
                SELECT a.id,a.slug,t.source_hash,t.updated_at translation_updated_at,
                       t.content_json::jsonb->>'title' title,
                       t.content_json::jsonb->>'dek' dek,
                       t.content_json::jsonb->>'summary' summary,
                       (t.content_json::jsonb->'keywords')::text keywords
                FROM article_translations t JOIN articles a ON a.id=t.article_id
                WHERE t.locale='en'
                  AND t.source_hash=md5(concat_ws(chr(31),a.title,a.dek,a.summary,a.body_json,a.keywords))
                  AND t.review_json::jsonb->>'approved'='true'
                """, order_by="id")
        cache["headers"] = {row["id"]: row for row in rows}
        cache["slugs"] = {row["slug"]: row for row in rows}
    return cache["headers"]


def edition(article_id):
    cache = _cache()
    key = ("edition", article_id)
    if key not in cache:
        with connection_provider() as conn:
            rows = read_research_rows(conn, """
                SELECT article_id,content_json FROM article_translations
                WHERE article_id=%s AND locale='en'
                """, (article_id,), order_by="article_id")
        cache[key] = json.loads(rows[0]["content_json"]) if rows else None
    return cache[key]


def article_row(row, *, detailed=False):
    row = dict(row)
    if language() == "zh":
        return row
    headers = english_headers()
    header = headers.get(row.get("id")) or _cache().get("slugs", {}).get(row.get("slug"))
    if header:
        for key in ("title", "dek", "summary", "keywords"):
            if key in row:
                row[key] = header[key]
        if "updated_at" in row:
            row["updated_at"] = max(str(row["updated_at"]), header["translation_updated_at"])
        if detailed:
            document = edition(header["id"])
            row["body_json"] = json.dumps(document["sections"], ensure_ascii=False)
            if "sources" in row:
                row["sources"] = [
                    {**source, "title": document["sourceTitles"][index]}
                    for index, source in enumerate(row["sources"])
                ]
    for key in ("author", "author_role", "category_name", "category_eyebrow"):
        if key in row:
            row[key] = text(row[key])
    return row


def payload(value):
    if isinstance(value, dict):
        value = dict(value)
        preserved = set()
        if "output_article_id" in value and "verification" in value:
            preserved.update({"summary", "error_message", "topic", "article_title", "sections", "evidence", "analysisProcess", "verification", "toolTrace"})
        if "agent_id" in value and "message" in value:
            preserved.add("message")
        # Editable registry values must round-trip in their source language.
        # Translating them in an English console response would save translated
        # names, notes or connector configuration during an unrelated edit.
        if "ingestion_method" in value and "publisher" in value and "url" in value:
            preserved.update({"name", "publisher", "source_type", "notes", "config", "secret_arn"})
        if language() == "en" and "title" in value and "slug" in value:
            header = english_headers().get(value.get("id")) or _cache().get("slugs", {}).get(value["slug"])
            if not header:
                preserved.update({"title", "dek", "summary", "sections", "keywords", "sources"})
            if header:
                for key in ("title", "dek", "summary"):
                    if key in value:
                        value[key] = header[key]
                for key in ("updatedAt", "updated_at"):
                    if key in value:
                        value[key] = max(str(value[key]), header["translation_updated_at"])
                if "sections" in value:
                    document = edition(header["id"])
                    value["sections"] = document["sections"]
                    value["keywords"] = document["keywords"]
                    if "sources" in value:
                        value["sources"] = [{**s, "title": document["sourceTitles"][i]}
                                            for i, s in enumerate(value["sources"])]
        return {key: item if key in preserved | {"code", "body_json", "config_json", "password", "token", "slug", "url"} else payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [payload(item) for item in value]
    return text(value)


def schema(value, base_url):
    if isinstance(value, dict):
        result = {key: schema(item, base_url) for key, item in value.items()}
        if "inLanguage" in result or str(result.get("@type")) in {"AnalysisNewsArticle", "WebPage", "CollectionPage", "WebSite"}:
            result["inLanguage"] = "zh-CN" if language() == "zh" else "en"
        return result
    if isinstance(value, list):
        return [schema(item, base_url) for item in value]
    if isinstance(value, str) and value.startswith(base_url + "/"):
        remainder = value[len(base_url):]
        path = remainder.split("#")[0]
        if path.startswith(PUBLIC_PATHS) or path in PUBLIC_PAGES:
            if remainder.endswith("#organization"):
                return value
            return base_url + path_for(remainder)
    return text(value)


class Markup(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.parts = []
        self.literal = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {"script", "style", "code"}:
            self.literal += 1
        if tag == "html":
            attrs["lang"] = "zh-CN" if language() == "zh" else "en"
        for key in ("title", "placeholder", "aria-label", "alt", "content"):
            if key in attrs and attrs[key]:
                attrs[key] = text(attrs[key])
        if "href" in attrs and not attrs.get("hreflang"):
            target = attrs["href"]
            if target and target.startswith("/") and not target.startswith("//"):
                parsed = urlsplit(target)
                if parsed.path in PUBLIC_PAGES or parsed.path.startswith(PUBLIC_PATHS):
                    attrs["href"] = urlunsplit(("", "", path_for(parsed.path), parsed.query, parsed.fragment))
        self.parts.append("<" + tag + "".join(
            " " + key if value is None else f' {key}="{html.escape(value, quote=True)}"'
            for key, value in attrs.items()) + ">")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.parts[-1] = self.parts[-1][:-1] + " />"

    def handle_endtag(self, tag):
        if tag in {"script", "style", "code"}:
            self.literal = max(0, self.literal - 1)
        self.parts.append(f"</{tag}>")

    def handle_data(self, data):
        self.parts.append(data if self.literal else html.escape(text(html.unescape(data)), quote=False))

    def handle_entityref(self, name):
        self.parts.append("&" + name + ";")

    def handle_charref(self, name):
        self.parts.append("&#" + name + ";")

    def handle_decl(self, decl):
        self.parts.append("<!" + decl + ">")

    def handle_comment(self, data):
        self.parts.append("<!--" + data + "-->")


def markup(value):
    parser = Markup()
    parser.feed(value)
    return "".join(parser.parts)
