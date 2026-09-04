#!/usr/bin/env python3
"""End-to-end PostgreSQL smoke test for all interactive GEO demo workflows."""

from __future__ import annotations

import json
import os
import sys
import time
from html import escape
from http.client import HTTPConnection
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.database import connection


PUBLIC_PORT = 4173
ADMIN_PORT = 4174
ADMIN_KEY = os.environ.get("GEO_ADMIN_KEY", "geo-admin-demo")
ALLOW_ADMIN_KEY = os.environ.get(
    "GEO_ALLOW_ADMIN_KEY", "false"
).lower() in {"1", "true", "yes"}
ADMIN_USERNAME = os.environ.get("GEO_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("GEO_ADMIN_PASSWORD", "")
SESSION_COOKIE = ""
TEST_SLUG = f"postgres-smoke-{int(time.time())}"
TEST_SOURCE_NAME = f"Registry smoke source {int(time.time())}"
TEST_SOURCE_URL = f"https://www.iana.org/domains/reserved?geo-smoke={int(time.time())}"
PUBLIC_BASE_URL = os.environ.get(
    "GEO_PUBLIC_BASE_URL", "https://aperture.zhangwangshu.com"
).rstrip("/")


def request(
    port: int,
    method: str,
    path: str,
    payload: dict | None = None,
    *,
    admin: bool = False,
    user_agent: str = "ApertureSmokeTest/1.0",
) -> tuple[int, object]:
    global SESSION_COOKIE
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json", "User-Agent": user_agent}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if admin:
        if SESSION_COOKIE:
            headers["Cookie"] = SESSION_COOKIE
        elif ALLOW_ADMIN_KEY:
            headers["X-Admin-Key"] = ADMIN_KEY
    connection = HTTPConnection("127.0.0.1", port, timeout=10)
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    set_cookie = response.getheader("Set-Cookie")
    if set_cookie and set_cookie.startswith("aperture_admin_session="):
        SESSION_COOKIE = set_cookie.split(";", 1)[0]
    raw = response.read()
    connection.close()
    data = json.loads(raw.decode("utf-8")) if raw else None
    return response.status, data


def request_raw(
    port: int,
    method: str,
    path: str,
    *,
    user_agent: str = "ApertureSmokeTest/1.0",
) -> tuple[int, dict[str, str], bytes]:
    client = HTTPConnection("127.0.0.1", port, timeout=10)
    client.request(
        method,
        path,
        headers={"Accept": "*/*", "User-Agent": user_agent},
    )
    response = client.getresponse()
    status = response.status
    headers = {key.lower(): value for key, value in response.getheaders()}
    body = response.read()
    client.close()
    return status, headers, body


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"PASS  {message}")


def authenticate_admin() -> None:
    if ALLOW_ADMIN_KEY and not ADMIN_PASSWORD:
        return
    if not ADMIN_PASSWORD:
        raise RuntimeError(
            "Set GEO_ADMIN_PASSWORD for the smoke-test administrator"
        )
    status, result = request(
        ADMIN_PORT,
        "POST",
        "/api/admin/auth/login",
        {"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
    )
    check(
        status == 200 and result["authenticated"],
        "Administrator session login",
    )


def cleanup() -> None:
    with connection() as conn:
        conn.execute(
            """
            DELETE FROM traffic_events
            WHERE article_id IN (
                SELECT id FROM articles WHERE slug LIKE 'postgres-smoke-%%'
            )
            """
        )
        conn.execute(
            "DELETE FROM traffic_events WHERE metadata LIKE %s",
            ('%"smokeTest": true%',),
        )
        conn.execute("DELETE FROM articles WHERE slug LIKE 'postgres-smoke-%%'")
        conn.execute(
            "DELETE FROM data_sources WHERE name LIKE 'Registry smoke source %'"
        )


def main() -> None:
    cleanup()
    created_article_id = None
    original_setting = None
    original_crawler_status = None
    original_crawler_schedule = None
    tested_crawler_id = None
    created_source_id = None
    created_job_ids: list[int] = []
    try:
        status, health = request(PUBLIC_PORT, "GET", "/api/health")
        check(
            status == 200 and health["database"] in {"postgresql", "aurora-postgresql"},
            "PostgreSQL health check",
        )
        check(health["databaseName"] == "geo", "Application is connected to geo database")

        status, categories = request(PUBLIC_PORT, "GET", "/api/v1/categories")
        check(status == 200 and len(categories) == 5, "Public category listing")

        status, articles = request(PUBLIC_PORT, "GET", "/api/v1/articles")
        check(status == 200 and len(articles) >= 7, "Public article listing")
        primary_slug = articles[0]["slug"]

        status, detail = request(PUBLIC_PORT, "GET", f"/api/v1/articles/{primary_slug}")
        check(status == 200 and detail["sections"] and detail["sources"], "Complete article detail")

        status, headers, body = request_raw(
            PUBLIC_PORT, "GET", f"/article/{primary_slug}"
        )
        document = body.decode("utf-8")
        check(
            status == 200
            and headers["content-type"].startswith("text/html")
            and escape(detail["title"]) in document
            and escape(detail["summary"]) in document,
            "Server-rendered article contains complete research content",
        )
        check(
            f'<link rel="canonical" href="{PUBLIC_BASE_URL}/article/{primary_slug}"'
            in document
            and '"@type":"AnalysisNewsArticle"' in document
            and 'rel="alternate" type="application/ld+json"' in document,
            "Article canonical, JSON-LD, and machine alternate metadata",
        )
        status, headers, body = request_raw(
            PUBLIC_PORT, "HEAD", f"/article/{primary_slug}"
        )
        check(
            status == 200
            and not body
            and int(headers["content-length"]) > 0,
            "Server-rendered article HEAD response",
        )
        status, _, _ = request_raw(
            PUBLIC_PORT, "GET", "/article/not-a-published-article"
        )
        check(status == 404, "Unknown article returns a real HTTP 404")

        for discovery_path, expected_type in (
            ("/robots.txt", "text/plain"),
            ("/sitemap.xml", "application/xml"),
            ("/sitemap-articles.xml", "application/xml"),
            ("/feed.xml", "application/rss+xml"),
            ("/llms.txt", "text/plain"),
            ("/indexnow-key.txt", "text/plain"),
        ):
            status, headers, body = request_raw(
                PUBLIC_PORT, "GET", discovery_path
            )
            check(
                status == 200
                and headers["content-type"].startswith(expected_type)
                and body,
                f"Discovery endpoint {discovery_path}",
            )
        _, _, sitemap_body = request_raw(
            PUBLIC_PORT, "GET", "/sitemap.xml"
        )
        sitemap = sitemap_body.decode("utf-8")
        check(
            f"{PUBLIC_BASE_URL}/article/{primary_slug}" in sitemap
            and "/api/admin/" not in sitemap
            and "/paid" not in sitemap,
            "Sitemap includes only public canonical content",
        )

        status, results = request(PUBLIC_PORT, "GET", "/api/v1/search?q=Agent")
        check(status == 200 and results, "Full-text content search")

        status, machine = request(
            PUBLIC_PORT,
            "GET",
            f"/agent/v1/articles/{primary_slug}",
            user_agent="GPTBot/1.0",
        )
        check(status == 200 and machine["claims"] and machine["citations"], "Agent-readable article")

        status, tracked = request(
            PUBLIC_PORT,
            "POST",
            "/api/v1/track",
            {
                "eventType": "smoke_test",
                "articleSlug": primary_slug,
                "metadata": {"smokeTest": True},
            },
        )
        check(status == 201 and tracked["ok"], "Traffic event persistence")

        authenticate_admin()
        status, metrics = request(
            ADMIN_PORT, "GET", "/api/admin/metrics?range=30d", admin=True
        )
        check(
            status == 200
            and metrics["daily"]
            and {
                "challenges",
                "paymentAttempts",
                "verificationFailures",
                "settlementFailures",
                "serviceErrors",
                "internalPayments",
                "externalPayments",
                "recentEvents",
            }.issubset(metrics["abTest"]),
            "GEO and detailed x402 metrics aggregation",
        )

        today = time.strftime("%Y-%m-%d", time.gmtime())
        status, custom_metrics = request(
            ADMIN_PORT,
            "GET",
            f"/api/admin/metrics?start={today}&end={today}",
            admin=True,
        )
        check(
            status == 200
            and custom_metrics["rangeKey"] == "custom"
            and custom_metrics["startDate"] == today
            and custom_metrics["endDate"] == today
            and len(custom_metrics["daily"]) == 1,
            "Custom GEO metrics date range",
        )

        status, created = request(
            ADMIN_PORT,
            "POST",
            "/api/admin/articles",
            {
                "title": "PostgreSQL 端到端验证内容",
                "slug": TEST_SLUG,
                "categorySlug": "ai",
                "author": "系统验证",
                "dek": "这是一篇用于验证内容创建、发布与公开读取流程的临时文章。",
                "status": "draft",
            },
            admin=True,
        )
        check(status == 201 and created["status"] == "draft", "Create draft article")
        created_article_id = created["articleId"]

        status, detail = request(
            ADMIN_PORT,
            "GET",
            f"/api/admin/articles/{created_article_id}",
            admin=True,
        )
        check(
            status == 200 and detail["id"] == created_article_id,
            "Read complete admin article detail",
        )

        status, reviewed = request(
            ADMIN_PORT,
            "PATCH",
            "/api/admin/articles/batch",
            {"ids": [created_article_id], "action": "review"},
            admin=True,
        )
        check(
            status == 200
            and reviewed["count"] == 1
            and reviewed["action"] == "review",
            "Batch move article to review",
        )

        status, published = request(
            ADMIN_PORT,
            "PATCH",
            "/api/admin/articles/batch",
            {"ids": [created_article_id], "action": "publish"},
            admin=True,
        )
        check(
            status == 200
            and published["count"] == 1
            and published["action"] == "publish",
            "Batch publish article",
        )

        status, public_test_article = request(
            PUBLIC_PORT, "GET", f"/api/v1/articles/{TEST_SLUG}"
        )
        check(status == 200 and public_test_article["slug"] == TEST_SLUG, "Published article is public")

        status, settings = request(ADMIN_PORT, "GET", "/api/admin/settings", admin=True)
        check(status == 200 and "automatic_json_ld" in settings, "Persistent settings listing")
        original_setting = settings["automatic_json_ld"]["value"]
        status, updated_setting = request(
            ADMIN_PORT,
            "PATCH",
            "/api/admin/settings/automatic_json_ld",
            {"value": not original_setting},
            admin=True,
        )
        check(status == 200 and updated_setting["ok"], "Update persistent setting")
        _, settings_after = request(ADMIN_PORT, "GET", "/api/admin/settings", admin=True)
        check(
            settings_after["automatic_json_ld"]["value"] is not original_setting,
            "Setting value is read back from PostgreSQL",
        )

        status, crawlers = request(ADMIN_PORT, "GET", "/api/admin/crawlers", admin=True)
        check(status == 200 and crawlers, "Crawler Agent listing")
        crawler = crawlers[-1]
        status, data_sources = request(
            ADMIN_PORT, "GET", "/api/admin/data-sources", admin=True
        )
        check(
            status == 200 and data_sources and data_sources[0]["assignments"],
            "Data source registry listing and Agent assignments",
        )
        status, created_source = request(
            ADMIN_PORT,
            "POST",
            "/api/admin/data-sources",
            {
                "publisher": "IANA",
                "name": TEST_SOURCE_NAME,
                "url": TEST_SOURCE_URL,
                "categorySlug": "ai",
                "sourceType": "Smoke test",
                "ingestionMethod": "web",
                "trustTier": 2,
                "maxItems": 2,
                "respectRobots": True,
                "accessModel": "open",
                "agentIds": [crawler["id"]],
            },
            admin=True,
        )
        check(
            status == 201 and created_source["sourceId"],
            "Register and assign data source",
        )
        created_source_id = created_source["sourceId"]
        status, source_test = request(
            ADMIN_PORT,
            "POST",
            f"/api/admin/data-sources/{created_source_id}/test",
            {},
            admin=True,
        )
        check(
            status == 200 and source_test["ok"],
            "Data source HTTPS connectivity test",
        )
        status, source_update = request(
            ADMIN_PORT,
            "PATCH",
            f"/api/admin/data-sources/{created_source_id}",
            {"status": "paused", "trustTier": 3, "agentIds": []},
            admin=True,
        )
        check(status == 200 and source_update["ok"], "Update and unassign data source")
        status, source_delete = request(
            ADMIN_PORT,
            "DELETE",
            f"/api/admin/data-sources/{created_source_id}",
            admin=True,
        )
        check(status == 200 and source_delete["ok"], "Delete data source")
        created_source_id = None
        tested_crawler_id = crawler["id"]
        original_crawler_status = crawler["status"]
        original_crawler_schedule = crawler["schedule"]
        test_schedule = (
            "0 */6 * * *"
            if original_crawler_schedule != "0 */6 * * *"
            else "0 */3 * * *"
        )
        status, schedule_update = request(
            ADMIN_PORT,
            "PATCH",
            f"/api/admin/crawlers/{crawler['id']}",
            {"schedule": test_schedule},
            admin=True,
        )
        check(
            status == 200
            and schedule_update["schedule"] == test_schedule
            and schedule_update["scheduleLabel"],
            "Crawler recurring schedule update",
        )
        status, invalid_schedule = request(
            ADMIN_PORT,
            "PATCH",
            f"/api/admin/crawlers/{crawler['id']}",
            {"schedule": "* * * * *"},
            admin=True,
        )
        check(
            status == 400 and "支持" in invalid_schedule["error"],
            "Crawler schedule validation",
        )
        target_status = "running" if original_crawler_status == "paused" else "paused"
        status, crawler_update = request(
            ADMIN_PORT,
            "PATCH",
            f"/api/admin/crawlers/{crawler['id']}",
            {"status": target_status},
            admin=True,
        )
        check(status == 200 and crawler_update["status"] == target_status, "Crawler status update")

        status, job = request(
            ADMIN_PORT,
            "POST",
            f"/api/admin/crawlers/{crawler['id']}/run",
            {},
            admin=True,
        )
        check(status == 202 and job["jobId"], "Create crawler job")
        created_job_ids.append(job["jobId"])

        status, jobs = request(ADMIN_PORT, "GET", "/api/admin/jobs", admin=True)
        check(
            status == 200 and any(item["id"] == job["jobId"] for item in jobs),
            "Crawler job is persisted",
        )

        status, batch = request(
            ADMIN_PORT, "POST", "/api/admin/crawlers/run-all", {}, admin=True
        )
        check(status == 202 and batch["jobs"], "Batch crawler scheduling")
        created_job_ids.extend(
            item["jobId"] for item in batch["jobs"] if item.get("jobId")
        )

        status, events = request(ADMIN_PORT, "GET", "/api/admin/events", admin=True)
        check(status == 200 and events, "Admin activity event listing")

        status, deleted = request(
            ADMIN_PORT,
            "PATCH",
            "/api/admin/articles/batch",
            {"ids": [created_article_id], "action": "delete", "confirm": True},
            admin=True,
        )
        check(
            status == 200
            and deleted["count"] == 1
            and deleted["action"] == "delete",
            "Batch delete temporary draft",
        )
        created_article_id = None

        print("\nAll PostgreSQL end-to-end checks passed.")
    finally:
        with connection() as conn:
            if original_setting is not None:
                conn.execute(
                    """
                    UPDATE app_settings SET value = %s
                    WHERE key = 'automatic_json_ld'
                    """,
                    ("true" if original_setting else "false",),
                )
            if created_job_ids:
                for job_id in created_job_ids:
                    conn.execute("DELETE FROM crawler_jobs WHERE id = %s", (job_id,))
            if created_source_id is not None:
                conn.execute(
                    "DELETE FROM data_sources WHERE id = %s",
                    (created_source_id,),
                )
        if (
            original_crawler_status is not None
            and original_crawler_schedule is not None
            and tested_crawler_id is not None
        ):
            request(
                ADMIN_PORT,
                "PATCH",
                f"/api/admin/crawlers/{tested_crawler_id}",
                {
                    "status": original_crawler_status,
                    "schedule": original_crawler_schedule,
                },
                admin=True,
            )
        if SESSION_COOKIE:
            request(
                ADMIN_PORT,
                "POST",
                "/api/admin/auth/logout",
                {},
                admin=True,
            )
        cleanup()


if __name__ == "__main__":
    main()
