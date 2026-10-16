#!/usr/bin/env python3
"""Build reviewed bilingual knowledge metadata without rewriting published articles."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend import knowledge
from backend.bilingual import model_json, source_hash
from backend.database import connection, utc_now
from backend.research import read_research_rows


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory",type=Path)
    parser.add_argument("--apply",action="store_true")
    parser.add_argument("--workers",type=int,default=4)
    parser.add_argument("--ids",type=int,nargs="+",help="Review only these published article IDs")
    args=parser.parse_args();args.directory.mkdir(parents=True,exist_ok=True)
    with connection() as conn:
        knowledge.ensure_schema(conn)
    with connection() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        articles=read_research_rows(conn,"SELECT * FROM articles WHERE status='published'",order_by="id")
        sources=read_research_rows(conn,"SELECT s.* FROM sources s JOIN articles a ON a.id=s.article_id WHERE a.status='published'",order_by="id")
        vocabulary=knowledge.vocabulary(conn)
    if args.ids:
        articles=[a for a in articles if a["id"] in set(args.ids)]
    for article in articles:
        article["sources"]=[s for s in sources if s["article_id"]==article["id"]]
    snapshot=args.directory/"snapshot.json"
    if not snapshot.exists(): snapshot.write_text(json.dumps(articles,ensure_ascii=False))
    print(json.dumps({"published":len(articles),"apply":args.apply}),flush=True)
    def build(article):
        path=args.directory/f'{article["id"]}.json'
        result=json.loads(path.read_text()) if path.exists() else None
        if not result or result["contentHash"]!=source_hash(article) or result["sourcesHash"]!=knowledge.sources_hash(article):
            with connection() as conn:
                grounded=knowledge.attach_evidence(conn,article)
            result=knowledge.create_document(grounded,model_json,vocabulary)
            path.write_text(json.dumps(result,ensure_ascii=False,indent=2))
        if args.apply:
            with connection() as conn:
                # Match publication's lock order, protecting content and source revision.
                conn.execute("LOCK TABLE articles,sources IN SHARE ROW EXCLUSIVE MODE")
                current=dict(conn.execute("SELECT * FROM articles WHERE id=%s",(article["id"],)).fetchone())
                current["sources"]=[dict(s) for s in conn.execute("SELECT * FROM sources WHERE article_id=%s ORDER BY id",(article["id"],)).fetchall()]
                if current["status"]!="published":raise ValueError("Publication state changed")
                knowledge.save_document(conn,current,result,utc_now())
        return {"id":article["id"],"score":result["review"]["score"],"nodes":len(result["document"]["nodes"]),"edges":len(result["document"]["edges"])}
    errors=[]
    with ThreadPoolExecutor(max_workers=max(1,min(args.workers,8))) as pool:
        futures={pool.submit(build,a):a["id"] for a in articles}
        for future in as_completed(futures):
            try: print(json.dumps(future.result()),flush=True)
            except Exception as error:
                item={"id":futures[future],"error":str(error)};errors.append(item)
                print(json.dumps(item,ensure_ascii=False),flush=True)
    error_file="errors-"+ "-".join(map(str,sorted(args.ids)))+".json" if args.ids else "errors.json"
    (args.directory/error_file).write_text(json.dumps(errors,ensure_ascii=False,indent=2))
    if errors:raise SystemExit(f"{len(errors)} articles need knowledge review corrections")
if __name__=="__main__":main()
