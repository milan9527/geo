#!/usr/bin/env python3
"""Full categories and paginated public APIs survive the Aurora response limit."""
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
from backend import i18n
from backend.database import SCHEMA
from backend.bilingual import ensure_schema
from scripts.test_admin_metrics import SizeLimitedConnection
from scripts.check_search_indexing import Page


@unittest.skipUnless(os.environ.get("PUBLICATION_TEST_DATABASE_URL"), "local PostgreSQL URL not set")
class PublicCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.url = os.environ["PUBLICATION_TEST_DATABASE_URL"]
        cls.schema = "catalog_test_" + uuid.uuid4().hex
        with psycopg.connect(cls.url, autocommit=True) as conn:
            conn.execute(f'CREATE SCHEMA "{cls.schema}"')
        with cls.connect() as conn:
            for statement in SCHEMA.split(";")[:3]:
                conn.execute(statement)
            ensure_schema(conn)
            conn.execute("""INSERT INTO categories(slug,name,eyebrow,description,accent)
                            VALUES('ai','AI','AI','Evidence-led research and analysis','teal')""")
            for i in range(65):
                conn.execute("""INSERT INTO articles(category_id,slug,title,dek,summary,author,author_role,
                    read_minutes,published_at,updated_at,status,featured,hero_style,authority_score,keywords,body_json)
                    VALUES(1,%s,%s,'Evidence summary','Findings','Editor','Researcher',5,
                    '2026-09-25','2026-09-25',%s,%s,'evidence',95,'[]',%s)""",
                    (f"article-{i}", f"Evidence subject {i}", "draft" if i == 64 else "published",
                     i % 2 == 0, json.dumps([{"heading": "Evidence", "paragraphs": ["Original evidence " * 3000]}])))
        with patch("backend.database.init_db"):
            cls.app = importlib.import_module("backend.app")

    @classmethod
    def connect(cls):
        return psycopg.connect(cls.url, options=f"-c search_path={cls.schema}", row_factory=dict_row)

    @classmethod
    def tearDownClass(cls):
        with psycopg.connect(cls.url, autocommit=True) as conn:
            conn.execute(f'DROP SCHEMA "{cls.schema}" CASCADE')

    @contextmanager
    def limited_connection(self):
        with self.connect() as conn:
            limited = SizeLimitedConnection(conn)
            yield limited
            self.sizes.extend(limited.response_sizes)

    def setUp(self):
        self.sizes = []
        self.lang_token = i18n.LANGUAGE.set("zh")
        self.cache_token = i18n.CACHE.set({})

    def tearDown(self):
        i18n.LANGUAGE.reset(self.lang_token)
        i18n.CACHE.reset(self.cache_token)

    def call(self, method, *args):
        handler = self.app.ApiHandler.__new__(self.app.ApiHandler)
        captured = []
        handler._json = lambda payload, status=200: captured.append((status, payload))
        handler._html = lambda payload, status=200, **kwargs: captured.append((status, payload))
        # Keep page-shell category navigation inside the same disposable database.
        with patch.object(self.app, "connection", self.limited_connection):
            getattr(handler, method)(*args)
        return captured[0]

    def test_large_category_includes_every_published_article(self):
        with self.connect() as conn:
            with self.assertRaisesRegex(RuntimeError, "size limit 1 MB"):
                SizeLimitedConnection(conn).execute("SELECT * FROM articles WHERE status='published'")
        status, markup = self.call("_category_page", "ai")
        self.assertEqual(status, 200)
        page = Page()
        page.feed(markup)
        self.assertEqual(page.article_paths, {f"/article/article-{i}" for i in range(64)})
        self.assertIn("64 篇已发布研究", markup)
        self.assertLess(max(self.sizes), 400000)

    def test_pagination_has_no_missing_or_duplicate_rows_and_respects_filters(self):
        result = []
        for offset in [0, 30, 60]:
            status, page = self.call("_articles", {"offset": [str(offset)], "category": ["ai"]})
            self.assertEqual(status, 200)
            result.extend(item["slug"] for item in page)
        self.assertEqual(len(result), 64)
        self.assertEqual(set(result), {f"article-{i}" for i in range(64)})
        status, featured = self.call("_articles", {"featured": ["true"], "limit": ["100"]})
        self.assertEqual(len(featured), 32)
        self.assertTrue(all(int(a["slug"].split("-")[1]) % 2 == 0 for a in featured))
        self.assertEqual(self.call("_articles", {"offset": ["100"]}), (200, []))
        self.assertEqual(self.call("_articles", {"category": ["unknown"]}), (200, []))
        self.assertLess(max(self.sizes), 400000)

    def test_invalid_pagination_is_a_client_error_and_limit_is_bounded(self):
        for query in [{"limit": ["oops"]}, {"offset": ["1.5"]}]:
            self.assertEqual(self.call("_articles", query)[0], 400)
        self.assertEqual(len(self.call("_articles", {"limit": ["-5"]})[1]), 1)
        self.assertEqual(len(self.call("_articles", {"limit": ["100000"]})[1]), 64)

    def test_search_handles_large_content_without_exposing_drafts(self):
        status, results = self.call("_search", {"q": ["Evidence"]})
        self.assertEqual(status, 200)
        self.assertEqual(len(results), 20)
        self.assertNotIn("article-64", [a["slug"] for a in results])
        self.assertEqual(self.call("_search", {"q": ["absent"]}), (200, []))
        self.assertEqual(self.call("_search", {"q": [""]}), (200, []))


if __name__ == "__main__":
    unittest.main()
