#!/usr/bin/env python3
"""Publication regressions; SQL tests use a disposable schema in a local PostgreSQL."""

from contextlib import contextmanager
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import sys
from threading import Barrier
import unittest
from unittest.mock import Mock
import uuid

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aws_runtime.publication import PublicationStore, content_identity, quality_gate, review_and_publish
from backend.publication_protection import ensure_publication_protection
from backend.article_redirects import ensure_article_redirects


def evidence():
    return [{"publisher": f"publisher-{i % 3}", "title": f"source-{i}",
             "url": f"https://source-{i}.example/release", "publishedAt": "2026-09-09",
             "sourceType": "primary", "excerpt": "实际来源摘录", "data": {}} for i in range(5)]


def output():
    return {"title": "工具执行的新版本", "sections": [
        {"type": "analysis", "paragraphs": ["正文事实与论证[S1][S2][S3][S4][S5]" * 140]}
    ]}


def audit():
    return {"status": "verified", "score": 94, "completeArticle": True,
            "unsupportedClaims": [], "citationIssues": [], "causalityRisks": []}


def distinct(*_):
    return {"relation": "distinct", "confidence": .99, "materialDifferences": ["不同核心事件"],
            "preferredArticleId": None, "reason": "核心证据与结论不同"}


class GateTests(unittest.TestCase):
    def test_score_and_material_issues_cannot_be_bypassed_by_verified_status(self):
        review = audit()
        self.assertTrue(quality_gate(output(), evidence(), review, human_title=True)["ready"])
        for change in [{"score": 89}, {"causalityRisks": ["因果未经验证"]},
                       {"completeArticle": False}, {"citationIssues": "malformed"}]:
            self.assertFalse(quality_gate(output(), evidence(), {**review, **change}, human_title=True)["ready"])

    def test_source_count_is_distinct_and_citations_must_resolve(self):
        self.assertFalse(quality_gate(output(), [evidence()[0]] * 5, audit(), human_title=True)["ready"])
        draft = output()
        draft["sections"][0]["paragraphs"].append("不存在的证据[S9]")
        self.assertFalse(quality_gate(draft, evidence(), audit(), human_title=True)["ready"])
        missing = evidence()
        for item in missing:
            item["excerpt"] = ""
        self.assertFalse(quality_gate(output(), missing, audit(), human_title=True)["ready"])

    def test_failure_gate_never_queries_catalog_or_calls_model(self):
        store, compare = Mock(), Mock()
        result = review_and_publish(store, 1, "hash", {"ready": False}, compare, enabled=True)
        self.assertEqual(result["reason"], "quality_gate_failed")
        store.article.assert_not_called()
        compare.assert_not_called()


@unittest.skipUnless(os.environ.get("PUBLICATION_TEST_DATABASE_URL"), "local PostgreSQL URL not set")
class SqlPublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.url = os.environ["PUBLICATION_TEST_DATABASE_URL"]
        cls.schema = "publication_test_" + uuid.uuid4().hex
        with psycopg.connect(cls.url, autocommit=True) as conn:
            conn.execute(f'CREATE SCHEMA "{cls.schema}"')
        with cls.connect() as conn:
            conn.execute("""
                CREATE TABLE categories(id BIGSERIAL PRIMARY KEY, slug TEXT NOT NULL);
                CREATE TABLE articles(
                  id BIGSERIAL PRIMARY KEY,category_id BIGINT REFERENCES categories(id),
                  slug TEXT UNIQUE NOT NULL,title TEXT NOT NULL,dek TEXT,summary TEXT,
                  author TEXT,author_role TEXT,read_minutes INT,updated_at TEXT NOT NULL,
                  published_at TEXT NOT NULL,hero_style TEXT,authority_score INT,citation_count INT,
                  keywords TEXT,body_json TEXT NOT NULL,status TEXT NOT NULL,featured BOOLEAN,
                  access_model TEXT,agent_price INT);
                CREATE TABLE sources(
                  id BIGSERIAL PRIMARY KEY,article_id BIGINT REFERENCES articles(id),
                  publisher TEXT NOT NULL,title TEXT NOT NULL,url TEXT NOT NULL,
                  published_at TEXT,source_type TEXT);
                INSERT INTO categories(slug) VALUES('agent'),('cloud');
            """)
            ensure_publication_protection(conn)
            ensure_article_redirects(conn)

    @classmethod
    def tearDownClass(cls):
        with psycopg.connect(cls.url, autocommit=True) as conn:
            conn.execute(f'DROP SCHEMA "{cls.schema}" CASCADE')

    @classmethod
    def connect(cls):
        return psycopg.connect(cls.url, options=f"-c search_path={cls.schema}", row_factory=dict_row)

    def setUp(self):
        with self.connect() as conn:
            conn.execute("TRUNCATE article_redirects,sources,articles RESTART IDENTITY")
        self.store = PublicationStore(self.sql, self.transaction)

    @contextmanager
    def transaction(self):
        with self.connect() as conn:
            yield conn

    def sql(self, statement, parameters=None, *, transaction_id=None):
        statement = re.sub(r":([a-z_][a-z_0-9]*)", r"%(\1)s", statement)
        if transaction_id is not None:
            cursor = transaction_id.execute(statement, parameters or {})
            return cursor.fetchall() if cursor.description else []
        with self.connect() as conn:
            cursor = conn.execute(statement, parameters or {})
            return cursor.fetchall() if cursor.description else []

    def values(self, slug, category=1):
        return {"category_id": category, "slug": slug, "title": slug, "dek": "导语",
                "summary": "摘要", "author": "编辑部", "author_role": "研究",
                "read_minutes": 8, "updated_at": "2026-09-09T00:00:00Z",
                "published_at": "2020-01-01", "hero_style": "network", "authority_score": 94,
                "citation_count": 5, "keywords": "[]",
                "body_json": json.dumps(output()["sections"], ensure_ascii=False)}

    def draft(self, slug="candidate", category=1):
        return self.store.save_draft(self.values(slug, category), evidence())

    def publish_existing(self, slug="existing", category=2):
        article = self.draft(slug, category)
        self.sql("UPDATE articles SET status='published' WHERE id=:id", {"id": article["id"]})
        return article

    def run_gate(self, article, compare=distinct, store=None):
        return review_and_publish(store or self.store, article["id"], article["contentHash"],
                                  {"ready": True}, compare, enabled=True, prepare_language=lambda candidate: {"approved": True})

    def test_redirected_legacy_article_is_never_republished_or_overwritten(self):
        target = self.publish_existing()
        legacy = self.draft("legacy")
        self.sql("""INSERT INTO article_redirects VALUES
            ('legacy',:source,:target,'hash','hash','{}','2026-09-14')""",
            {"source": legacy["id"], "target": target["id"]})
        compare = Mock(side_effect=AssertionError("redirect must stop before model review"))
        self.assertEqual(self.run_gate(legacy, compare)["reason"], "legacy_url_redirected")
        candidate = {"output_article_id": legacy["id"], "updated_at": self.store.article(legacy["id"])["updated_at"]}
        new = self.store.save_draft(self.values("fresh"), evidence(), update_candidate=candidate)
        self.assertNotEqual(new["id"], legacy["id"])
        self.assertEqual(self.store.article(legacy["id"])["slug"], "legacy")
        with self.assertRaises(ValueError):
            self.store.save_draft(self.values("fresh-again"), evidence(),
                                  update_candidate=candidate, require_existing=True)

    def test_independent_article_publishes_after_cross_category_full_text_check(self):
        existing = self.publish_existing()
        candidate = self.draft()
        compare = Mock(side_effect=distinct)
        result = self.run_gate(candidate, compare)
        self.assertTrue(result["published"])
        self.assertEqual(result["comparedArticles"], 1)
        self.assertEqual(compare.call_args.args[1]["id"], existing["id"])
        self.assertEqual(compare.call_args.args[1]["category_slug"], "cloud")
        self.assertIn("body_json", compare.call_args.args[1])
        self.assertEqual(self.store.article(candidate["id"])["status"], "published")

    def test_covered_article_never_publishes_even_if_model_prefers_its_new_background(self):
        self.publish_existing()
        candidate = self.draft()
        def duplicate(a, b):
            return {"relation": "duplicate", "confidence": .98, "materialDifferences": [],
                    "incrementalDetails": ["新增MCP背景"], "preferredArticleId": a["id"], "reason": "核心相同"}
        result = self.run_gate(candidate, duplicate)
        self.assertEqual(result["reason"], "duplicate_content")
        self.assertEqual(self.store.article(candidate["id"])["status"], "review")

    def test_uncertain_malformed_and_failed_comparisons_stay_pending(self):
        self.publish_existing()
        candidate = self.draft()
        for comparison in [lambda *_: {"invalid": True},
                           lambda *_: {**distinct(), "confidence": .7},
                           Mock(side_effect=TimeoutError("model timeout"))]:
            self.assertFalse(self.run_gate(candidate, comparison)["published"])
        self.assertEqual(self.store.article(candidate["id"])["status"], "review")

    def test_all_published_articles_are_included_without_category_or_age_cutoff(self):
        for i in range(17):
            self.publish_existing(f"existing-{i}", category=1 if i % 2 else 2)
        candidate = self.draft()
        result = self.run_gate(candidate)
        self.assertTrue(result["published"])
        self.assertEqual(result["comparedArticles"], 17)

    def test_concurrent_source_edit_invalidates_review(self):
        existing = self.publish_existing()
        candidate = self.draft()
        def compare(*args):
            self.sql("UPDATE sources SET title='changed' WHERE article_id=:id", {"id": existing["id"]})
            return distinct()
        self.assertEqual(self.run_gate(candidate, compare)["reason"], "published_catalog_changed")
        self.assertEqual(self.store.article(candidate["id"])["status"], "review")

    def test_candidate_change_invalidates_review(self):
        self.publish_existing()
        candidate = self.draft()
        def compare(*args):
            self.sql("UPDATE articles SET body_json='[]' WHERE id=:id", {"id": candidate["id"]})
            return distinct()
        self.assertEqual(self.run_gate(candidate, compare)["reason"], "candidate_changed")

    def test_simultaneous_publications_cannot_both_commit_against_an_old_catalog(self):
        candidates = [self.draft("first"), self.draft("second")]
        barrier = Barrier(2)
        outer = self
        class ConcurrentStore(PublicationStore):
            def commit_publication(self, candidate, public):
                barrier.wait(timeout=10)
                return super().commit_publication(candidate, public)
        store = ConcurrentStore(outer.sql, outer.transaction)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda item: self.run_gate(item, store=store), candidates))
        self.assertEqual(sum(r["published"] for r in results), 1)
        self.assertEqual(sum(r["reason"] == "published_catalog_changed" for r in results), 1)

    def test_source_similarity_cannot_overwrite_published_article(self):
        existing = self.publish_existing()
        original = self.store.article(existing["id"])
        saved = self.store.save_draft(
            self.values("new-version"), evidence(),
            {"output_article_id": existing["id"], "updated_at": original["updated_at"]},
        )
        self.assertNotEqual(existing["id"], saved["id"])
        self.assertEqual(content_identity(self.store.article(existing["id"])), content_identity(original))
        self.assertEqual(self.store.article(existing["id"])["status"], "published")

    def test_failed_source_write_rolls_back_article_and_source_changes(self):
        article = self.draft()
        original = self.store.article(article["id"])
        broken = evidence()
        broken[-1]["title"] = None
        with self.assertRaises(psycopg.errors.NotNullViolation):
            self.store.save_draft(
                self.values("changed"), broken,
                {"output_article_id": article["id"], "updated_at": original["updated_at"]},
            )
        self.assertEqual(content_identity(self.store.article(article["id"])), content_identity(original))

    def test_reverification_cannot_overwrite_a_concurrently_published_draft(self):
        article = self.draft()
        original = self.store.article(article["id"])
        self.sql("UPDATE articles SET status='published' WHERE id=:id", {"id": article["id"]})
        with self.assertRaises(ValueError):
            self.store.save_draft(
                self.values("changed"), evidence(),
                {"output_article_id": article["id"], "updated_at": original["updated_at"]},
                require_existing=True,
            )
        self.assertEqual(content_identity(self.store.article(article["id"])), content_identity(original))


if __name__ == "__main__":
    unittest.main()
