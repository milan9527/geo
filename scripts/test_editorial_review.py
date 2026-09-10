#!/usr/bin/env python3
"""Checks for conservative duplicate decisions and publication eligibility."""

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.review_articles import (
    DEDUPE_POLICY_VERSION, POLICY_VERSION, build_plan, canonical_url, content_hash,
    pair_results, quality_gates, require_pair_coverage, snapshot_signature, validate_pair, write_json,
    publication_target,
)
import hashlib


class EditorialReviewTests(unittest.TestCase):
    def setUp(self):
        self.checks = {"sourceCount": 5, "publisherCount": 3, "sectionJsonCharacters": 4000,
                       "invalidCitationIds": [], "archivedSourceCount": 5}
        self.review = {"decision": "approve", "score": 94, "evidenceSupported": True,
                       "completeArticle": True, "issues": []}

    def test_optional_style_suggestion_is_not_a_factual_rejection(self):
        self.review["issues"] = [{"kind": "quality", "detail": "可精简一处重复说明"}]
        self.assertTrue(all(quality_gates(self.review, self.checks).values()))

    def test_factual_issue_blocks_even_if_model_says_approve(self):
        self.review["issues"] = [{"kind": "citation", "detail": "数字与来源不符"}]
        self.assertFalse(all(quality_gates(self.review, self.checks).values()))

    def test_unknown_evidence_or_bad_citation_blocks_publication(self):
        self.checks["archivedSourceCount"] = 0
        self.assertFalse(all(quality_gates(self.review, self.checks).values()))
        self.checks["archivedSourceCount"] = 5
        self.checks["invalidCitationIds"] = [9]
        self.assertFalse(all(quality_gates(self.review, self.checks).values()))

    def test_source_tracking_removed_but_document_identity_preserved(self):
        self.assertEqual(
            canonical_url("https://EXAMPLE.com/research/?id=5&utm_source=x#note"),
            "https://example.com/research?id=5",
        )
        self.assertNotEqual(canonical_url("https://example.com/?id=5"),
                            canonical_url("https://example.com/?id=6"))

    def test_duplicate_edges_are_not_merged_transitively(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            articles = [{
                "id": i, "slug": f"article-{i}", "title": f"研究{i}", "category_slug": "ai",
                "status": "published" if i == 1 else "review", "updated_at": "2026-09-09",
                "content_hash": str(i),
            } for i in [1, 2, 3]]
            reviews = {i: {"qualityApproved": True, "score": 100 - i,
                           "checks": {"archivedSourceCount": 5},
                           "gates": {"verified": True}, "issues": []} for i in [1, 2, 3]}
            write_json(directory / "snapshot.json", {"articles": articles})
            for a, b in [(1, 2), (2, 3)]:
                write_json(directory / "pairs" / f"{a}-{b}.json", {
                    "a": a, "b": b, "confirmedDuplicate": True, "relation": "duplicate",
                    "reason": "直接比对重复",
                    "signature": hashlib.sha256((DEDUPE_POLICY_VERSION + str(a) + str(b)).encode()).hexdigest(),
                })
            plan = build_plan(directory, articles, reviews)
            decisions = {a["articleId"]: a for a in plan["articles"]}
            self.assertEqual(decisions[2]["duplicateOf"], 1)
            self.assertEqual(decisions[3]["decision"], "approved")
            # An article edit invalidates its earlier pair decisions.
            articles[1]["content_hash"] = "changed"
            write_json(directory / "snapshot.json", {"articles": articles})
            self.assertEqual(pair_results(directory), [])

    def test_background_addition_does_not_rescue_covered_article(self):
        result = {"relation": "duplicate", "confidence": .98,
                  "incrementalDetails": ["162比170多了一段MCP版本背景"],
                  "materialDifferences": [], "preferredArticleId": 162}
        validate_pair(result, 162, 170)
        self.assertTrue(result["confirmedDuplicate"])
        result["preferredArticleId"] = 999
        with self.assertRaises(ValueError):
            validate_pair(result, 162, 170)

    def test_publication_requires_all_pairs_not_only_a_catalog_group(self):
        comparisons = [{"a": 127, "b": 162}, {"a": 162, "b": 170}]
        with self.assertRaises(ValueError):
            require_pair_coverage({127, 162, 170}, comparisons)
        comparisons.append({"a": 170, "b": 127})
        require_pair_coverage({127, 162, 170}, comparisons)

    def test_core_coverage_preferred_over_small_score_difference(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            articles = [{
                "id": i, "slug": f"article-{i}", "title": f"研究{i}", "category_slug": "agent",
                "status": "published", "updated_at": "2026-09-09", "content_hash": str(i),
            } for i in [162, 170]]
            reviews = {i: {"qualityApproved": True, "score": 91 if i == 162 else 95,
                           "checks": {"archivedSourceCount": 5},
                           "gates": {"verified": True}, "issues": []} for i in [162, 170]}
            write_json(directory / "snapshot.json", {"articles": articles})
            pair = {"a": 162, "b": 170, "confirmedDuplicate": True, "relation": "duplicate",
                    "preferredArticleId": 162, "reason": "核心相同，162覆盖更完整",
                    "signature": hashlib.sha256((DEDUPE_POLICY_VERSION + "162170").encode()).hexdigest()}
            write_json(directory / "pairs/162-170.json", pair)
            decisions = {a["articleId"]: a for a in build_plan(directory, articles, reviews)["articles"]}
            self.assertEqual(decisions[162]["decision"], "approved")
            self.assertEqual(decisions[170]["duplicateOf"], 162)
            # An earlier permissive policy must not be silently reused.
            pair["signature"] = hashlib.sha256((POLICY_VERSION + "162170").encode()).hexdigest()
            write_json(directory / "pairs/162-170.json", pair)
            self.assertEqual(pair_results(directory), [])

    def test_body_and_sources_are_part_of_snapshot_identity(self):
        article = {"title": "同一标题", "body_json": "[]", "sources": [{"url": "https://a.com"}]}
        original = content_hash(article)
        article["sources"] = [{"url": "https://b.com"}]
        self.assertNotEqual(content_hash(article), original)

    def test_similarity_cache_invalidated_by_new_or_changed_articles(self):
        first = [{"id": 1, "content_hash": "a"}, {"id": 2, "content_hash": "b"}]
        self.assertEqual(snapshot_signature(first), snapshot_signature(list(reversed(first))))
        self.assertNotEqual(snapshot_signature(first), snapshot_signature(first + [{"id": 3, "content_hash": "c"}]))
        self.assertNotEqual(snapshot_signature(first), snapshot_signature([first[0], {"id": 2, "content_hash": "changed"}]))

    def test_later_review_never_unpublishes_existing_page(self):
        for decision in ["approved", "duplicate", "needs_revision"]:
            self.assertEqual(publication_target({"status": "published"}, {"decision": decision}, True), "published")
        self.assertEqual(publication_target({"status": "review"}, {"decision": "duplicate"}, True), "review")
        self.assertEqual(publication_target({"status": "review"}, {"decision": "approved"}, True), "published")

    def test_new_preferred_duplicate_cannot_replace_existing_public_url(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            articles = [
                {"id": i, "slug": f"article-{i}", "title": f"研究{i}", "category_slug": "agent",
                 "status": "published" if i == 1 else "review", "updated_at": "2026-09-10", "content_hash": str(i)}
                for i in [1, 2]
            ]
            # Even a newly identified issue on the old page is no permission to
            # remove its URL or publish a duplicate replacement at another URL.
            reviews = {
                i: {"qualityApproved": i == 2, "score": 70 if i == 1 else 99,
                    "checks": {"archivedSourceCount": 5}, "gates": {"verified": i == 2}, "issues": []}
                for i in [1, 2]
            }
            write_json(directory / "snapshot.json", {"articles": articles})
            write_json(directory / "pairs/1-2.json", {
                "a": 1, "b": 2, "confirmedDuplicate": True, "relation": "duplicate",
                "preferredArticleId": 2, "reason": "新稿更完整，但核心重复",
                "signature": hashlib.sha256((DEDUPE_POLICY_VERSION + "12").encode()).hexdigest(),
            })
            decisions = {a["articleId"]: a for a in build_plan(directory, articles, reviews)["articles"]}
            self.assertEqual(decisions[1]["decision"], "needs_revision")
            self.assertEqual(decisions[2]["duplicateOf"], 1)
            self.assertEqual(publication_target(articles[0], decisions[1], True), "published")
            self.assertEqual(publication_target(articles[1], decisions[2], True), "review")


if __name__ == "__main__":
    unittest.main()
