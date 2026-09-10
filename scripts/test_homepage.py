#!/usr/bin/env python3
"""Check crawlable home content, empty collections, and untrusted metadata."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.homepage import render_home
from scripts.check_search_indexing import Page


class HomepageTests(unittest.TestCase):
    def article(self, index):
        return {
            "slug": f"research-{index}", "title": f"研究标题 {index}", "dek": f"独立摘要 {index}",
            "summary": "研究导语", "hero_style": "visual-agent", "author": "研究编辑部",
            "category_name": "Agent 技术", "category_eyebrow": "AGENT",
            "read_minutes": 12, "source_count": 5,
            "published_at": "2026-09-09T00:00:00+00:00", "updated_at": "2026-09-09",
        }

    def test_all_visible_articles_have_links_and_matching_structured_data(self):
        for count in (0, 1, 2, 3, 15, 30):
            with self.subTest(count=count):
                articles = [self.article(i) for i in range(count)]
                markup, schemas = render_home(articles, [], "https://example.com")
                page = Page()
                page.feed(markup)
                self.assertEqual(len(page.h1_texts), 1)
                self.assertEqual(page.article_paths, {f"/article/research-{i}" for i in range(count)})
                for item in articles:
                    self.assertIn(item["title"], markup)
                    self.assertIn(item["dek"], markup)
                entries = schemas[-1]["mainEntity"]["itemListElement"]
                self.assertEqual({entry["url"] for entry in entries},
                                 {"https://example.com" + path for path in page.article_paths})
                self.assertEqual([e["position"] for e in entries], list(range(1, count + 1)))

    def test_article_and_category_metadata_cannot_inject_html(self):
        article = self.article(1)
        article.update(title='<img src=x onerror="alert(1)">', hero_style='" onmouseover="bad',
                       slug='bad" slug', summary='<script>bad()</script>')
        category = {"slug": 'bad" category', "name": "<script>bad()</script>",
                    "description": "<img src=x>", "article_count": 1}
        markup, _ = render_home([article], [category], "https://example.com")
        self.assertNotIn("<script>", markup)
        self.assertNotIn("<img", markup)
        self.assertNotIn('class="story-visual " onmouseover=', markup)
        self.assertIn("/article/bad%22%20slug", markup)
        self.assertIn("/category/bad%22%20category", markup)


if __name__ == "__main__":
    unittest.main()
