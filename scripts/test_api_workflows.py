#!/usr/bin/env python3
"""Real HTTP/SQL workflows; AWS writes and payment settlement are simulated."""
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import importlib
import json
import os
from pathlib import Path
import sys
from threading import Thread
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.database import SCHEMA
from backend.bilingual import ensure_schema
from backend.auth import hash_password
from backend.x402_payment import HandlerAdapter
from x402.http import HTTPResponseInstructions


@unittest.skipUnless(os.environ.get("PUBLICATION_TEST_DATABASE_URL"), "local PostgreSQL URL not set")
class ApiWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.url = os.environ["PUBLICATION_TEST_DATABASE_URL"]
        cls.schema = "api_workflow_" + uuid.uuid4().hex
        with psycopg.connect(cls.url, autocommit=True) as conn:
            conn.execute(f'CREATE SCHEMA "{cls.schema}"')
        with cls.connect() as conn:
            for sql in SCHEMA.split(";"):
                if sql.strip():
                    conn.execute(sql)
            ensure_schema(conn)
            conn.execute("""INSERT INTO categories VALUES(1,'ai','AI','AI','Evidence-led research','teal',0);
                INSERT INTO crawler_agents(id,name,slug,kind,industries,status,schedule)
                VALUES(1,'Research','research','Browser','[]','running','0 * * * *');
                INSERT INTO articles(category_id,slug,title,dek,summary,author,author_role,read_minutes,
                published_at,updated_at,status,hero_style,authority_score,keywords,body_json)
                VALUES(1,'existing','Evidence','Research summary','Findings','Editor','Researcher',5,
                '2026-09-25','2026-09-25','published','evidence',95,'[]',
                '[{"heading":"Evidence","paragraphs":["Original conclusion [S1]"]}]')""")
        with patch("backend.database.init_db"):
            cls.app = importlib.import_module("backend.app")
        cls.patches = [
            patch.object(cls.app, "connection", cls.connect),
            patch.object(cls.app, "submit_indexing", return_value=True),
            patch.object(cls.app, "eventbridge_schedule_states", return_value={}),
        ]
        for item in cls.patches:
            item.start()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), cls.app.ApiHandler)
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def connect(cls):
        return psycopg.connect(cls.url, options=f"-c search_path={cls.schema}", row_factory=dict_row)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        for item in reversed(cls.patches):
            item.stop()
        with psycopg.connect(cls.url, autocommit=True) as conn:
            conn.execute(f'DROP SCHEMA "{cls.schema}" CASCADE')

    def setUp(self):
        self.cookie = ""
        digest, salt, iterations = hash_password("fixture-password", iterations=1000)
        with self.connect() as conn:
            conn.execute("TRUNCATE admin_users,admin_sessions,data_sources,agent_source_assignments,traffic_events,app_settings RESTART IDENTITY")
            conn.execute("""INSERT INTO admin_users(username,display_name,password_hash,password_salt,
                password_iterations,created_at,updated_at) VALUES('fixture','Fixture',%s,%s,%s,'2026-09-25','2026-09-25')""",
                (digest, salt, iterations))
            conn.execute("INSERT INTO app_settings VALUES('automatic_json_ld','true','boolean','2026-09-25')")
            conn.execute("UPDATE crawler_agents SET status='running',schedule='0 * * * *'")
        status, _, _ = self.request("POST", "/api/admin/auth/login",
                                   {"username": "fixture", "password": "fixture-password"})
        self.assertEqual(status, 200)

    def request(self, method, path, payload=None, *, anonymous=False, headers=None):
        client = HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        request_headers = {"Content-Type": "application/json", "User-Agent": "Aperture-Functional-Verification"}
        if self.cookie and not anonymous:
            request_headers["Cookie"] = self.cookie
        request_headers.update(headers or {})
        try:
            client.request(method, path, json.dumps(payload) if payload is not None else None, request_headers)
            response = client.getresponse()
            result_headers = dict(response.getheaders())
            cookie = response.getheader("Set-Cookie")
            if cookie:
                self.cookie = cookie.split(";", 1)[0]
            raw = response.read().decode()
            body = json.loads(raw) if "application/json" in response.getheader("Content-Type", "") and raw else raw
            return response.status, body, result_headers
        finally:
            client.close()

    def test_authentication_cookie_logout_expiry_and_all_admin_routes_require_session(self):
        self.assertEqual(self.request("GET", "/api/admin/auth/me")[0], 200)
        for method, path, payload in [
            ("GET", "/api/admin/articles", None), ("GET", "/api/admin/metrics", None),
            ("GET", "/api/admin/research", None), ("GET", "/api/admin/jobs", None),
            ("GET", "/api/admin/events", None), ("GET", "/api/admin/data-sources", None),
            ("GET", "/api/admin/crawlers", None), ("GET", "/api/admin/settings", None),
            ("POST", "/api/admin/articles", {}), ("POST", "/api/admin/crawlers/run-all", {}),
            ("PATCH", "/api/admin/settings/automatic_json_ld", {"value": False}),
            ("DELETE", "/api/admin/data-sources/1", None),
        ]:
            self.assertEqual(self.request(method, path, payload, anonymous=True)[0], 401, path)
        self.assertEqual(self.request("POST", "/api/admin/auth/login",
                                     {"username": "fixture", "password": "wrong"})[0], 401)
        status, _, headers = self.request("POST", "/api/admin/auth/logout", {})
        self.assertEqual(status, 200)
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertIn("SameSite=Strict", headers["Set-Cookie"])
        self.assertEqual(self.request("GET", "/api/admin/auth/me")[0], 401)

    def source_payload(self, **extra):
        return {"publisher": "研究编辑部", "name": "金融市场", "url": "https://example.org/feed",
                "sourceType": "官方发布", "categorySlug": "ai", "ingestionMethod": "feed",
                "notes": "研究摘要", "agentIds": [1], **extra}

    def test_registry_create_duplicate_edit_assignment_and_delete_round_trip(self):
        status, result, _ = self.request("POST", "/api/admin/data-sources", self.source_payload())
        self.assertEqual(status, 201)
        source_id = result["sourceId"]
        self.assertEqual(self.request("POST", "/api/admin/data-sources", self.source_payload())[0], 409)
        status, sources, _ = self.request("GET", "/api/admin/data-sources?lang=en")
        self.assertEqual(status, 200)
        self.assertEqual(sources[0]["name"], "金融市场")
        self.assertEqual(sources[0]["notes"], "研究摘要")
        self.assertEqual(sources[0]["agentIds"], [1])
        self.assertEqual(self.request("PATCH", f"/api/admin/data-sources/{source_id}",
                                     {"status": "paused", "agentIds": []})[0], 200)
        self.assertEqual(self.request("GET", "/api/admin/data-sources")[1][0]["agentIds"], [])
        self.assertEqual(self.request("DELETE", f"/api/admin/data-sources/{source_id}")[0], 200)
        self.assertEqual(self.request("GET", "/api/admin/data-sources")[1], [])
        self.assertEqual(self.request("DELETE", f"/api/admin/data-sources/{source_id}")[0], 404)

    def test_registry_validation_rolls_back_and_secrets_are_not_stored_in_sql(self):
        for extra in [{"trustTier": 9}, {"agentIds": [999]}, {"url": "http://example.org"},
                      {"accessModel": "authenticated", "config": {"auth": {"type": "bearer"}}}]:
            self.assertEqual(self.request("POST", "/api/admin/data-sources", self.source_payload(**extra))[0], 400)
        self.assertEqual(self.request("GET", "/api/admin/data-sources")[1], [])
        with patch.object(self.app, "put_source_secret", return_value="arn:aws:secretsmanager:us-east-1:123:secret:fixture") as put:
            status, _, _ = self.request("POST", "/api/admin/data-sources",
                self.source_payload(accessModel="authenticated", config={"auth": {"type": "bearer"}},
                                    credential={"token": "isolated-fixture-token"}))
        self.assertEqual(status, 201)
        put.assert_called_once()
        self.assertNotIn("isolated-fixture-token", json.dumps(self.request("GET", "/api/admin/data-sources")[1]))
        with self.connect() as conn:
            self.assertNotIn("isolated-fixture-token", json.dumps(conn.execute("SELECT * FROM data_sources").fetchall()))

    def test_schedule_sync_failure_preserves_sql_and_manual_dispatch_is_bounded(self):
        with patch.object(self.app, "sync_eventbridge_schedule", side_effect=RuntimeError("fixture failure")):
            self.assertEqual(self.request("PATCH", "/api/admin/crawlers/1", {"schedule": "45 13 * * *"})[0], 502)
        with self.connect() as conn:
            self.assertEqual(conn.execute("SELECT schedule FROM crawler_agents WHERE id=1").fetchone()["schedule"], "0 * * * *")
        with patch.object(self.app, "sync_eventbridge_schedule", return_value={"state": "ENABLED"}) as sync:
            self.assertEqual(self.request("PATCH", "/api/admin/crawlers/1", {"schedule": "45 13 * * *"})[0], 200)
            self.assertEqual(sync.call_args.kwargs["schedule_expression"], "cron(45 13 * * ? *)")
            self.assertEqual(self.request("PATCH", "/api/admin/crawlers/1", {"schedule": "invalid"})[0], 400)
        with patch.object(self.app, "USE_AURORA_DATA_API", True), patch.object(
            self.app, "invoke_crawler_bridge", return_value={"submitted": True, "result": {"jobId": 42}}
        ) as invoke:
            self.assertEqual(self.request("POST", "/api/admin/crawlers/1/run", {"allowPayment": False})[0], 202)
            self.assertFalse(invoke.call_args.kwargs["allow_payment"])
            self.assertEqual(self.request("POST", "/api/admin/crawlers/run-all", {})[0], 202)
            self.assertTrue(invoke.call_args.kwargs["asynchronous"])

    def test_boolean_settings_require_boolean_values(self):
        for value in ["false", None, 0, {}]:
            self.assertEqual(self.request("PATCH", "/api/admin/settings/automatic_json_ld", {"value": value})[0], 400)
        self.assertEqual(self.request("PATCH", "/api/admin/settings/automatic_json_ld", {"value": False})[0], 200)
        with self.connect() as conn:
            self.assertEqual(conn.execute("SELECT value FROM app_settings WHERE key='automatic_json_ld'").fetchone()["value"], "false")

    def test_agent_editions_and_unpaid_invalid_failed_and_successful_settlements(self):
        paid_path = "/agent/v1/articles/existing/paid"
        for language in ["en", "zh"]:
            suffix = "?lang=" + language
            status, result, _ = self.request("GET", "/agent/v1/articles/existing" + suffix)
            self.assertEqual(status, 200)
            self.assertEqual(result["license"]["variants"]["B"], paid_path + suffix)
            self.assertEqual(result["inLanguage"], "en" if language == "en" else "zh-CN")
            adapter = HandlerAdapter(SimpleNamespace(path=paid_path + suffix, headers={"Host": "example.org"}), paid_path)
            self.assertTrue(adapter.get_url().endswith(paid_path + suffix))
            prefix = "/zh" if language == "zh" else ""
            status, markup, _ = self.request("GET", prefix + "/article/existing")
            self.assertEqual(status, 200)
            self.assertIn(f'data-machine-url="{self.app.PUBLIC_BASE_URL}{paid_path}{suffix}"', markup)
            self.assertIn("复制 x402 B 页地址" if language == "zh" else "Copy x402 B URL", markup)
        for supplied in [False, True]:
            instruction = HTTPResponseInstructions(status=402, headers={"PAYMENT-REQUIRED": "fixture"}, body={"error": "payment required"})
            request = SimpleNamespace(price_usd=0.002, process_result=SimpleNamespace(type="payment-required", response=instruction))
            with patch.object(self.app, "process_paid_request", return_value=request):
                status, _, headers = self.request("GET", paid_path, headers={"PAYMENT-SIGNATURE": "invalid"} if supplied else {})
            self.assertEqual(status, 402)
            self.assertIn("noindex", headers["X-Robots-Tag"])
        requirements = SimpleNamespace(network="eip155:84532", amount="2000", asset="fixture", pay_to="fixture")
        request = SimpleNamespace(price_usd=0.002, process_result=SimpleNamespace(type="payment-verified", payment_requirements=requirements))
        for success in [False, True]:
            settlement = SimpleNamespace(success=success, network="eip155:84532", transaction="fixture-transaction",
                payer="fixture-payer", error_reason="fixture failure", response=None, headers={"PAYMENT-RESPONSE": "fixture"})
            with patch.object(self.app, "process_paid_request", return_value=request), patch.object(
                self.app, "settle_paid_request", return_value=settlement
            ):
                status, result, headers = self.request("GET", paid_path + "?lang=zh")
            self.assertEqual(status, 200 if success else 503)
            if success:
                self.assertTrue(result["settlement"]["success"])
                self.assertEqual(result["inLanguage"], "zh-CN")
                self.assertIn("no-store", headers["Cache-Control"])
        with self.connect() as conn:
            events = [r["event_type"] for r in conn.execute("SELECT event_type FROM traffic_events ORDER BY id")]
        self.assertEqual(events, ["x402_challenge", "x402_verification_failed", "x402_settlement_failed", "x402_payment"])


if __name__ == "__main__":
    unittest.main()
