#!/usr/bin/env python3
"""Browser regressions: data errors must not turn successful logins into failures."""

import json
from pathlib import Path
import unittest
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1] / "frontend" / "admin"
USER = {"id": 1, "username": "test-admin", "displayName": "Test Admin", "role": "administrator"}
METRICS = {
    "summary": dict.fromkeys([
        "humanViews", "agentViews", "humanRequests", "agentRequests", "agentShare",
        "citations", "citationRate", "revenue",
    ], 0),
    "abTest": dict.fromkeys([
        "internalPayments", "externalPayments", "variantAViews", "variantBViews", "challenges",
        "paymentSuccessRate", "payments", "paymentAttempts", "revenue", "externalRevenue",
        "unpaidChallengesEstimate", "verificationFailures", "settlementFailures", "serviceErrors",
        "internalRevenue", "uniquePayers", "confirmedTransactions", "conversionRate",
    ], 0),
    "growth": dict.fromkeys(["agent", "human", "citations", "revenue"], 0),
    "startDate": "2026-09-01", "endDate": "2026-09-13",
    "daily": [], "agentSources": [], "content": {"published": 17},
    "crawlers": {"running": 0, "total": 6, "pagesToday": 0},
}


class AdminLoginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True, args=["--no-proxy-server"])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.context = self.browser.new_context()
        if "bilingual" not in self._testMethodName:
            self.context.add_init_script("localStorage.setItem('aperture-admin-language', 'zh')")
        self.page = self.context.new_page()
        self.authenticated = False
        self.login_status = 200
        self.metrics_status = 200
        self.login_requests = 0
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.route("https://admin.test/**", self.route)

    def tearDown(self):
        self.assertEqual(self.errors, [])
        self.context.close()

    def route(self, route):
        path = urlsplit(route.request.url).path
        if not path.startswith("/api/"):
            file = ROOT / (path.lstrip("/") or "index.html")
            types = {".html": "text/html", ".js": "text/javascript", ".css": "text/css"}
            if file.is_file():
                route.fulfill(path=file, content_type=types.get(file.suffix, "text/plain"))
            else:
                route.fulfill(status=404)
            return
        status = 200
        payload = []
        if path == "/api/admin/auth/me":
            status = 200 if self.authenticated else 401
            payload = {"authenticated": self.authenticated, "user": USER}
        elif path == "/api/admin/auth/login":
            self.login_requests += 1
            status = self.login_status
            self.authenticated = status == 200
            payload = {"authenticated": True, "user": USER} if self.authenticated else {"error": "用户名或密码错误"}
        elif path == "/api/admin/metrics":
            status = self.metrics_status
            payload = METRICS if status == 200 else {"error": f"API {status}"}
        elif path.endswith("/settings") or path.endswith("/test-batch"):
            payload = {}
        route.fulfill(status=status, content_type="application/json", body=json.dumps(payload))

    def login(self):
        self.page.goto("https://admin.test/")
        expect(self.page.locator("#loginScreen")).to_be_visible()
        self.page.locator('[name="username"]').fill("test-admin")
        self.page.locator('[name="password"]').fill("test-password")
        self.page.locator("#loginButton").click()

    def assert_load_error(self):
        expect(self.page.locator("#adminShell")).to_be_visible()
        expect(self.page.locator("#loginScreen")).to_be_hidden()
        expect(self.page.locator("#adminApp [role=alert]")).to_contain_text("登录会话仍然有效")

    def test_default_english_and_bilingual_switch_keep_login_session(self):
        self.page.goto("https://admin.test/")
        expect(self.page.locator("html")).to_have_attribute("lang", "en")
        expect(self.page.locator("#loginTitle")).to_have_text("Log in to the admin console")
        self.login()
        expect(self.page.locator("#adminShell")).to_be_visible()
        self.page.locator("[data-language-picker]").select_option("zh")
        expect(self.page.locator("html")).to_have_attribute("lang", "zh-CN")
        expect(self.page.locator("#adminShell")).to_be_visible()
        self.page.locator("[data-language-picker]").select_option("en")
        expect(self.page.locator("html")).to_have_attribute("lang", "en")
        expect(self.page.locator("#adminShell")).to_be_visible()
        self.assertEqual(self.login_requests, 1)

    def test_successful_login_survives_502_and_retries_without_reauthentication(self):
        self.metrics_status = 502
        self.login()
        self.assert_load_error()
        self.page.locator('[data-view="content"]').click()
        self.assert_load_error()
        self.metrics_status = 200
        self.page.locator("[data-retry-admin]").click()
        expect(self.page.locator("#adminApp")).to_contain_text("专业内容库")
        self.assertEqual(self.login_requests, 1)

    def test_restored_session_survives_502_and_refresh_recovers_dashboard(self):
        self.authenticated = True
        self.metrics_status = 502
        self.page.goto("https://admin.test/")
        self.assert_load_error()
        self.metrics_status = 200
        self.page.locator("#refreshButton").click()
        expect(self.page.locator("#pageTitle")).to_have_text("GEO 运营总览")
        expect(self.page.locator(".metric-grid")).to_be_visible()
        self.assertEqual(self.login_requests, 0)

    def test_invalid_password_stays_on_login_screen(self):
        self.login_status = 401
        self.login()
        expect(self.page.locator("#loginError")).to_have_text("用户名或密码错误")
        expect(self.page.locator("#adminShell")).to_be_hidden()

    def test_expired_session_requires_login(self):
        self.authenticated = True
        self.metrics_status = 401
        self.page.goto("https://admin.test/")
        expect(self.page.locator("#loginError")).to_contain_text("会话已过期")
        expect(self.page.locator("#adminShell")).to_be_hidden()

    def test_successful_login_loads_dashboard_and_restores_after_reload(self):
        self.login()
        expect(self.page.locator(".metric-grid")).to_be_visible()
        self.page.reload()
        expect(self.page.locator(".metric-grid")).to_be_visible()
        self.assertEqual(self.login_requests, 1)


if __name__ == "__main__":
    unittest.main()
