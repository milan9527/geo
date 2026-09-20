#!/usr/bin/env python3
"""Apply a reviewed URL repair plan; default is validation only.

The private review directory must contain snapshot.json, revisions/<id>.json,
revised-review/{quality,pairs}/..., and plan.json. No model calls or relaxed
publication gates happen during this transaction.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.database import connection, utc_now
from editorial_policy import DEDUPE_POLICY_VERSION
from scripts.review_articles import (
    content_hash, mechanical_checks, pair_signature, quality_gates,
    quality_signature, write_json,
)


def read(path):
    return json.loads(path.read_text())


def validate(directory):
    baseline = {a["id"]: a for a in read(directory / "snapshot.json")["articles"]}
    plan = read(directory / "plan.json")
    if plan.get("operation", "manual_editorial_url_repair") not in {
        "manual_editorial_url_repair", "manual_original_publication",
    }:
        raise ValueError("Unsupported reviewed publication operation")
    revisions = {i: read(directory / "revisions" / f"{i}.json") for i in plan["revise"]}
    redirects = {int(k): int(v) for k, v in plan["redirects"].items()}
    assert not set(revisions) & set(redirects), "A URL cannot be restored and redirected"
    final = {**{i: a for i, a in baseline.items() if a["status"] == "published"}, **revisions}
    audit_dir = directory / "revised-review"
    qualities, pairs = {}, {}
    for i, article in revisions.items():
        assert article["id"] == i and article["slug"] == baseline[i]["slug"]
        assert article["content_hash"] == content_hash(article), f"Changed revision {i}"
        quality = read(audit_dir / "quality" / f"{i}.json")
        assert quality["cacheKey"] == quality_signature(article), f"Stale quality audit {i}"
        assert quality["contentHash"] == article["content_hash"]
        assert quality["checks"] == mechanical_checks(article)
        assert all(quality_gates(quality, quality["checks"]).values()), f"Quality failed: {i}"
        qualities[i] = quality
        for j, other in final.items():
            if i == j:
                continue
            a, b = sorted([i, j])
            pair = read(audit_dir / "pairs" / f"{a}-{b}.json")
            assert pair["signature"] == pair_signature(article, other), f"Stale pair {a}-{b}"
            assert pair["dedupePolicyVersion"] == DEDUPE_POLICY_VERSION
            assert pair["relation"] in {"distinct", "overlap_distinct"}, f"Duplicate: {a}-{b}"
            assert pair["confidence"] >= .9, f"Uncertain: {a}-{b}"
            assert pair["materialDifferences"], f"No independent contribution: {a}-{b}"
            pairs[f"{a}-{b}"] = pair
    redirect_reviews = {}
    for source, target in redirects.items():
        assert baseline[source]["status"] != "published" and target in final
        a, b = sorted([source, target])
        pair = read(audit_dir / "pairs" / f"{a}-{b}.json")
        assert pair["signature"] == pair_signature(baseline[source], final[target])
        assert pair["relation"] == "duplicate" and pair["confidence"] >= .9, f"Unconfirmed redirect {source}"
        assert not pair["materialDifferences"], f"Independent content would be lost: {source}"
        assert pair["preferredArticleId"] == target, f"Target lacks coverage for {source}"
        redirect_reviews[source] = pair
    return baseline, plan, revisions, redirects, final, qualities, pairs, redirect_reviews


def load_article(conn, article_id):
    row = conn.execute("SELECT * FROM articles WHERE id=%s", (article_id,)).fetchone()
    if row is None:
        raise ValueError(f"Article {article_id} disappeared")
    article = dict(row)
    article["sources"] = [dict(s) for s in conn.execute(
        "SELECT * FROM sources WHERE article_id=%s ORDER BY id", (article_id,),
    ).fetchall()]
    return article


def apply(directory, validated):
    baseline, plan, revisions, redirects, final, qualities, pairs, redirect_reviews = validated
    plan_hash = hashlib.sha256(json.dumps({
        "plan": plan, "revisions": {i: a["content_hash"] for i, a in revisions.items()},
        "redirects": redirect_reviews,
    }, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    now = utc_now()
    operation = plan.get("operation", "manual_editorial_url_repair")
    result = {"planHash": plan_hash, "operation": operation, "appliedAt": now, "redirects": redirects,
              "restored": [i for i in revisions if baseline[i]["status"] != "published"],
              "revisedPublished": [i for i in revisions if baseline[i]["status"] == "published"],
              "persistedHashes": {}, "researchRunIds": {}}
    with connection() as conn:
        # Same lock order as automatic publication. No calls to a model/network
        # while these locks are held. Readers can continue to serve the site.
        conn.execute("LOCK TABLE articles IN SHARE ROW EXCLUSIVE MODE")
        conn.execute("LOCK TABLE sources IN SHARE ROW EXCLUSIVE MODE")
        conn.execute("LOCK TABLE article_redirects IN SHARE ROW EXCLUSIVE MODE")
        existing = conn.execute(
            "SELECT id FROM research_runs WHERE tool_trace_json::jsonb ->> 'legacyRepairPlan'=%s LIMIT 1",
            (plan_hash,),
        ).fetchone()
        if existing:
            raise ValueError("This repair plan has already been committed; do not apply twice")
        published = {int(r["id"]) for r in conn.execute(
            "SELECT id FROM articles WHERE status='published'",
        ).fetchall()}
        assert published == {i for i, a in baseline.items() if a["status"] == "published"}, "Catalog changed"
        for i in published | set(revisions) | set(redirects):
            current = load_article(conn, i)
            assert current["slug"] == baseline[i]["slug"] and current["status"] == baseline[i]["status"], f"State changed: {i}"
            assert content_hash(current) == baseline[i]["content_hash"], f"Content changed: {i}"
        for i, article in revisions.items():
            agent = conn.execute(
                "SELECT agent_id FROM research_runs WHERE id=%s", (baseline[i]["latest_run"]["id"],),
            ).fetchone()
            audit = {
                "status": "verified", "score": qualities[i]["score"],
                "completeArticle": True, "unsupportedClaims": [], "citationIssues": [], "causalityRisks": [],
                "qualityReview": qualities[i], "reviewedContentHash": article["content_hash"],
                "semanticDeduplication": {"approved": True, "policyVersion": DEDUPE_POLICY_VERSION,
                    "reason": operation, "catalogIds": sorted(set(final) - {i}),
                    "pairReviews": [p for p in pairs.values() if i in (p["a"], p["b"])]},
            }
            conn.execute(
                """UPDATE articles SET title=%s,dek=%s,summary=%s,body_json=%s,keywords=%s,
                   read_minutes=%s,updated_at=%s,status='published',authority_score=%s,citation_count=%s
                   WHERE id=%s""",
                (article["title"], article["dek"], article["summary"], article["body_json"],
                 article["keywords"], article["read_minutes"], now, qualities[i]["score"],
                 len(article["sources"]), i),
            )
            conn.execute("DELETE FROM sources WHERE article_id=%s", (i,))
            for source in article["sources"]:
                conn.execute(
                    """INSERT INTO sources(article_id,publisher,title,url,published_at,source_type)
                       VALUES(%s,%s,%s,%s,%s,%s)""",
                    (i, *(source[k] for k in ("publisher", "title", "url", "published_at", "source_type"))),
                )
            result["persistedHashes"][i] = content_hash(load_article(conn, i))
            audit["persistedContentHash"] = result["persistedHashes"][i]
            run = conn.execute(
                """INSERT INTO research_runs(agent_id,status,category_slug,topic,started_at,completed_at,
                   model_id,summary,output_article_id,verification_status,verification_json,tool_trace_json)
                   VALUES(%s,'completed',%s,%s,%s,%s,%s,%s,%s,'verified',%s,%s) RETURNING id""",
                (agent["agent_id"], article["category_slug"], article["title"], now, now,
                 os.environ["BEDROCK_MODEL_ID"], article["summary"], i,
                 json.dumps(audit, ensure_ascii=False),
                 json.dumps({"operation": operation, "legacyRepairPlan": plan_hash})),
            ).fetchone()
            result["researchRunIds"][i] = run["id"]
            for e in article["evidence"]:
                conn.execute(
                    """INSERT INTO research_evidence(run_id,publisher,title,url,published_at,
                       retrieved_at,source_type,content_excerpt,data_json) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (run["id"], *(e.get(k) or (now if k == "retrieved_at" else "{}" if k == "data_json" else "")
                                 for k in ("publisher", "title", "url", "published_at", "retrieved_at",
                                           "source_type", "content_excerpt", "data_json"))),
                )
        for source, target in redirects.items():
            conn.execute(
                """INSERT INTO article_redirects(source_slug,source_article_id,target_article_id,
                   source_content_hash,target_content_hash,review_json,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                (baseline[source]["slug"], source, target, baseline[source]["content_hash"],
                 content_hash(load_article(conn, target)),
                 json.dumps({"planHash": plan_hash, "pairReview": redirect_reviews[source]}, ensure_ascii=False), now),
            )
        assert {r["id"] for r in conn.execute("SELECT id FROM articles WHERE status='published'").fetchall()} == set(final)
    write_json(directory / "applied.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not __debug__:
        raise RuntimeError("Do not disable validation with python -O")
    validated = validate(args.directory)
    result = apply(args.directory, validated) if args.apply else {
        "valid": True, "revisions": len(validated[2]), "redirects": len(validated[3]),
        "reviewedPairs": len(validated[6]),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
