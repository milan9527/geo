#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from unittest.mock import Mock, patch
from pathlib import Path

os.environ.setdefault("GEO_PUBLIC_DISTRIBUTION_ID", "TESTDISTRIBUTION")
sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1] / "aws_indexing_notifier"),
)

with patch("boto3.client", return_value=Mock()):
    import lambda_function


def main() -> None:
    slugs = lambda_function.clean_values(
        ["valid-article", "../invalid", "valid-article"],
        max_length=240,
    )
    categories = lambda_function.clean_values(
        ["ai", "agent", "AI"],
        max_length=100,
    )
    assert slugs == ["valid-article"]
    assert categories == ["agent", "ai"]
    assert lambda_function.build_urls(slugs, categories) == [
        "https://aperture.zhangwangshu.com/article/valid-article",
        "https://aperture.zhangwangshu.com/category/agent",
        "https://aperture.zhangwangshu.com/category/ai",
    ]
    lambda_function.cloudfront.create_invalidation.return_value = {"Invalidation": {"Id": "TEST"}}
    lambda_function.invalidate(slugs, categories, "test")
    paths = lambda_function.cloudfront.create_invalidation.call_args.kwargs["InvalidationBatch"]["Paths"]
    assert "/" in paths["Items"]
    assert "/sitemap.xml" in paths["Items"]
    assert paths["Quantity"] == len(set(paths["Items"]))
    print("Indexing notifier unit checks passed")


if __name__ == "__main__":
    main()
