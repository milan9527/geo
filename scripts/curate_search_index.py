#!/usr/bin/env python3
"""Reduce the public index to a small, reversible set of differentiated research."""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter

import boto3

from backend.database import connection, utc_now


KEEP_SLUGS = {
    # AI: global model vendors, Chinese model vendors, and platform/API changes.
    "ai-research-20260908-0004-1088",
    "ai-research-20260907-1207-1007",
    "ai-research-20260908-1205-1165",
    # Agent: framework execution, governance/identity, and MCP/tool protocols.
    "agent-research-20260908-1804-1170",
    "agent-research-20260908-0403-1114",
    "agent-research-20260908-0003-1085",
    # Cloud: architecture comparison, Chinese cloud expansion, and data platforms.
    "cloud-research-20260904-1205-526",
    "cloud-research-20260908-0606-1129",
    "cloud-research-20260907-1207-1008",
    # Commerce: delegated buying, seller distribution, and agent payment economics.
    "commerce-research-20260908-0006-1090",
    "commerce-research-20260908-0606-1130",
    "commerce-research-20260909-0004-1178",
    # Finance: current market data, market structure, and systemic AI-capex risk.
    "finance-research-20260909-0014-1179",
    "finance-research-20260904-0905-503",
    "finance-research-20260907-1208-1006",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Move non-selected published articles to review. Default is dry-run.",
    )
    parser.add_argument(
        "--notify",
        action="store_true",
        help="Invalidate changed URLs and notify IndexNow after applying.",
    )
    args = parser.parse_args()

    with connection() as conn:
        published = conn.execute(
            """
            SELECT a.id, a.slug, a.title, c.slug category_slug
            FROM articles a
            JOIN categories c ON c.id = a.category_id
            WHERE a.status = 'published'
            ORDER BY c.slug, a.published_at DESC
            """
        ).fetchall()
        by_slug = {str(row["slug"]): row for row in published}
        missing = sorted(KEEP_SLUGS - set(by_slug))
        if missing:
            raise RuntimeError(
                "Selected articles are not currently published: " + ", ".join(missing)
            )
        retained = [row for row in published if str(row["slug"]) in KEEP_SLUGS]
        moved = [row for row in published if str(row["slug"]) not in KEEP_SLUGS]
        print(
            json.dumps(
                {
                    "mode": "apply" if args.apply else "dry-run",
                    "publishedBefore": len(published),
                    "retained": len(retained),
                    "movedToReview": len(moved),
                    "retainedByCategory": dict(
                        Counter(str(row["category_slug"]) for row in retained)
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        for row in retained:
            print(f"KEEP\t{row['category_slug']}\t{row['slug']}\t{row['title']}")
        if not args.apply:
            return
        if moved:
            placeholders = ", ".join(["%s"] * len(moved))
            conn.execute(
                f"UPDATE articles SET status = 'review' WHERE id IN ({placeholders})",
                [int(row["id"]) for row in moved],
            )
        conn.execute(
            """
            INSERT INTO traffic_events(
                event_type, visitor_type, agent_name, article_id,
                occurred_at, metadata
            ) VALUES(%s, 'human', %s, NULL, %s, %s)
            """,
            (
                "editorial_index_curation",
                "maintenance-script",
                utc_now(),
                json.dumps(
                    {
                        "retainedSlugs": sorted(KEEP_SLUGS),
                        "movedCount": len(moved),
                        "reversible": True,
                    },
                    ensure_ascii=False,
                ),
            ),
        )

    print(f"Applied: {len(moved)} articles moved to review; no rows deleted.")
    if args.notify and moved:
        function_name = os.environ.get(
            "GEO_INDEXING_NOTIFIER_FUNCTION",
            "geo-intelligence-indexing-notifier",
        )
        response = boto3.client(
            "lambda",
            region_name=os.environ.get("AWS_REGION", "us-east-1"),
        ).invoke(
            FunctionName=function_name,
            InvocationType="RequestResponse",
            Payload=json.dumps(
                {
                    "slugs": [str(row["slug"]) for row in moved],
                    "categories": sorted(
                        {str(row["category_slug"]) for row in published}
                    ),
                    "reason": "editorial_index_curation",
                }
            ).encode("utf-8"),
        )
        payload = json.loads(response["Payload"].read() or "{}")
        if response.get("FunctionError"):
            raise RuntimeError(f"Indexing notifier failed: {payload}")
        print(
            json.dumps(
                {
                    "notifierStatus": response.get("StatusCode"),
                    "urlCount": payload.get("urlCount"),
                    "indexNowStatus": payload.get("indexnowStatus"),
                    "invalidationId": payload.get("invalidationId"),
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
