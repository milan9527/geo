#!/usr/bin/env python3
"""Exercise scheduled persistence with cloud services mocked, using the real gates."""

import importlib
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "aws_runtime"), str(ROOT)]
for name in ("BEDROCK_MODEL_ID", "AURORA_RESOURCE_ARN", "AURORA_SECRET_ARN"):
    os.environ.setdefault(name, "test")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
with patch("boto3.client", return_value=Mock()):
    runtime = importlib.import_module("app")
from publication import content_identity


def response(**values):
    return {"columnMetadata": [{"name": key} for key in values],
            "records": [[{"longValue": value} if isinstance(value, int) else {"stringValue": value}
                         for value in values.values()]]}


class RuntimePublicationTests(unittest.TestCase):
    def setUp(self):
        self.evidence = [{"publisher": f"org-{i%3}", "title": f"source-{i}",
                          "url": f"https://source-{i}.example/release", "publishedAt": "2026-09-09",
                          "retrievedAt": "2026-09-09", "sourceType": "primary",
                          "excerpt": "可核实证据", "data": {}} for i in range(5)]
        self.output = {"title": "框架增加工具执行支持", "dek": "具体版本变化", "summary": "有证据的研究",
                       "keywords": [], "authorityScore": 94, "analysisProcess": [],
                       "sections": [{"type": "analysis", "heading": "功能变化",
                                     "paragraphs": ["执行变更及其证据[S1][S2][S3][S4][S5]" * 150]}]}
        self.audit = {"status": "verified", "score": 94, "completeArticle": True,
                      "unsupportedClaims": [], "citationIssues": [], "causalityRisks": []}
        self.article = {"id": 9, "category_id": 1, "category_slug": "agent", "slug": "new-article",
                        "title": self.output["title"], "dek": self.output["dek"],
                        "summary": self.output["summary"], "keywords": "[]",
                        "body_json": json.dumps(self.output["sections"]), "status": "review",
                        "updated_at": "2026-09-09", "sources": [
                            {**e, "published_at": e["publishedAt"], "source_type": e["sourceType"]}
                            for e in self.evidence]}
        self.store = Mock()
        self.store.article.return_value = self.article
        self.store.published.return_value = []
        self.store.commit_publication.return_value = (True, "published")
        self.store.save_draft.return_value = {
            "id": 9, "slug": "new-article", "categorySlug": "agent",
            "contentHash": content_identity(self.article), "action": "created_review_draft"}

    def sql(self, statement, parameters=None, **kwargs):
        if "INSERT INTO research_runs" in statement:
            return response(id=7)
        if "SELECT id FROM categories" in statement:
            return response(id=1)
        return {"columnMetadata": [], "records": []}

    def persist(self, compare=None):
        with patch.multiple(runtime, AUTO_PUBLISH_RESEARCH=True, publication_store=self.store), \
             patch.object(runtime, "execute_sql", side_effect=self.sql), \
             patch.object(runtime, "article_update_candidate", return_value=None), \
             patch.object(runtime, "article_title_candidate", return_value=None), \
             patch.object(runtime, "generate_deep_research", return_value=(self.output, {})), \
             patch.object(runtime, "verify_research_output", return_value=self.audit), \
             patch.object(runtime, "compare_publication_pair", new=compare or Mock()), \
             patch.object(runtime, "prepare_english_publication", return_value={"approved": True}), \
             patch.object(runtime, "submit_indexing", return_value=True) as notify:
            result = runtime.persist_research_output(
                crawler={"id": 1, "name": "定时采集"}, job_id=1,
                profile={"category": "agent", "topic": "框架", "writingStyles": ["comparative"]},
                evidence=self.evidence, fetch_errors=[], force_analysis=False, tool_trace={})
            return result, notify

    def test_verified_independent_article_publishes_and_notifies_after_commit(self):
        result, notify = self.persist()
        self.assertEqual(result["articleStatus"], "published")
        self.assertTrue(result["indexingSubmitted"])
        self.store.commit_publication.assert_called_once()
        self.assertEqual(self.store.save_draft.call_args.args[0]["status"], "review")
        notify.assert_called_once_with("new-article", "agent", reason="scheduled_verified_unique")

    def test_verified_label_alone_does_not_bypass_quality_gate(self):
        self.audit["score"] = 89
        result, notify = self.persist()
        self.assertEqual(result["articleStatus"], "review")
        self.assertEqual(result["verification"]["semanticDeduplication"]["reason"], "quality_gate_failed")
        self.store.commit_publication.assert_not_called()
        notify.assert_not_called()

    def test_duplicate_stays_review_and_never_notifies_search_engines(self):
        existing = {**self.article, "id": 10, "slug": "existing", "status": "published"}
        self.store.published.return_value = [existing]
        compare = Mock(return_value={"relation": "duplicate", "confidence": .99,
                                     "materialDifferences": [], "preferredArticleId": 10,
                                     "reason": "主要内容已被现有稿覆盖"})
        result, notify = self.persist(compare)
        self.assertEqual(result["articleStatus"], "review")
        self.assertEqual(result["verification"]["semanticDeduplication"]["duplicateOf"], [10])
        self.store.commit_publication.assert_not_called()
        notify.assert_not_called()

    def test_incomplete_or_untrusted_audit_never_becomes_verified(self):
        payload = {**self.audit, "status": "needs_review", "score": 99}
        with patch.object(runtime.bedrock, "converse", return_value={
            "output": {"message": {"content": [{"text": json.dumps(payload)}]}},
            "usage": {},
        }):
            self.assertEqual(runtime.verify_research_output(self.output, self.evidence)["status"], "needs_review")
        payload = {**self.audit}
        del payload["completeArticle"]
        with patch.object(runtime.bedrock, "converse", return_value={
            "output": {"message": {"content": [{"text": json.dumps(payload)}]}}, "usage": {},
        }):
            self.assertEqual(runtime.verify_research_output(self.output, self.evidence)["status"], "needs_review")

    def test_unchanged_evidence_rechecks_pending_article_without_generating_another(self):
        def previous_sql(statement, parameters=None, **kwargs):
            if "WHERE agent_id" in statement or "WHERE id=:run_id" in statement:
                return response(id=5, output_article_id=9, summary="旧稿",
                                verification_status="verified", verification_json=json.dumps(self.audit))
            return self.sql(statement, parameters, **kwargs)
        self.store.article.side_effect = [
            self.article, {**self.article, "status": "published"},
        ]
        with patch.multiple(runtime, AUTO_PUBLISH_RESEARCH=True, publication_store=self.store), \
             patch.object(runtime, "execute_sql", side_effect=previous_sql), \
             patch.object(runtime, "article_update_candidate", return_value=None), \
             patch.object(runtime, "generate_deep_research") as generate, \
             patch.object(runtime, "reverify_recent_research", return_value=[{"articleId": 9}]) as recheck:
            result = runtime.persist_research_output(
                crawler={"id": 1, "name": "定时采集"}, job_id=1,
                profile={"category": "agent", "topic": "框架"},
                evidence=self.evidence, fetch_errors=[], force_analysis=False, tool_trace={})
        generate.assert_not_called()
        recheck.assert_called_once_with(7, article_id=9)
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["articleStatus"], "published")


if __name__ == "__main__":
    unittest.main()
