#!/usr/bin/env python3
"""Exercise permanent published URLs and admin review requirements on local SQL."""

from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import importlib
import json
import os
from pathlib import Path
import sys
from threading import Thread
import unittest
from unittest.mock import patch
import uuid

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.database import SCHEMA
from backend.publication_protection import ensure_publication_protection
from backend.article_redirects import ensure_article_redirects


@unittest.skipUnless(os.environ.get("PUBLICATION_TEST_DATABASE_URL"), "local PostgreSQL URL not set")
class PublicationProtectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.url = os.environ["PUBLICATION_TEST_DATABASE_URL"]
        cls.schema = "protection_test_" + uuid.uuid4().hex
        with psycopg.connect(cls.url, autocommit=True) as conn:
            conn.execute(f'CREATE SCHEMA "{cls.schema}"')
        with cls.connect() as conn:
            for statement in SCHEMA.split(";")[:3]:
                conn.execute(statement)
            ensure_publication_protection(conn)
            ensure_article_redirects(conn)
            conn.execute("""INSERT INTO categories(slug,name,eyebrow,description,accent)
                            VALUES('agent','Agent 技术','AGENT','分类说明','teal')""")
        with patch("backend.database.init_db"):
            cls.app = importlib.import_module("backend.app")
        cls.patches = [
            patch.object(cls.app, "connection", cls.connect),
            patch.object(cls.app, "submit_indexing", return_value=True),
            patch.object(cls.app.ApiHandler, "_require_admin", return_value=True),
        ]
        for item in cls.patches:
            item.start()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), cls.app.ApiHandler)
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        for item in reversed(cls.patches):
            item.stop()
        with psycopg.connect(cls.url, autocommit=True) as conn:
            conn.execute(f'DROP SCHEMA "{cls.schema}" CASCADE')

    @classmethod
    def connect(cls):
        return psycopg.connect(cls.url, options=f"-c search_path={cls.schema}", row_factory=dict_row)

    def setUp(self):
        # Never truncate production. Each test owns a disposable local schema.
        with self.connect() as conn:
            conn.execute("TRUNCATE article_redirects, sources, articles RESTART IDENTITY")
            for slug, status in [("existing", "published"), ("new-draft", "draft"), ("pending", "review")]:
                conn.execute(
                    """INSERT INTO articles(
                      category_id,slug,title,dek,summary,author,author_role,read_minutes,
                      published_at,updated_at,status,hero_style,authority_score,keywords,body_json
                    ) VALUES(1,%s,'原有研究标题','研究摘要','已有公开内容','研究编辑部','编辑',8,
                      '2026-09-09','2026-09-09',%s,'evidence',95,'[]','[]')""",
                    (slug, status),
                )
            conn.execute("""INSERT INTO sources(article_id,publisher,title,url,published_at,source_type)
                            VALUES(1,'来源机构','证据','https://example.com/source','2026-09-09','primary')""")

    def request(self, method, path, body=None):
        client = HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        try:
            client.request(method, path, json.dumps(body) if body is not None else None,
                           {"Content-Type": "application/json"})
            response = client.getresponse()
            return response.status, response.read().decode()
        finally:
            client.close()

    def test_sql_cannot_delete_unpublish_or_rename_published_article(self):
        for sql in [
            "DELETE FROM articles WHERE id=1",
            "UPDATE articles SET status='review' WHERE id=1",
            "UPDATE articles SET status='draft' WHERE id=1",
            "UPDATE articles SET slug='different-url' WHERE id=1",
        ]:
            with self.subTest(sql=sql):
                with self.assertRaises(psycopg.errors.CheckViolation):
                    with self.connect() as conn:
                        conn.execute(sql)
        with self.connect() as conn:
            self.assertEqual(conn.execute("SELECT status,slug FROM articles WHERE id=1").fetchone(),
                             {"status": "published", "slug": "existing"})
            self.assertEqual(conn.execute("SELECT count(*) n FROM sources WHERE article_id=1").fetchone()["n"], 1)

    def test_corrections_preserve_url_and_reviewed_new_publication_remains_possible(self):
        with self.connect() as conn:
            conn.execute("UPDATE articles SET title='修订后的研究标题' WHERE id=1")
            conn.execute("UPDATE articles SET status='published' WHERE id=2")
            self.assertEqual(conn.execute("SELECT slug,status FROM articles WHERE id=2").fetchone(),
                             {"slug": "new-draft", "status": "published"})
        status, page = self.request("GET", "/article/existing")
        self.assertEqual(status, 200)
        self.assertIn("修订后的研究标题", page)
        self.assertIn('/article/existing"', page)

    def test_batch_delete_or_review_is_atomic_when_selection_contains_public_page(self):
        for action in ["delete", "review"]:
            status, body = self.request("PATCH", "/api/admin/articles/batch",
                                        {"ids": [1, 2], "action": action, "confirm": True})
            self.assertEqual(status, 409, body)
        with self.connect() as conn:
            self.assertEqual([r["status"] for r in conn.execute("SELECT status FROM articles ORDER BY id")],
                             ["published", "draft", "review"])

    def test_single_article_cannot_be_unpublished_but_pricing_can_change(self):
        for status in ["draft", "review"]:
            code, body = self.request("PATCH", "/api/admin/articles/1", {"status": status})
            self.assertEqual(code, 409, body)
        code, body = self.request("PATCH", "/api/admin/articles/1", {"agent_price": 0.01})
        self.assertEqual(code, 200, body)
        self.assertEqual(self.request("HEAD", "/article/existing"), (200, ""))

    def test_all_admin_shortcuts_to_publish_are_rejected(self):
        for method, path, body in [
            ("POST", "/api/admin/articles", {"title": "未经审核的新文章", "categorySlug": "agent",
             "author": "作者", "dek": "摘要", "status": "published"}),
            ("PATCH", "/api/admin/articles/2", {"status": "published"}),
            ("PATCH", "/api/admin/articles/batch", {"ids": [1, 2], "action": "publish"}),
        ]:
            with self.subTest(path=path):
                code, response = self.request(method, path, body)
                self.assertEqual(code, 409, response)
        with self.connect() as conn:
            self.assertEqual(conn.execute("SELECT count(*) n FROM articles WHERE status='published'").fetchone()["n"], 1)

    def test_unpublished_draft_cleanup_does_not_remove_a_public_page(self):
        with self.connect() as conn:
            conn.execute("DELETE FROM articles WHERE id=2")
            self.assertEqual(conn.execute("SELECT count(*) n FROM articles WHERE status='published'").fetchone()["n"], 1)

    def add_redirect(self):
        with self.connect() as conn:
            conn.execute("""INSERT INTO article_redirects VALUES
                ('pending',3,1,'source-hash','target-hash','{}','2026-09-14')""")

    def test_legacy_get_head_json_and_agent_routes_redirect_in_one_hop(self):
        self.add_redirect()
        for method in ["GET", "HEAD"]:
            for prefix, suffix in [("/article/", ""), ("/api/v1/articles/", ""),
                                   ("/agent/v1/articles/", ""), ("/agent/v1/articles/", "/paid")]:
                for query in ["", "?utm_source=old"]:
                    with self.subTest(method=method, prefix=prefix, suffix=suffix, query=query):
                        client = HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
                        try:
                            client.request(method, prefix + "pending" + suffix + query)
                            response = client.getresponse()
                            self.assertEqual(response.status, 301)
                            self.assertEqual(response.getheader("Location"),
                                             self.app.PUBLIC_BASE_URL + prefix + "existing" + suffix)
                            self.assertEqual(response.read(), b"")
                        finally:
                            client.close()
        self.assertEqual(self.request("GET", "/article/existing")[0], 200)
        self.assertEqual(self.request("GET", "/article/unknown")[0], 404)
        self.assertEqual(self.request("GET", "/api/v1/articles/unknown")[0], 404)

    def test_redirect_sources_cannot_be_deleted_renamed_or_republished(self):
        self.add_redirect()
        for sql, error in [
            ("DELETE FROM articles WHERE id=3", psycopg.errors.ForeignKeyViolation),
            ("UPDATE articles SET slug='replacement' WHERE id=3", psycopg.errors.CheckViolation),
            ("UPDATE articles SET status='published' WHERE id=3", psycopg.errors.CheckViolation),
        ]:
            with self.subTest(sql=sql), self.assertRaises(error):
                with self.connect() as conn:
                    conn.execute(sql)
        code, body = self.request("PATCH", "/api/admin/articles/batch",
                                  {"ids": [2, 3], "action": "delete", "confirm": True})
        self.assertEqual(code, 409, body)
        with self.connect() as conn:
            self.assertEqual(conn.execute("SELECT count(*) n FROM articles").fetchone()["n"], 3)

    def test_redirects_reject_unpublished_targets_wrong_slugs_and_cycles(self):
        for slug, source, target in [("pending", 3, 2), ("wrong", 3, 1),
                                     ("existing", 1, 3), ("pending", 3, 3)]:
            with self.subTest(slug=slug, source=source, target=target):
                with self.assertRaises(psycopg.errors.CheckViolation):
                    with self.connect() as conn:
                        conn.execute("""INSERT INTO article_redirects VALUES
                            (%s,%s,%s,'hash','hash','{}','2026-09-14')""", (slug, source, target))


if __name__ == "__main__":
    unittest.main()
