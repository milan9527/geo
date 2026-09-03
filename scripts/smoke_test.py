#!/usr/bin/env python3
"""End-to-end PostgreSQL smoke test for all interactive GEO demo workflows."""

from __future__ import annotations

import json
import os
import time
from http.client import HTTPConnection

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


def main() -> None:
    cleanup()
    created_article_id = None
    original_setting = None
    original_crawler_status = None
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
        check(status == 200 and metrics["daily"], "GEO metrics aggregation")

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

        status, published = request(
            ADMIN_PORT,
            "PATCH",
            f"/api/admin/articles/{created_article_id}",
            {"status": "published"},
            admin=True,
        )
        check(status == 200 and published["ok"], "Publish article")

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
        original_crawler_status = crawler["status"]
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
        if original_crawler_status is not None:
            request(
                ADMIN_PORT,
                "PATCH",
                "/api/admin/crawlers/6",
                {"status": original_crawler_status},
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
