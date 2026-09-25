#!/usr/bin/env python3
"""Capture real English UI screens with a temporary admin and remove it afterward."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import secrets
import sys

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.auth import hash_password
from backend.database import connection, utc_now


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/screenshots")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    public = "https://aperture.zhangwangshu.com"
    admin = "https://deu7vkdd3jf5.cloudfront.net"
    username = "presentation-" + secrets.token_hex(6)
    password = secrets.token_urlsafe(32)
    user_id = None
    screenshots = []
    errors = []
    expect.set_options(timeout=30000)

    try:
        with connection() as conn:
            digest, salt, iterations = hash_password(password)
            user_id = conn.execute(
                """INSERT INTO admin_users(username,display_name,role,password_hash,password_salt,
                   password_iterations,status,created_at,updated_at)
                   VALUES(%s,'Documentation','administrator',%s,%s,%s,'active',%s,%s) RETURNING id""",
                (username, digest, salt, iterations, utc_now(), utc_now()),
            ).fetchone()["id"]
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--no-proxy-server"])
            context = browser.new_context(viewport={"width": 1600, "height": 1000},
                                          device_scale_factor=1.5,
                                          user_agent="Aperture-Documentation-Capture/1.0")
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))

            def capture(name, caption, *, locator=None):
                expect(page.locator("html")).to_have_attribute("lang", "en")
                page.evaluate("document.fonts.ready")
                page.wait_for_timeout(250)
                destination = args.output / (name + ".png")
                if locator:
                    page.locator(locator).screenshot(path=str(destination))
                else:
                    page.screenshot(path=str(destination))
                screenshots.append({
                    "file": destination.name, "caption": caption, "url": page.url,
                    "capturedAt": datetime.now(timezone.utc).isoformat(),
                    "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
                    "capture": "Actual English UI; normal controls only; no replaced text or synthetic data.",
                    "region": locator or "viewport",
                })
                print(name, flush=True)

            page.goto(public + "/", wait_until="domcontentloaded")
            expect(page.locator("body")).to_have_attribute("data-ssr", "true")
            capture("public-home-en", "English research homepage")
            page.goto(public + "/category/agent", wait_until="domcontentloaded")
            expect(page.locator(".category-hero h1")).to_have_text("Agent technology")
            capture("public-category-en", "Complete Agent technology category")
            page.goto(public + "/article/aurora-data-api-1mb-502-chunked-read",
                      wait_until="domcontentloaded")
            expect(page.locator(".article-page")).to_be_visible()
            capture("public-article-en", "Published English engineering article")
            page.locator("#searchButton").click()
            page.locator("#searchInput").fill("Agent")
            expect(page.locator(".search-result").first).to_be_visible()
            capture("public-search-en", "English search results", locator=".search-panel")

            page.goto(admin + "/", wait_until="domcontentloaded")
            expect(page.locator("#loginTitle")).to_have_text("Log in to the admin console")
            capture("admin-login-en", "English admin login; no credentials shown")
            page.locator('[name="username"]').fill(username)
            page.locator('[name="password"]').fill(password)
            page.locator("#loginButton").click()
            expect(page.locator(".metric-grid")).to_be_visible()
            expect(page.locator("#adminApp [role=alert]")).to_have_count(0)
            # A natural viewport crop shows the live overview without historical source-language logs.
            page.set_viewport_size({"width": 1600, "height": 850})
            capture("admin-dashboard-en", "Live English overview and x402 testnet metrics")
            page.set_viewport_size({"width": 1600, "height": 1000})

            page.locator('[data-view="content"]').click()
            page.locator("#statusFilter").select_option("published")
            expect(page.locator("#contentRows tr")).not_to_have_count(0)
            capture("admin-content-en", "Published content filter in the English console")
            article_id = page.evaluate("""state.articles.find(a =>
                a.slug==='aurora-data-api-1mb-502-chunked-read').id""")
            page.locator(f'[data-open-article="{article_id}"]').first.click()
            expect(page.locator("#contentDetailBody .detail-sections")).to_be_visible()
            capture("admin-article-en", "Reviewed English article detail", locator=".detail-modal")
            page.locator("#closeContentDetail").click()

            for view, name, caption in [
                ("crawlers", "admin-crawlers-en", "Live crawler agents and UTC schedules"),
                ("sources", "admin-sources-en", "English source registry"),
                ("settings", "admin-settings-en", "English settings with explicit feature limits"),
            ]:
                page.locator(f'[data-view="{view}"]').click()
                capture(name, caption)
            page.locator("#logoutButton").click()
            expect(page.locator("#loginScreen")).to_be_visible()
            browser.close()
        if errors:
            raise RuntimeError("Browser errors: " + "; ".join(errors))
    finally:
        if user_id is not None:
            with connection() as conn:
                conn.execute("DELETE FROM admin_users WHERE id=%s AND username=%s", (user_id, username))
        manifest = {"language": "en", "screenshots": screenshots, "browserErrors": errors,
                    "temporaryAdminRemoved": user_id is not None,
                    "contentMutations": False, "paymentsExecuted": False}
        (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
