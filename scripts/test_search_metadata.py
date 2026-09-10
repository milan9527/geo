#!/usr/bin/env python3
"""Regression checks for multilingual search excerpts, without database access."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.seo import search_description, search_title


class SearchMetadataTests(unittest.TestCase):
    def test_short_chinese_description_stays_intact(self):
        text = "追踪 AI 大模型、推理系统与算力产业动态，结合一手资料分析技术进展、商业影响和关键风险。"
        self.assertEqual(search_description(text), text)

    def test_citations_removed_without_dropping_dates_or_other_brackets(self):
        text = "2026年9月8日发布接口说明。[S1][S10] 支持 [beta] 功能。"
        self.assertEqual(search_description(text), "2026年9月8日发布接口说明。 支持 [beta] 功能。")

    def test_long_description_ends_on_complete_sentence(self):
        sentence = "研究覆盖模型部署、数据权限与更新控制。" * 5
        text = sentence + "这是仍需进一步验证的另一个研究方向" * 20
        self.assertEqual(search_description(text), sentence)

    def test_long_english_description_preserves_word_boundary(self):
        text = "Evidence-based research examines infrastructure and deployment choices. " * 6
        result = search_description(text)
        self.assertLessEqual(len(result), 160)
        self.assertTrue(result.endswith("."))
        self.assertTrue(text.startswith(result))

    def test_long_title_drops_brand_before_shortening_headline(self):
        headline = "Moonshot、智谱、Qwen与MiniMax推进训练、Agent、安全和语音能力，四项研究检验执行与迁移"
        self.assertEqual(search_title(headline + " · Aperture Intelligence"), headline)

    def test_brand_is_not_repeated(self):
        self.assertEqual(
            search_title("关于 Aperture Intelligence · Aperture Intelligence"),
            "关于 Aperture Intelligence",
        )

    def test_limits_use_unicode_characters_not_utf8_bytes(self):
        result = search_description("模型研究" * 100)
        self.assertEqual(len(result), 160)
        self.assertTrue(result.endswith("…"))
        self.assertLessEqual(len(search_title("模型研究" * 100)), 65)


if __name__ == "__main__":
    unittest.main()
