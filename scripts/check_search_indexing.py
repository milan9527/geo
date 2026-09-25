#!/usr/bin/env python3
"""Audit public search discovery; optionally notify IndexNow of audited sitemap URLs."""

from __future__ import annotations

import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser
import uuid
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
BOTS = {
    "Googlebot": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "GooglebotSmartphone": "Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X Build/MMB29P) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "bingbot": "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
}


class Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.canonicals: list[str] = []
        self.robots: dict[str, str] = {}
        self.title_texts: list[str] = []
        self.in_title = False
        self.h1_texts: list[str] = []
        self.descriptions: list[str] = []
        self.in_h1 = False
        self.article_links = 0
        self.article_paths: set[str] = set()
        self.language = ""
        self.alternates: dict[str, list[str]] = defaultdict(list)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "html":
            self.language = values.get("lang") or ""
        if tag == "link" and values.get("rel") == "alternate" and values.get("hreflang"):
            self.alternates[values["hreflang"]].append(values.get("href") or "")
        if tag == "link" and values.get("rel") == "canonical":
            self.canonicals.append(values.get("href") or "")
        if tag == "meta":
            self.robots[(values.get("name") or "").lower()] = (values.get("content") or "").lower()
            if (values.get("name") or "").lower() == "description":
                self.descriptions.append(values.get("content") or "")
        if tag == "title":
            self.title_texts.append("")
            self.in_title = True
        if tag == "h1":
            self.h1_texts.append("")
            self.in_h1 = True
        if tag == "a" and re.match(r"^/(?:zh/)?article/", values.get("href") or ""):
            self.article_links += 1
            self.article_paths.add(values["href"])

    def handle_data(self, data: str) -> None:
        if self.in_h1:
            self.h1_texts[-1] += data
        if self.in_title:
            self.title_texts[-1] += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "h1":
            self.in_h1 = False
        if tag == "title":
            self.in_title = False


def fetch(url: str, *, bot: str = "Googlebot", method: str = "GET") -> dict:
    request = Request(url, headers={"User-Agent": BOTS[bot]}, method=method)
    try:
        response = urlopen(request, timeout=30)
    except HTTPError as error:
        response = error
    with response:
        return {
            "status": response.status,
            "url": response.geturl(),
            "headers": dict(response.headers.items()),
            "body": response.read().decode("utf-8", "replace"),
        }


def header(response: dict, name: str) -> str:
    return next((v for k, v in response["headers"].items() if k.lower() == name.lower()), "")


def sitemap_urls(response: dict, base: str) -> list[str]:
    if response["status"] != 200:
        raise ValueError(f"Sitemap returned HTTP {response['status']}")
    root = ET.fromstring(response["body"])
    if root.tag != f"{{{NS['s']}}}urlset":
        raise ValueError("Expected a sitemap urlset")
    urls = [element.text or "" for element in root.findall("s:url/s:loc", NS)]
    if not urls or len(urls) != len(set(urls)):
        raise ValueError("Empty sitemap or duplicate URLs")
    origin = urlparse(base)
    for url in urls:
        parsed = urlparse(url)
        if (parsed.scheme, parsed.netloc) != (origin.scheme, origin.netloc) or parsed.query or parsed.fragment:
            raise ValueError(f"Noncanonical sitemap URL: {url}")
        if parsed.path.startswith(("/api/", "/agent/")):
            raise ValueError(f"Non-page URL in sitemap: {url}")
    for element in root.findall("s:url/s:lastmod", NS):
        stamp = datetime.fromisoformat((element.text or "").replace("Z", "+00:00"))
        if stamp.replace(tzinfo=stamp.tzinfo or timezone.utc) > datetime.now(timezone.utc):
            raise ValueError("Sitemap contains a future lastmod")
    return urls


def audit(base: str) -> tuple[dict, list[str]]:
    report: dict = {
        "site": base, "checkedAt": datetime.now(timezone.utc).isoformat(),
        "checks": [], "warnings": [],
        "webmasterAccounts": "Not checked: site verification files do not establish account/API access.",
        "metadataGuidelines": {
            "titleCharacters": [10, 65], "descriptionCharacters": [25, 160],
            "meaning": "Site editorial targets, measured in Unicode characters; not an indexing guarantee.",
        },
    }
    checks = report["checks"]

    def check(name: str, ok: bool, detail: object = "") -> None:
        checks.append({"check": name, "passed": bool(ok), "detail": detail})

    robots_response = fetch(base + "/robots.txt")
    robots = RobotFileParser(base + "/robots.txt")
    robots.parse(robots_response["body"].splitlines())
    check("robots.txt", robots_response["status"] == 200 and "text/plain" in header(robots_response, "Content-Type"))
    for filename in ["sitemap.xml", "sitemap-articles.xml"]:
        check(f"robots declares {filename}", base + "/" + filename in (robots.site_maps() or []))
    maps = {}
    for filename in ["sitemap.xml", "sitemap-articles.xml"]:
        response = fetch(base + "/" + filename)
        check(f"{filename} is XML without a redirect",
              response["url"] == base + "/" + filename
              and "xml" in header(response, "Content-Type"),
              {"url": response["url"], "contentType": header(response, "Content-Type")})
        maps[filename] = sitemap_urls(response, base)
    urls = maps["sitemap.xml"]
    articles = maps["sitemap-articles.xml"]
    check("Article sitemap matches main sitemap",
          set(articles) == {url for url in urls if re.match(r"^/(?:zh/)?article/", urlparse(url).path)})
    report["urlCount"] = len(urls)
    report["articleCount"] = len(articles)
    for filename in ["google2fca4b1360d4ff6f.html", "BingSiteAuth.xml"]:
        response = fetch(base + "/" + filename)
        expected = (ROOT / "frontend/public" / filename).read_text().strip()
        check(filename, response["status"] == 200 and response["body"].strip() == expected)
    key_response = fetch(base + "/indexnow-key.txt")
    key = key_response["body"].strip()
    check("IndexNow ownership file",
          key_response["status"] == 200 and bool(re.fullmatch(r"[a-zA-Z0-9-]{8,128}", key)))
    missing = "/search-check-missing-" + uuid.uuid4().hex
    for path in [missing, missing + "/", "/article" + missing, "/category" + missing]:
        response = fetch(base + path)
        check(f"Real 404: {path}", response["status"] == 404, response["status"])
    response = fetch(base + missing, method="HEAD")
    check("HEAD returns real 404", response["status"] == 404)
    response = fetch(base + "/index.html")
    check("Home alias resolves to canonical home", response["status"] == 200 and response["url"] == base + "/")

    def inspect(item: tuple[str, str]) -> dict:
        url, bot = item
        errors = []
        try:
            response = fetch(url, bot=bot)
            if response["status"] != 200:
                errors.append(f"HTTP {response['status']}")
            if response["url"] != url:
                errors.append("Sitemap URL redirects")
            if "text/html" not in header(response, "Content-Type"):
                errors.append("Not HTML")
            robot_name = "Googlebot" if bot == "GooglebotSmartphone" else bot
            if not robots.can_fetch(robot_name, url):
                errors.append("Blocked by robots.txt")
            page = Page()
            page.feed(response["body"])
            path = urlparse(url).path
            english_path = re.sub(r"^/zh(?=/|$)", "", path) or "/"
            expected_alternates = {
                "en": [base + english_path], "zh-CN": [base + "/zh" + english_path],
                "x-default": [base + english_path],
            }
            if dict(page.alternates) != expected_alternates:
                errors.append("Missing or conflicting bilingual alternate URLs")
            if any(target not in urls for targets in page.alternates.values() for target in targets):
                errors.append("Alternate URL missing from sitemap")
            if page.language != ("zh-CN" if path.startswith("/zh/") else "en"):
                errors.append("Incorrect document language")
            if page.canonicals != [url]:
                errors.append("Missing, duplicate, or conflicting canonical")
            if len(page.title_texts) != 1 or not page.title_texts[0].strip():
                errors.append("Expected one nonempty server-rendered title")
            elif not 10 <= len(page.title_texts[0]) <= 65:
                errors.append("Title outside site target of 10–65 characters")
            if len(page.h1_texts) != 1 or not page.h1_texts[0].strip():
                errors.append("Expected one nonempty server-rendered h1")
            if len(page.descriptions) != 1 or not page.descriptions[0].strip():
                errors.append("Expected one nonempty meta description")
            elif not 25 <= len(page.descriptions[0]) <= 160:
                errors.append("Meta description outside site target of 25–160 characters")
            elif re.search(r"\[S\d+\]", page.descriptions[0]):
                errors.append("Citation IDs in meta description")
            directives = ",".join([
                header(response, "X-Robots-Tag").lower(),
                page.robots.get("robots", ""), page.robots.get(robot_name.lower(), ""),
            ])
            if re.search(r"\b(?:noindex|none)\b", directives):
                errors.append("noindex directive")
            return {"url": url, "bot": bot, "passed": not errors, "errors": errors,
                    "articleLinksInHtml": page.article_links,
                    "articlePathsInHtml": sorted(page.article_paths),
                    "titleTexts": page.title_texts,
                    "descriptions": page.descriptions,
                    "h1Texts": page.h1_texts,
                    "language": page.language, "alternates": dict(page.alternates),
                    "descriptionLengths": [
                        {"characters": len(text), "utf8Bytes": len(text.encode("utf-8"))}
                        for text in page.descriptions
                    ]}
        except Exception as error:
            return {"url": url, "bot": bot, "passed": False, "errors": [str(error)]}

    with ThreadPoolExecutor(max_workers=6) as pool:
        report["pages"] = list(pool.map(inspect, [(url, bot) for url in urls for bot in BOTS]))
    for field, label in [("titleTexts", "titles"), ("descriptions", "meta descriptions")]:
        groups: dict[str, list[str]] = defaultdict(list)
        for page in report["pages"]:
            if page["bot"] == "Googlebot" and len(page.get(field, [])) == 1:
                groups[page[field][0].strip()].append(page["url"])
        duplicates = [group for group in groups.values() if len(group) > 1]
        check(f"Unique {label} across sitemap pages", not duplicates, duplicates)
    for bot in BOTS:
        for prefix in ["", "/zh"]:
            home = next((item for item in report["pages"] if item["url"] == base + prefix + "/" and item["bot"] == bot), {})
            check(f"{bot}: {prefix or 'English'} home exposes published article links without JavaScript",
                  not articles or home.get("articleLinksInHtml", 0) > 0)
        collections = [item for item in report["pages"] if item["bot"] == bot
                       and (urlparse(item["url"]).path in {"/", "/zh/"}
                            or re.match(r"^/(?:zh/)?category/", urlparse(item["url"]).path))]
        linked = {base + path for item in collections for path in item.get("articlePathsInHtml", [])}
        check(f"{bot}: home and categories link to every published article",
              linked == set(articles),
              {"missing": sorted(set(articles) - linked), "notPublished": sorted(linked - set(articles))})
    report["passed"] = all(item["passed"] for item in checks + report["pages"])
    report["indexnowKey"] = key
    return report, urls


def submit_indexnow(base: str, key: str, urls: list[str]) -> list[dict]:
    results = []
    for offset in range(0, len(urls), 10000):
        batch = urls[offset:offset + 10000]
        request = Request(
            "https://www.bing.com/indexnow",
            data=json.dumps({
                "host": urlparse(base).netloc, "key": key,
                "keyLocation": base + "/indexnow-key.txt", "urlList": batch,
            }).encode(),
            headers={"Content-Type": "application/json; charset=utf-8",
                     "User-Agent": "ApertureGEO-SearchAudit/1.0"},
            method="POST",
        )
        with urlopen(request, timeout=30) as response:
            if response.status not in (200, 202):
                raise RuntimeError(f"Unexpected IndexNow status: {response.status}")
            results.append({"endpoint": request.full_url, "httpStatus": response.status, "urlCount": len(batch)})
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.environ.get("GEO_PUBLIC_BASE_URL", "https://aperture.zhangwangshu.com"))
    parser.add_argument("--submit-indexnow", action="store_true", help="Notify IndexNow only after every required audit check passes")
    parser.add_argument("--output", type=Path, help="Write the full JSON audit report")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme != "https" or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password:
        parser.error("--base-url must be an HTTPS origin")
    report, urls = audit(base)
    key = report.pop("indexnowKey")
    if args.submit_indexnow and report["passed"]:
        report["indexnow"] = {
            "batches": submit_indexnow(base, key, urls),
            "meaning": "Notification accepted, not a guarantee of indexing. Google does not use IndexNow.",
        }
    if args.output:
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
