#!/usr/bin/env python3
"""Exercise console actions in both languages against a stateful, isolated API."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from urllib.parse import urlsplit

from playwright.sync_api import expect
import test_admin_login as login_fixture

BASE = "https://admin.test"
CATEGORY = {"id": 1, "slug": "ai", "name": "AI", "articleCount": 2}
ARTICLE = {
    "id": 1, "slug": "published", "title": "Published research", "author": "Researcher",
    "author_role": "Editor", "status": "published", "category_name": "AI",
    "updated_at": "2026-09-25T00:00:00Z", "authority_score": 95, "citation_count": 2,
    "access_model": "open", "agent_price": 0, "dek": "Evidence summary",
    "summary": "Original findings", "sections": [{"heading": "Evidence", "code": 'print("原文")'}],
    "sources": [], "verification": {},
}
CRAWLER = {
    "id": 1, "slug": "research", "name": "Research agent", "kind": "Browser",
    "status": "running", "schedule": "0 * * * *", "scheduleLabel": "Hourly",
    "industries": ["AI"], "sourceCount": 1, "success_rate": 100, "pages_today": 0,
    "eventbridge": {"state": "ENABLED"},
}
SOURCE = {
    "id": 1, "publisher": "Example", "name": "Original source", "url": "https://example.com/feed",
    "category_slug": "ai", "category_name": "AI", "source_type": "Primary evidence",
    "ingestion_method": "feed", "access_model": "open", "status": "active",
    "trust_tier": 1, "max_items": 4, "respect_robots": True, "notes": "Original notes",
    "config": {"preservedOption": True}, "agentIds": [1],
    "assignments": [{"agent_name": "Research agent"}], "credentialsConfigured": False,
}


class AdminWorkflowTests(unittest.TestCase):
    setUpClass = classmethod(login_fixture.AdminLoginTests.setUpClass.__func__)
    tearDownClass = classmethod(login_fixture.AdminLoginTests.tearDownClass.__func__)

    def setUp(self):
        self.context = self.browser.new_context(viewport={"width": 1440, "height": 1000})
        self.page = self.context.new_page()
        self.errors = []
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        self.page.on("dialog", lambda dialog: dialog.accept())
        self.authenticated = True
        self.metrics_status = 200
        self.calls = []
        self.fail = set()
        self.articles = [deepcopy(ARTICLE), {**deepcopy(ARTICLE), "id": 2, "slug": "draft",
                                            "title": "待核验的原始研究", "status": "draft"}]
        self.crawlers = [deepcopy(CRAWLER)]
        self.sources = [deepcopy(SOURCE)]
        self.settings = {key: {"value": True} for key in [
            "agent_user_agent_detection", "machine_content_endpoint", "automatic_json_ld",
            "x402_payments", "payment_failure_alerts"]}
        self.page.route(BASE + "/**", self.route)

    def tearDown(self):
        self.context.close()
        self.assertEqual(self.errors, [])

    def route(self, route):
        path = urlsplit(route.request.url).path
        method = route.request.method
        body = route.request.post_data_json if route.request.post_data else None
        if path.startswith("/api/"):
            self.calls.append((method, path, body, route.request.url))
        if (method, path) in self.fail:
            route.fulfill(status=503, json={"error": "Temporary save failure"})
            return
        result = None
        if path == "/api/admin/articles/batch":
            ids = body["ids"]
            if body["action"] == "delete":
                self.articles = [a for a in self.articles if a["id"] not in ids]
            else:
                for a in self.articles:
                    if a["id"] in ids:
                        a["status"] = "review"
            result = {"count": len(ids)}
        elif path == "/api/admin/articles":
            if method == "POST":
                result = {**deepcopy(ARTICLE), **body, "id": 3}
                self.articles.append(result)
            else:
                result = self.articles
        elif path.startswith("/api/admin/articles/"):
            result = next(a for a in self.articles if a["id"] == int(path.rsplit("/", 1)[1]))
            if method == "PATCH":
                result.update(body)
        elif path == "/api/v1/categories":
            result = [CATEGORY]
        elif path == "/api/admin/crawlers":
            result = self.crawlers
        elif path == "/api/admin/crawlers/1":
            self.crawlers[0].update(body)
            result = self.crawlers[0]
        elif path.endswith("/run-all"):
            result = {"jobs": [1]}
        elif path.endswith("/run"):
            result = {"agent": CRAWLER["name"], "jobId": 1}
        elif path == "/api/admin/data-sources":
            if method == "POST":
                self.sources.append({**deepcopy(SOURCE), "id": 2, "name": body["name"]})
            result = self.sources if method == "GET" else self.sources[-1]
        elif path.endswith("/test-all") or path.endswith("/test-batch"):
            result = {"running": False, "total": len(self.sources), "completed": len(self.sources),
                      "success": len(self.sources), "failed": 0}
        elif path == "/api/admin/data-sources/1/test":
            self.sources[0]["last_test_status"] = "success"
            result = {"message": "HTTP 200"}
        elif path == "/api/admin/data-sources/1":
            if method == "DELETE":
                self.sources = []
                result = {"deleted": True}
            else:
                self.sources[0].update(body)
                result = self.sources[0]
        elif path == "/api/admin/settings":
            result = self.settings
        elif path.startswith("/api/admin/settings/"):
            self.settings[path.rsplit("/", 1)[1]] = body
            result = body
        elif path == "/api/admin/auth/logout":
            self.authenticated = False
            result = {"authenticated": False}
        if result is None:
            login_fixture.AdminLoginTests.route(self, route)
        else:
            route.fulfill(json=result)

    def start(self, language="en", view="content"):
        self.page.goto(BASE)
        expect(self.page.locator(".metric-grid")).to_be_visible()
        if language == "zh":
            self.page.locator("[data-language-picker]").select_option("zh")
            expect(self.page.locator("html")).to_have_attribute("lang", "zh-CN")
            expect(self.page.locator(".metric-grid")).to_be_visible()
        self.page.locator(f'[data-view="{view}"]').click()

    def wrote(self, method, path):
        return [c[2] for c in self.calls if c[0:2] == (method, path)]

    def test_create_draft_failure_retry_and_reset_in_both_languages(self):
        for language in ["en", "zh"]:
            with self.subTest(language=language):
                self.start(language)
                self.page.locator("#createContent").click()
                form = self.page.locator("#createArticleForm")
                for name, value in {"title": "New verified subject", "author": "Editor",
                                    "dek": "Evidence to review", "slug": "new-subject"}.items():
                    form.locator(f'[name="{name}"]').fill(value)
                self.fail.add(("POST", "/api/admin/articles"))
                form.locator('[type="submit"]').click()
                expect(self.page.locator("#toastCopy")).to_have_text("Temporary save failure")
                expect(form.locator('[name="title"]')).to_have_value("New verified subject")
                expect(form.locator('[type="submit"]')).to_be_enabled()
                self.fail.clear()
                form.locator('[type="submit"]').click()
                expect(self.page.locator("#articleModal")).to_have_attribute("aria-hidden", "true")
                expect(form.locator('[name="title"]')).to_have_value("")
                self.assertEqual(self.wrote("POST", "/api/admin/articles")[-1]["status"], "draft")
                # Only this test's mock catalogue changes.
                self.articles = self.articles[:2]

    def test_content_filters_detail_bulk_protection_review_delete_and_export(self):
        self.start()
        self.page.locator('[data-select-article="1"]').check()
        expect(self.page.locator('[data-batch-action="delete"]')).to_be_disabled()
        expect(self.page.locator('[data-batch-action="review"]')).to_be_disabled()
        self.page.locator('[data-select-article="1"]').uncheck()
        self.page.locator("#statusFilter").select_option("draft")
        expect(self.page.locator("#contentRows tr")).to_have_count(1)
        self.page.locator('[data-open-article="2"]').first.click()
        expect(self.page.locator("#contentDetailTitle")).to_have_text("待核验的原始研究")
        expect(self.page.locator("#contentDetailBody code")).to_have_text('print("原文")')
        self.assertTrue(any(c[1] == "/api/admin/articles/2" and "lang=zh" in c[3] for c in self.calls))
        self.page.locator("#closeContentDetail").click()
        self.page.locator('[data-submit-review="2"]').click()
        self.page.wait_for_function("state.articles.find(a=>a.id===2).status==='review'")
        self.page.locator("#contentSearch").fill("待核验")
        expect(self.page.locator("#contentRows tr")).to_have_count(1)
        self.page.locator("#selectAllArticles").check()
        self.page.locator('[data-batch-action="review"]').click()
        self.page.wait_for_function("state.selectedArticles.size===0")
        with self.page.expect_download() as download:
            self.page.locator("#exportContent").click()
        export = json.loads(Path(download.value.path()).read_text())
        self.assertEqual(len(export["articles"]), 2)
        self.page.locator('[data-select-article="2"]').check()
        self.page.locator('[data-batch-action="delete"]').click()
        expect(self.page.locator("#contentRows tr")).to_have_count(1)
        self.assertEqual([a["id"] for a in self.articles], [1])

    def test_schedule_save_errors_daily_time_pause_resume_and_run(self):
        self.start("zh", "crawlers")
        self.page.locator("[data-edit-crawler-schedule]").click()
        self.page.locator("#crawlerSchedulePreset").select_option("daily")
        self.page.locator("#crawlerDailyTime").fill("13:45")
        submit = self.page.locator('#crawlerScheduleForm [type="submit"]')
        self.fail.add(("PATCH", "/api/admin/crawlers/1"))
        submit.click()
        expect(self.page.locator("#toastCopy")).to_have_text("Temporary save failure")
        expect(submit).to_be_enabled()
        self.fail.clear()
        submit.click()
        expect(self.page.locator("#crawlerScheduleModal")).to_have_attribute("aria-hidden", "true")
        self.assertEqual(self.wrote("PATCH", "/api/admin/crawlers/1")[-1], {"schedule": "45 13 * * *"})
        for status in ["paused", "running"]:
            self.page.locator("[data-toggle-crawler]").click()
            expect(self.page.locator("[data-toggle-crawler]")).to_have_attribute("data-status", status)
        self.page.locator("[data-run-crawler]").click()
        expect(self.page.locator("#toastTitle")).to_have_text("任务已提交")
        self.assertEqual(self.wrote("POST", "/api/admin/crawlers/1/run"), [{"allowPayment": False}])
        self.page.locator("[data-run-all]").click()
        expect(self.page.locator("#toastTitle")).to_have_text("批量任务已提交")
        self.assertEqual(len(self.wrote("POST", "/api/admin/crawlers/run-all")), 1)

    def test_source_edit_auth_validation_test_filter_toggle_and_delete(self):
        self.start(view="sources")
        self.page.locator("[data-edit-source]").click()
        form = self.page.locator("#dataSourceForm")
        self.page.locator("#sourceAuthType").select_option("basic")
        form.locator('[name="credentialUsername"]').fill("fixture-user")
        form.locator('[type="submit"]').click()
        self.assertEqual(self.wrote("PATCH", "/api/admin/data-sources/1"), [])
        form.locator('[name="credentialPassword"]').fill("fixture-password")
        self.fail.add(("PATCH", "/api/admin/data-sources/1"))
        form.locator('[type="submit"]').click()
        expect(self.page.locator("#toastCopy")).to_have_text("Temporary save failure")
        expect(form.locator('[name="name"]')).to_have_value("Original source")
        self.fail.clear()
        form.locator('[type="submit"]').click()
        expect(self.page.locator("#dataSourceModal")).to_have_attribute("aria-hidden", "true")
        saved = self.wrote("PATCH", "/api/admin/data-sources/1")[-1]
        self.assertEqual(saved["agentIds"], [1])
        self.assertEqual(saved["config"]["auth"]["type"], "basic")
        self.assertTrue(saved["config"]["preservedOption"])
        self.assertEqual(saved["credential"], {"username": "fixture-user", "password": "fixture-password"})
        self.page.locator("[data-test-source]").click()
        self.page.wait_for_function("state.dataSources[0].last_test_status==='success'")
        self.page.locator("[data-test-all-sources]").click()
        self.page.wait_for_timeout(1400)
        self.assertGreater(len([c for c in self.calls if c[1].endswith("/test-batch")]), 1)
        self.fail.add(("GET", "/api/admin/data-sources/test-batch"))
        self.page.evaluate("pollSourceTestBatch()")
        expect(self.page.locator("#toastTitle")).to_have_text("Source test status unavailable")
        self.fail.clear()
        self.page.evaluate("clearTimeout(pollSourceTestBatch.timer)")
        self.page.locator("[data-toggle-source]").click()
        expect(self.page.locator("[data-toggle-source]")).to_have_attribute("data-source-status", "paused")
        self.page.locator("#sourceStatusFilter").select_option("active")
        expect(self.page.locator("[data-edit-source]")).to_have_count(0)
        self.page.locator("#sourceStatusFilter").select_option("all")
        self.page.locator("#sourceSearch").fill("not present")
        expect(self.page.locator("[data-edit-source]")).to_have_count(0)
        self.page.locator("#sourceSearch").fill("")
        self.page.locator("[data-delete-source]").click()
        expect(self.page.locator("[data-edit-source]")).to_have_count(0)

    def test_source_create_and_all_auth_modes_preserve_expected_payloads(self):
        self.start("zh", "sources")
        for mode, credential_key in [("bearer", "token"), ("apiKeyHeader", "apiKey"), ("cookie", "cookie")]:
            self.page.locator("[data-create-source]").click()
            form = self.page.locator("#dataSourceForm")
            for name, value in {"publisher": "Fixture", "name": mode, "url": "https://example.org/feed",
                                "sourceType": "Primary", "credentialToken": "isolated-fixture"}.items():
                if name != "credentialToken":
                    form.locator(f'[name="{name}"]').fill(value)
            self.page.locator("#sourceAuthType").select_option(mode)
            form.locator('[name="credentialToken"]').fill("isolated-fixture")
            form.locator('[type="submit"]').click()
            expect(self.page.locator("#dataSourceModal")).to_have_attribute("aria-hidden", "true")
            saved = self.wrote("POST", "/api/admin/data-sources")[-1]
            self.assertEqual(saved["credential"], {credential_key: "isolated-fixture"})
            self.assertEqual(saved["accessModel"], "authenticated")

    def test_settings_failures_ranges_refresh_views_and_logout(self):
        self.start(view="settings")
        expect(self.page.locator(".view-header")).to_contain_text("save preferences only")
        expect(self.page.locator("#adminApp")).to_contain_text("Alert delivery is not configured")
        toggle = self.page.locator('[data-setting="automatic_json_ld"]')
        self.fail.add(("PATCH", "/api/admin/settings/automatic_json_ld"))
        toggle.click()
        expect(self.page.locator("#toastCopy")).to_have_text("Temporary save failure")
        expect(toggle).to_have_class("toggle on")
        expect(toggle).to_be_enabled()
        self.fail.clear()
        toggle.click()
        expect(toggle).to_have_class("toggle ")
        self.assertEqual(self.settings["automatic_json_ld"]["value"], False)
        self.page.locator('[data-view="dashboard"]').click()
        self.page.locator('[data-range="7d"]').click()
        self.page.wait_for_function("state.range==='7d'")
        expect(self.page.locator('[data-range="7d"]')).to_be_enabled()
        self.page.locator("#rangeStart").fill("2026-09-01")
        self.page.locator("#rangeEnd").fill("2026-09-20")
        with self.page.expect_response(lambda r: "start=2026-09-01&end=2026-09-20" in r.url):
            self.page.locator("[data-apply-custom-range]").click()
        self.page.wait_for_function("state.range==='custom'")
        self.assertTrue(any("start=2026-09-01&end=2026-09-20" in c[3] for c in self.calls))
        for view, refresh in [("jobs", "#refreshJobs"), ("research", "#refreshResearch")]:
            self.page.locator(f'[data-view="{view}"]').click()
            self.page.locator(refresh).click()
            expect(self.page.locator(refresh)).to_be_enabled()
        self.page.locator("#logoutButton").click()
        expect(self.page.locator("#loginScreen")).to_be_visible()
        self.page.reload()
        expect(self.page.locator("#loginScreen")).to_be_visible()


if __name__ == "__main__":
    unittest.main()
