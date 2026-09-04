from __future__ import annotations

import hashlib
import json
import os
import re
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

import boto3


REGION = os.environ.get("AWS_REGION", "us-east-1")
PUBLIC_BASE_URL = os.environ.get(
    "GEO_PUBLIC_BASE_URL", "https://aperture.zhangwangshu.com"
).rstrip("/")
PUBLIC_DISTRIBUTION_ID = os.environ["GEO_PUBLIC_DISTRIBUTION_ID"]
INDEXNOW_ENDPOINT = os.environ.get(
    "INDEXNOW_ENDPOINT", "https://api.indexnow.org/indexnow"
)
INDEXNOW_KEY = os.environ.get("INDEXNOW_KEY") or hashlib.sha256(
    PUBLIC_BASE_URL.encode("utf-8")
).hexdigest()[:32]
cloudfront = boto3.client("cloudfront", region_name=REGION)


def clean_values(values: object, *, max_length: int) -> list[str]:
    if not isinstance(values, list):
        return []
    return sorted(
        {
            str(value)
            for value in values
            if re.fullmatch(
                rf"[a-z0-9][a-z0-9-]{{0,{max_length - 1}}}",
                str(value),
            )
        }
    )


def build_urls(slugs: list[str], categories: list[str]) -> list[str]:
    urls = [
        f"{PUBLIC_BASE_URL}/article/{quote(slug, safe='')}"
        for slug in slugs
    ]
    urls.extend(
        f"{PUBLIC_BASE_URL}/category/{quote(category, safe='')}"
        for category in categories
    )
    return list(dict.fromkeys(urls))


def invalidate(slugs: list[str], categories: list[str], request_id: str) -> str:
    paths = [
        *(f"/article/{quote(slug, safe='')}" for slug in slugs),
        *(f"/category/{quote(category, safe='')}" for category in categories),
        "/sitemap.xml",
        "/sitemap-articles.xml",
        "/feed.xml",
    ]
    unique_paths = list(dict.fromkeys(paths))
    response = cloudfront.create_invalidation(
        DistributionId=PUBLIC_DISTRIBUTION_ID,
        InvalidationBatch={
            "Paths": {
                "Quantity": len(unique_paths),
                "Items": unique_paths,
            },
            "CallerReference": (
                f"indexing-{request_id}-{time.time_ns()}"
            )[:128],
        },
    )
    return str(response["Invalidation"]["Id"])


def submit_indexnow(urls: list[str]) -> int:
    host = urlparse(PUBLIC_BASE_URL).netloc
    payload = json.dumps(
        {
            "host": host,
            "key": INDEXNOW_KEY,
            "keyLocation": f"{PUBLIC_BASE_URL}/indexnow-key.txt",
            "urlList": urls,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    request = Request(
        INDEXNOW_ENDPOINT,
        data=payload,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "ApertureGEO-IndexingNotifier/1.0",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=15) as response:
            return int(response.status)
    except HTTPError as error:
        detail = error.read(1000).decode("utf-8", "replace")
        raise RuntimeError(
            f"IndexNow rejected submission: HTTP {error.code} {detail}"
        ) from error
    except URLError as error:
        raise RuntimeError(f"IndexNow connection failed: {error}") from error


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    slugs = clean_values(event.get("slugs"), max_length=240)
    categories = clean_values(event.get("categories"), max_length=100)
    urls = build_urls(slugs, categories)
    if not urls:
        raise ValueError("At least one valid article slug or category is required")
    request_id = str(getattr(context, "aws_request_id", "manual"))
    invalidation_id = invalidate(slugs, categories, request_id)
    indexnow_status = submit_indexnow(urls)
    result = {
        "submitted": True,
        "reason": str(event.get("reason") or "content_change")[:80],
        "urlCount": len(urls),
        "urls": urls,
        "indexnowStatus": indexnow_status,
        "invalidationId": invalidation_id,
    }
    print(json.dumps(result, ensure_ascii=False))
    return result
