from __future__ import annotations

import json

from backend.database import connection, utc_now


def main() -> None:
    structured: list[int] = []
    published: list[int] = []
    with connection() as conn:
        runs = conn.execute(
            """
            SELECT rr.id, rr.category_slug, rr.summary, rr.output_article_id,
                   a.body_json
            FROM research_runs rr
            JOIN articles a ON a.id = rr.output_article_id
            WHERE rr.status = 'completed' AND rr.output_article_id IS NOT NULL
            ORDER BY rr.id
            """
        ).fetchall()
        for run in runs:
            sections = json.loads(run["body_json"] or "[]")
            has_outlook = any(
                section.get("type") == "outlook"
                or "结论" in str(section.get("heading") or "")
                or "未来观察" in str(section.get("heading") or "")
                for section in sections
                if isinstance(section, dict)
            )
            if not has_outlook:
                evidence = conn.execute(
                    """
                    SELECT title, published_at
                    FROM research_evidence
                    WHERE run_id = %s
                    ORDER BY id
                    """,
                    (run["id"],),
                ).fetchall()
                titles = [row["title"] for row in evidence[:4]]
                cutoff = max(
                    (row["published_at"] for row in evidence),
                    default=utc_now()[:10],
                )
                bullets = [
                    f"结论：{run['summary']}",
                    (
                        "未来观察指标：持续跟踪 "
                        + "、".join(titles)
                        + " 的新增数据与官方发布，检验当前观点是否得到进一步支持。"
                    ),
                    (
                        f"风险与边界：本次判断只基于截至 {cutoff} 的 "
                        f"{len(evidence)} 条证据；来源口径、时间窗口或覆盖范围不一致时，"
                        "不能将相关性解释为已经证实的因果关系。"
                    ),
                ]
                if run["category_slug"] == "finance":
                    bullets.append("声明：本文为行业研究与市场信息分析，不构成投资建议。")
                sections.append(
                    {
                        "type": "outlook",
                        "heading": "结论与未来观察",
                        "bullets": bullets,
                    }
                )
                structured.append(run["output_article_id"])
            conn.execute(
                """
                UPDATE articles
                SET body_json = %s, status = 'published', updated_at = %s
                WHERE id = %s
                """,
                (
                    json.dumps(sections, ensure_ascii=False),
                    utc_now(),
                    run["output_article_id"],
                ),
            )
            published.append(run["output_article_id"])

    print(
        "Backfilled research structure for article IDs: "
        + (", ".join(map(str, structured)) if structured else "none")
    )
    print(
        "Published research article IDs: "
        + (", ".join(map(str, published)) if published else "none")
    )


if __name__ == "__main__":
    main()
