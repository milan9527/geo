#!/usr/bin/env python3
"""Backfill reviewed English editions without changing Chinese content or URLs."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.bilingual import create_english, ensure_schema, save_english, source_hash
from backend.database import connection
from backend.research import read_research_rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--enable-policy", action="store_true", help="Require reviewed bilingual editions for all future publication; requires --apply")
    args = parser.parse_args()
    if args.enable_policy and not args.apply:
        parser.error("--enable-policy requires --apply")
    args.directory.mkdir(parents=True, exist_ok=True)
    if args.apply:
        with connection() as conn:
            ensure_schema(conn)
    with connection() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        articles = read_research_rows(conn, "SELECT * FROM articles WHERE status='published'", order_by="id")
        sources = read_research_rows(conn, "SELECT * FROM sources ORDER BY id", order_by="id")
        headers = [dict(r) for r in conn.execute("SELECT id,title,dek,summary FROM articles ORDER BY id")]
    for article in articles:
        article["sources"] = [s for s in sources if s["article_id"] == article["id"]]
    (args.directory / "snapshot.json").write_text(json.dumps({"articles": articles, "headers": headers}, ensure_ascii=False))
    print(json.dumps({"publishedArticles": len(articles), "catalogArticles": len(headers)}), flush=True)

    def translate(article):
        path = args.directory / f"{article['id']}.json"
        edition = json.loads(path.read_text()) if path.exists() else None
        if not edition or edition["sourceHash"] != source_hash(article):
            edition = create_english(article)
            path.write_text(json.dumps(edition, ensure_ascii=False, indent=2))
        if args.apply:
            with connection() as conn:
                current = dict(conn.execute("SELECT * FROM articles WHERE id=%s FOR UPDATE", (article["id"],)).fetchone())
                if source_hash(current) != edition["sourceHash"]:
                    raise ValueError("Article changed during translation")
                save_english(conn, article, edition)
        return {"id": article["id"], "score": edition["review"]["score"]}

    errors = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(translate, article): article["id"] for article in articles}
        for future in as_completed(futures):
            try:
                print(json.dumps(future.result()), flush=True)
            except Exception as error:
                result = {"id": futures[future], "error": str(error)[:1500]}
                errors.append(result)
                print(json.dumps(result, ensure_ascii=False), flush=True)
    (args.directory / "errors.json").write_text(json.dumps(errors, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(f"{len(errors)} translations require revision; rerun resumes approved editions.")

    if args.enable_policy:
        with connection() as conn:
            conn.execute("LOCK TABLE articles, article_translations IN SHARE ROW EXCLUSIVE MODE")
            missing = conn.execute("""SELECT a.id FROM articles a WHERE a.status='published'
                AND NOT EXISTS(SELECT 1 FROM article_translations t WHERE t.article_id=a.id AND t.locale='en'
                  AND t.source_hash=md5(concat_ws(chr(31),a.title,a.dek,a.summary,a.body_json,a.keywords))
                  AND t.review_json::jsonb->>'approved'='true'
                  AND t.review_json::jsonb->>'complete'='true'
                  AND t.review_json::jsonb->>'faithful'='true'
                  AND (t.review_json::jsonb->>'score')::numeric>=90
                  AND t.review_json::jsonb->'issues'='[]'::jsonb)""").fetchall()
            if missing:
                raise ValueError("Published catalogue changed; rerun backfill before enabling the policy")
            conn.execute("UPDATE bilingual_policy SET required=TRUE")
        print(json.dumps({"bilingualPolicyRequired": True}), flush=True)


if __name__ == "__main__":
    main()
