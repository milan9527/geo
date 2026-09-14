#!/usr/bin/env python3
"""Exercise research list/detail beyond Data API limits on real PostgreSQL."""

from contextlib import contextmanager
import importlib
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import uuid

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.database import SCHEMA
from backend.research import read_research_rows
from scripts.test_admin_metrics import SizeLimitedConnection


@unittest.skipUnless(os.environ.get("RESEARCH_TEST_DATABASE_URL"), "local PostgreSQL URL not set")
class AdminResearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.url = os.environ["RESEARCH_TEST_DATABASE_URL"]
        cls.schema = "research_test_" + uuid.uuid4().hex
        with psycopg.connect(cls.url, autocommit=True) as conn:
            conn.execute(f'CREATE SCHEMA "{cls.schema}"')
        with cls.connect() as conn:
            for statement in SCHEMA.split(";"):
                if statement.strip():
                    conn.execute(statement)
            conn.execute("""INSERT INTO crawler_agents(id,name,slug,kind,industries,status,schedule)
                            VALUES(1,'研究员','research','research','[]','idle','manual')""")
        with patch("backend.database.init_db"):
            cls.app = importlib.import_module("backend.app")

    @classmethod
    def tearDownClass(cls):
        with psycopg.connect(cls.url, autocommit=True) as conn:
            conn.execute(f'DROP SCHEMA "{cls.schema}" CASCADE')

    @classmethod
    def connect(cls):
        return psycopg.connect(cls.url, options=f"-c search_path={cls.schema}", row_factory=dict_row)

    def setUp(self):
        self.audit = {
            "status": "verified", "score": 94, "completeArticle": True,
            "semanticDeduplication": {"pairReviews": [
                {"a": i, "reason": '中文证据 😀 "quoted" \\backslash\nnext line\t' * 300}
                for i in range(8)
            ]},
        }
        with self.connect() as conn:
            conn.execute("TRUNCATE research_evidence,research_runs RESTART IDENTITY")
            for i in range(1, 24):
                conn.execute(
                    """INSERT INTO research_runs(id,agent_id,status,category_slug,topic,started_at,
                       verification_json,analysis_process_json,tool_trace_json)
                       VALUES(%s,1,'completed','agent',%s,'2026-09-14T00:00:00Z',%s,%s,%s)""",
                    (i, f"研究 {i}", json.dumps(self.audit, ensure_ascii=False),
                     json.dumps([{"step": "核验", "result": '多行\n"内容"'}], ensure_ascii=False),
                     json.dumps({"documents": 8, "provider": "test"})),
                )
            conn.execute("""INSERT INTO research_evidence(run_id,publisher,title,url,published_at,
                            retrieved_at,source_type,content_excerpt,data_json)
                            VALUES(23,'证据机构','原始证据','https://example.com/source',
                            '2026-09-14','2026-09-14','primary','完整正文','{"value":123}')""")
        self.sizes = []

    @contextmanager
    def limited_connection(self):
        with self.connect() as conn:
            limited = SizeLimitedConnection(conn)
            yield limited
            self.sizes.extend(limited.response_sizes)

    def call_handler(self, name, *args):
        handler = self.app.ApiHandler.__new__(self.app.ApiHandler)
        captured = []
        handler._json = lambda payload, status=200: captured.append((payload, status))
        with patch.object(self.app, "connection", self.limited_connection):
            getattr(handler, name)(*args)
        return captured[0]

    def test_original_list_exceeds_limit_but_all_latest_twenty_audits_survive(self):
        with self.connect() as conn:
            with self.assertRaisesRegex(RuntimeError, "size limit 1 MB"):
                SizeLimitedConnection(conn).execute(
                    "SELECT * FROM research_runs ORDER BY started_at DESC,id DESC LIMIT 20",
                )
        result, status = self.call_handler("_admin_research")
        self.assertEqual(status, 200)
        self.assertEqual([r["id"] for r in result], list(range(23, 3, -1)))
        for run in result:
            self.assertEqual(run["verification"], self.audit)
            self.assertEqual(run["analysisProcess"][0]["result"], '多行\n"内容"')
            self.assertEqual(run["toolTrace"]["documents"], 8)
        self.assertEqual(result[0]["evidence"][0]["content_excerpt"], "完整正文")
        self.assertNotIn("id", result[0]["evidence"][0])
        self.assertLess(max(self.sizes), 400000)

    def test_single_large_audit_and_evidence_are_reassembled_without_truncation(self):
        audit = {**self.audit, "notes": "超大单条审计 😀\n\\\"" * 90000}
        excerpt = "证据原文 🧪\n\\\"" * 90000
        with self.connect() as conn:
            conn.execute("UPDATE research_runs SET verification_json=%s WHERE id=23",
                         (json.dumps(audit, ensure_ascii=False),))
            conn.execute("UPDATE research_evidence SET content_excerpt=%s WHERE run_id=23", (excerpt,))
        result, status = self.call_handler("_admin_research_detail", 23)
        self.assertEqual(status, 200)
        self.assertEqual(result["verification"], audit)
        self.assertEqual(result["evidence"][0]["content_excerpt"], excerpt)
        self.assertEqual(result["evidence"][0]["data"], {"value": 123})
        self.assertLess(max(self.sizes), 400000)

    def test_unknown_detail_remains_404_and_empty_list_remains_empty(self):
        self.assertEqual(self.call_handler("_admin_research_detail", 99999)[1], 404)
        with self.connect() as conn:
            conn.execute("TRUNCATE research_evidence,research_runs")
        self.assertEqual(self.call_handler("_admin_research"), ([], 200))

    def test_updates_between_chunks_do_not_mix_versions_or_change_selected_ids(self):
        with self.connect() as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            limited = SizeLimitedConnection(conn)
            updated = False

            class ConcurrentUpdate:
                def execute(inner, sql, parameters=None):
                    nonlocal updated
                    page = limited.execute(sql, parameters)
                    if not updated:
                        updated = True
                        with self.connect() as writer:
                            writer.execute(
                                """UPDATE research_runs SET verification_json='{"new":true}',
                                   started_at='2026-09-15' WHERE id=5""",
                            )
                    return page

            result = read_research_rows(
                ConcurrentUpdate(),
                "SELECT id,started_at,verification_json FROM research_runs ORDER BY started_at DESC,id DESC LIMIT 20",
                order_by="started_at DESC,id DESC",
            )
        self.assertTrue(updated)
        self.assertEqual([r["id"] for r in result], list(range(23, 3, -1)))
        self.assertTrue(all(json.loads(r["verification_json"]) == self.audit for r in result))


if __name__ == "__main__":
    unittest.main()
