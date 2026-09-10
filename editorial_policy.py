"""Shared full-text duplicate policy for batch review and scheduled publication."""

import json

DEDUPE_POLICY_VERSION = "2026-09-09-v2-core-coverage"
PUBLICATION_POLICY_VERSION = "2026-09-10-preserve-published"


def validate_pair(result: dict, first_id: int, second_id: int) -> None:
    if result.get("relation") not in {"duplicate", "overlap_distinct", "distinct", "uncertain"}:
        raise ValueError("Invalid duplicate decision")
    if not isinstance(result.get("materialDifferences"), list):
        raise ValueError("Missing material differences")
    result["confidence"] = float(result.get("confidence", 0))
    if not 0 <= result["confidence"] <= 1:
        raise ValueError("Duplicate confidence must be between zero and one")
    preferred = result.get("preferredArticleId")
    if preferred is not None and preferred not in {first_id, second_id}:
        raise ValueError("Preferred article is not part of the comparison")
    if result["relation"] == "duplicate" and preferred is None:
        raise ValueError("Duplicate decision needs a retained representative")
    # Minor additions may be absorbed into one retained article. Only differences
    # that justify publishing BOTH articles are material differences here.
    result["confirmedDuplicate"] = (
        result["relation"] == "duplicate" and result["confidence"] >= .9
        and not result["materialDifferences"]
    )



def build_pair_prompt(first: dict, second: dict) -> str:
    return """
对两篇研究稿做全文去重裁决。判断是否值得作为两篇独立文章公开。
duplicate：主要事实、版本/事件、研究问题和结论重复；或一篇覆盖了另一篇的主要价值，
即使覆盖更完整的一篇增加了有价值的段落，也只需要保留一篇。
overlap_distinct：有重合，但两篇分别具有足以独立发表的核心问题与证据支撑的独立结论。
distinct：核心事件或研究问题不同。uncertain：证据不足，不能可靠裁决。
同一来源、公司、领域或写作模板不能单独证明重复；新标题、日期和措辞也不能单独证明独立。
先比较标题、导语和主要篇幅对应的核心事实，再评估差异能否支撑两篇独立文章。
增加MCP背景、旁支新闻、个别版本条目、风险提示、测试建议或抓取时间，
若未形成独立核心结论，均属可合并补充，放入incrementalDetails，不能当作独立发表理由。
尤其注意包含关系：A的核心价值已被B覆盖时，应判duplicate并优先保留B，
不能因为B多了一段真实信息，就把A和B都判为独立。
真实的新版本进展、不同实验结果或有证据支持的独立问题可以分别发表；
但相同版本的重复汇总以及对未来测试的不同建议不算新研究。
duplicate时推荐覆盖核心证据更完整、正文更聚焦的一篇；不能只按字数或日期推荐。
只输出 JSON：
{"relation":"duplicate或overlap_distinct或distinct或uncertain","confidence":0到1,
 "sharedClaims":["最多4条具体重合事实"],
 "incrementalDetails":["可以并入保留稿、但不支持两篇分别发表的差异，最多3条"],
 "materialDifferences":["足以支持两篇分别发表的核心差异，最多3条"],
 "preferredArticleId":重复时推荐保留的文章ID，否则null,
 "reason":"解释双方是否各有独立价值，以及为何保留该版本，最多260字"}
duplicate的materialDifferences必须为空，但incrementalDetails可以非空。
overlap_distinct必须说明两篇各自的独立核心问题和结论，不能仅指出一篇包含更多内容。
""" + "\n\n".join(json.dumps({
        "id": article["id"], "category": article["category_slug"],
        "title": article["title"], "dek": article["dek"], "summary": article["summary"],
        "sections": json.loads(article["body_json"]),
        "sources": [{"publisher": s["publisher"], "title": s["title"], "url": s["url"],
                     "publishedAt": s["published_at"]} for s in article["sources"]],
    }, ensure_ascii=False) for article in [first, second])
