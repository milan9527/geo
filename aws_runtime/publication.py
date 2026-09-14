"""Fail-closed publication gate shared by scheduled generation and rechecks."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from editorial_policy import DEDUPE_POLICY_VERSION, validate_pair


def canonical_source_url(url):
    parsed = urlsplit(str(url or "").strip())
    query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in {"ref", "source", "campaign"}]
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(),
                      parsed.path.rstrip("/") or "/", urlencode(sorted(query)), ""))


def quality_gate(output, evidence, verification, *, human_title):
    urls = {canonical_source_url(e.get("url")) for e in evidence if e.get("url")}
    publishers = {str(e.get("publisher") or "").strip().casefold() for e in evidence}
    publishers.discard("")
    archived = {canonical_source_url(e.get("url")) for e in evidence
                if e.get("url") and (str(e.get("excerpt") or "").strip() or e.get("data"))}
    sections = output.get("sections") or []
    text = json.dumps(sections, ensure_ascii=False)
    citations = [int(value) for value in re.findall(r"\[S(\d+)\]", json.dumps(output, ensure_ascii=False))]
    checks = {
        "verified": verification.get("status") == "verified",
        "verificationScore": int(verification.get("score") or 0) >= 90,
        "noMaterialIssues": all(isinstance(verification.get(key), list) and not verification[key]
                               for key in ("unsupportedClaims", "citationIssues", "causalityRisks")),
        "completeArticle": verification.get("completeArticle") is True,
        "sourceCount": len(urls) >= 5,
        "publisherDiversity": len(publishers) >= 3,
        "archivedEvidence": len(archived) >= 5,
        "validCitations": bool(citations) and all(1 <= value <= len(evidence) for value in citations),
        "substantiveLength": len(text) >= 3000,
        "humanTitle": bool(human_title),
    }
    return {"ready": all(checks.values()), "checks": checks, "sourceCount": len(urls),
            "distinctPublishers": len(publishers), "archivedSources": len(archived),
            "articleCharacters": len(text), "policy": "quality_then_full_text_deduplication",
            "dedupePolicyVersion": DEDUPE_POLICY_VERSION}


def content_identity(article):
    content = {key: article.get(key) for key in (
        "category_id", "title", "dek", "summary", "body_json", "keywords",
    )}
    # Source order is significant: it defines [S1], [S2], ... in the article.
    content["sources"] = [{key: source.get(key) for key in (
        "publisher", "title", "url", "published_at", "source_type",
    )} for source in article["sources"]]
    return hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def state_identity(article):
    return (content_identity(article), article["status"], article["updated_at"])


class PublicationStore:
    """sql returns dictionaries; transaction supplies one database transaction id."""

    def __init__(self, sql, transaction):
        self.sql = sql
        self.transaction = transaction

    def article(self, article_id, tx=None):
        rows = self.sql(
            """SELECT a.*, c.slug category_slug, r.target_article_id redirect_target_id
               FROM articles a JOIN categories c ON c.id=a.category_id
               LEFT JOIN article_redirects r ON r.source_article_id=a.id
               WHERE a.id=:article_id""",
            {"article_id": article_id}, transaction_id=tx,
        )
        if not rows:
            raise ValueError("Article no longer exists")
        article = dict(rows[0])
        article["sources"] = self.sql(
            "SELECT * FROM sources WHERE article_id=:article_id ORDER BY id",
            {"article_id": article_id}, transaction_id=tx,
        )
        return article

    def published(self, tx=None):
        result, after = [], 0
        while True:
            batch = self.sql(
                """SELECT a.*, c.slug category_slug FROM articles a
                   JOIN categories c ON c.id=a.category_id
                   WHERE a.status='published' AND a.id > :after_id ORDER BY a.id LIMIT 8""",
                {"after_id": after}, transaction_id=tx,
            )
            if not batch:
                return result
            for row in batch:
                article = dict(row)
                article["sources"] = self.sql(
                    "SELECT * FROM sources WHERE article_id=:article_id ORDER BY id",
                    {"article_id": article["id"]}, transaction_id=tx,
                )
                result.append(article)
            after = batch[-1]["id"]

    def save_draft(self, values, evidence, update_candidate=None, *, require_existing=False):
        """Never replace a live article; article and source updates are atomic."""
        fields = (
            "category_id", "title", "dek", "summary", "author", "author_role",
            "read_minutes", "updated_at", "hero_style", "authority_score",
            "citation_count", "keywords", "body_json",
        )
        params = {field: values[field] for field in fields}
        params["status"] = "review"
        with self.transaction() as tx:
            current = []
            if update_candidate:
                current = self.sql(
                    """SELECT a.id,a.status,a.updated_at,
                              EXISTS(SELECT 1 FROM article_redirects r WHERE r.source_article_id=a.id) redirected
                       FROM articles a WHERE a.id=:article_id FOR UPDATE OF a""",
                    {"article_id": update_candidate["output_article_id"]}, transaction_id=tx,
                )
            can_update = bool(current and current[0]["status"] in {"draft", "review"}
                              and not current[0].get("redirected")
                              and current[0]["updated_at"] == update_candidate.get("updated_at"))
            if require_existing and not can_update:
                raise ValueError("Draft changed while its evidence was being reviewed")
            if can_update:
                params.pop("status")
                params["article_id"] = current[0]["id"]
                assignments = ", ".join(f"{field}=:{field}" for field in fields)
                article = self.sql(
                    f"""UPDATE articles SET {assignments}, status='review'
                        WHERE id=:article_id AND status IN ('draft','review') RETURNING id,slug""",
                    params, transaction_id=tx,
                )[0]
                self.sql("DELETE FROM sources WHERE article_id=:article_id",
                         {"article_id": article["id"]}, transaction_id=tx)
            else:
                params.update(slug=values["slug"], published_at=values["published_at"])
                names = [*fields, "status", "slug", "published_at"]
                article = self.sql(
                    f"""INSERT INTO articles({','.join(names)},featured,access_model,agent_price)
                        VALUES({','.join(':'+field for field in names)},FALSE,'open',0)
                        RETURNING id,slug""", params, transaction_id=tx,
                )[0]
            for item in evidence:
                self.sql(
                    """INSERT INTO sources(article_id,publisher,title,url,published_at,source_type)
                       VALUES(:article_id,:publisher,:title,:url,:published_at,:source_type)""",
                    {"article_id": article["id"], "publisher": item["publisher"], "title": item["title"],
                     "url": item["url"], "published_at": item["publishedAt"], "source_type": item["sourceType"]},
                    transaction_id=tx,
                )
            saved = self.article(article["id"], tx)
        return {**article, "contentHash": content_identity(saved),
                "categorySlug": saved["category_slug"],
                "action": "updated_existing_draft" if can_update else "created_review_draft"}

    def commit_publication(self, candidate, public_articles):
        with self.transaction() as tx:
            # Model work happens before this short transaction. Every publisher
            # serializes here, including two jobs reviewing the same old catalog.
            self.sql("LOCK TABLE articles, sources IN SHARE ROW EXCLUSIVE MODE", transaction_id=tx)
            current = self.article(candidate["id"], tx)
            if state_identity(current) != state_identity(candidate):
                return False, "candidate_changed"
            live = self.published(tx)
            if {a["id"]: state_identity(a) for a in live} != {
                a["id"]: state_identity(a) for a in public_articles
            }:
                return False, "published_catalog_changed"
            result = self.sql(
                """UPDATE articles SET status='published', updated_at=:updated_at
                   WHERE id=:article_id AND status IN ('draft','review') RETURNING id""",
                {"article_id": candidate["id"],
                 "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat()},
                transaction_id=tx,
            )
            return bool(result), "published" if result else "candidate_not_pending"


def review_and_publish(store, article_id, expected_hash, gate, compare, *, enabled, workers=4):
    result = {"policyVersion": DEDUPE_POLICY_VERSION, "published": False,
              "comparedArticles": 0, "comparisons": [], "reason": "automatic_publication_disabled"}
    if not enabled:
        return result
    if not gate["ready"]:
        result["reason"] = "quality_gate_failed"
        return result
    try:
        candidate = store.article(article_id)
        if candidate.get("redirect_target_id"):
            result["reason"] = "legacy_url_redirected"
            return result
        if candidate["status"] not in {"draft", "review"} or content_identity(candidate) != expected_hash:
            result["reason"] = "candidate_changed"
            return result
        public = store.published()
        result["candidateHash"] = expected_hash
        result["publishedSnapshot"] = [
            {"articleId": a["id"], "contentHash": content_identity(a)} for a in public
        ]
        with ThreadPoolExecutor(max_workers=max(1, min(4, workers))) as pool:
            pending = {pool.submit(compare, candidate, existing): existing for existing in public}
            for future in as_completed(pending):
                existing = pending[future]
                pair = future.result()
                validate_pair(pair, candidate["id"], existing["id"])
                result["comparisons"].append({**pair, "articleId": existing["id"], "slug": existing["slug"]})
        result["comparisons"].sort(key=lambda item: item["articleId"])
        result["comparedArticles"] = len(result["comparisons"])
        duplicates = [p["articleId"] for p in result["comparisons"] if p["confirmedDuplicate"]]
        if duplicates:
            result.update(reason="duplicate_content", duplicateOf=duplicates)
            return result
        if any(p["relation"] not in {"distinct", "overlap_distinct"} or p["confidence"] < .9
               or (p["relation"] == "overlap_distinct" and not p["materialDifferences"])
               for p in result["comparisons"]):
            result["reason"] = "uncertain_comparison"
            return result
        result["published"], result["reason"] = store.commit_publication(candidate, public)
    except Exception as error:
        result.update(reason="review_unavailable", error=f"{type(error).__name__}: {str(error)[:500]}")
    return result
