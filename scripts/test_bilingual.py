#!/usr/bin/env python3
"""Translation integrity and database publication barriers."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import unittest
import uuid
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.bilingual import create_english, ensure_schema, save_english, source_document, source_hash, validate_shape
from backend import i18n
from aws_runtime.publication import review_and_publish, content_identity


def article():
    return {"id": 1, "slug": "stable-url", "title": "异步执行", "dek": "有证据的异步执行说明", "summary": "支持与限制",
            "body_json": json.dumps([{"type": "analysis", "heading": "证据", "paragraphs": ["功能上线 [S1]"],
                                      "code": "print('原样保留')"}], ensure_ascii=False),
            "keywords": '["异步"]', "sources": [{"title": "原始发布", "url": "https://example.com"}],
            "category_id": 1, "status": "review", "updated_at": "2026-09-25"}


def edition(a):
    content = source_document(a)
    content.update(title="Asynchronous execution", dek="Evidence for asynchronous execution", summary="Support and limitations", keywords=["asynchronous"])
    content["sections"][0].update(heading="Evidence", paragraphs=["The feature launched [S1]"])
    content["sourceTitles"] = ["Original release"]
    return {"sourceHash": source_hash(a), "content": content,
            "review": {"approved": True, "complete": True, "faithful": True, "score": 98, "issues": []},
            "reviewedAt": "2026-09-25T12:00:00Z"}


class TranslationTests(unittest.TestCase):
    def tearDown(self):
        i18n.LANGUAGE.set("zh")
        i18n.CACHE.set(None)

    def test_omitted_paragraph_changed_citation_and_changed_code_are_rejected(self):
        a = article()
        for change in (lambda d: d["sections"][0]["paragraphs"].clear(),
                       lambda d: d["sections"][0].update(paragraphs=["Changed [S2]"]),
                       lambda d: d["sections"][0].update(code="print('translated')")):
            translated = edition(a)["content"]
            change(translated)
            with self.assertRaises(ValueError):
                validate_shape(source_document(a), translated)

    def test_independent_review_can_block_structurally_complete_translation(self):
        a = article()
        model = Mock(side_effect=[edition(a)["content"], {"approved": False, "complete": True,
                      "faithful": False, "score": 89, "issues": ["Meaning changed"]}])
        with self.assertRaisesRegex(ValueError, "English review failed"):
            create_english(a, call=model)
        self.assertEqual(model.call_count, 2)

    def test_default_english_explicit_chinese_and_code_preservation(self):
        self.assertEqual(i18n.begin_request("/article/a", {}), "/article/a")
        self.assertEqual(i18n.language(), "en")
        self.assertEqual(i18n.payload({"code": "print('专业内容库')"})["code"], "print('专业内容库')")
        self.assertEqual(i18n.begin_request("/zh/article/a", {}), "/article/a")
        self.assertEqual(i18n.path_for("/article/a"), "/zh/article/a")
        i18n.begin_request("/api/v1/articles", {"lang": ["zh"]})
        self.assertEqual(i18n.language(), "zh")

    def test_unpublished_text_and_original_evidence_are_not_fragment_translated(self):
        i18n.begin_request("/api/admin/articles", {})
        i18n.CACHE.set({"headers": {}, "slugs": {}})
        draft = {"id": 1, "slug": "draft", "title": "研究标题尚未发布", "summary": "证据需要重新审核"}
        self.assertEqual(i18n.payload(draft), draft)
        research = {"output_article_id": 1, "verification": {"notes": "研究证据保持原文"},
                    "analysisProcess": [{"step": "分析原始证据"}]}
        self.assertEqual(i18n.payload(research), research)

    def test_scheduled_publication_fails_closed_when_translation_is_unavailable(self):
        a = article()
        store = Mock()
        store.article.return_value = a
        store.published.return_value = []
        for callback in (None, Mock(side_effect=ValueError("review failed"))):
            result = review_and_publish(store, 1, content_identity(a), {"ready": True}, Mock(),
                                        enabled=True, prepare_language=callback)
            self.assertFalse(result["published"])
            self.assertTrue(result["reason"].startswith("bilingual_review_"))
        store.commit_publication.assert_not_called()


@unittest.skipUnless(os.environ.get("PUBLICATION_TEST_DATABASE_URL"), "local PostgreSQL URL not set")
class DatabaseBilingualTests(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg.rows import dict_row
        self.conn = psycopg.connect(os.environ["PUBLICATION_TEST_DATABASE_URL"], row_factory=dict_row, autocommit=True)
        self.schema = "bilingual_test_" + uuid.uuid4().hex
        self.conn.execute(f'CREATE SCHEMA "{self.schema}"')
        self.conn.execute(f'SET search_path TO "{self.schema}"')
        self.conn.execute("CREATE TABLE articles(id BIGINT PRIMARY KEY,title TEXT,dek TEXT,summary TEXT,body_json TEXT,keywords TEXT,status TEXT)")
        ensure_schema(self.conn)
        self.a = article()
        self.conn.execute("INSERT INTO articles VALUES(%s,%s,%s,%s,%s,%s,'review')", tuple(self.a[k] for k in ("id", "title", "dek", "summary", "body_json", "keywords")))
        self.conn.execute("UPDATE bilingual_policy SET required=TRUE")

    def tearDown(self):
        self.conn.execute(f'DROP SCHEMA "{self.schema}" CASCADE')
        self.conn.close()

    def test_unicode_revision_hash_matches_sql_and_stale_or_missing_edition_blocks(self):
        from psycopg.errors import RaiseException
        digest = self.conn.execute("SELECT md5(concat_ws(chr(31),title,dek,summary,body_json,keywords)) hash FROM articles").fetchone()["hash"]
        self.assertEqual(digest, source_hash(self.a))
        with self.assertRaises(RaiseException):
            self.conn.execute("UPDATE articles SET status='published'")
        save_english(self.conn, self.a, edition(self.a))
        self.conn.execute("UPDATE articles SET title='正文已更新'")
        with self.assertRaises(RaiseException):
            self.conn.execute("UPDATE articles SET status='published'")
        self.conn.execute("UPDATE articles SET title=%s", (self.a["title"],))
        self.conn.execute("UPDATE articles SET status='published'")
        with self.assertRaises(RaiseException):
            self.conn.execute("UPDATE articles SET body_json='[]'")
        self.assertEqual(self.conn.execute("SELECT status FROM articles").fetchone()["status"], "published")

    def test_unapproved_review_cannot_be_used_to_publish(self):
        from psycopg.errors import RaiseException
        e = edition(self.a)
        save_english(self.conn, self.a, e)
        self.conn.execute("UPDATE article_translations SET review_json=%s", (json.dumps({**e["review"], "faithful": False}),))
        with self.assertRaises(RaiseException):
            self.conn.execute("UPDATE articles SET status='published'")


if __name__ == "__main__":
    unittest.main()
