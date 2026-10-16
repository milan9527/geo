# Reader value and connected research

Aperture commissions a research question before drafting. The assignment names a professional audience, the decision it informs, why the evidence matters now, the contribution beyond existing coverage, and an evidence-backed outline. All five categories follow this policy; finance is not limited to AI spending, and commerce is not limited to Agent pricing.

## Publication decisions

1. **New research:** a distinct, supported question warrants its own permanent page.
2. **Update existing research:** material new evidence changes an existing question. A private candidate is reviewed against the original article; the published URL, category, original publication date, and valid history are retained.
3. **Hold:** the evidence does not justify either action. A completed crawl can produce an editorial hold without creating another repetitive draft.

The assignment and the finished article receive separate reader-value reviews. Approval requires a score of at least 90, every substantive check passing, and no outstanding issues. The existing factual, source-diversity, citation, full-catalogue deduplication, and bilingual checks remain in place. An article is not published simply because it is long or has a high factual-review score.

One defect-directed revision is allowed after a failed assignment, reader-value or knowledge review; each revised artifact is independently checked again. A low-confidence semantic comparison receives at most one independent second review; disagreement stays pending. Rechecks of the same pending candidate have a 48-hour cooldown unless a specific article is explicitly requested.

Errors, empty news indexes, and filing-heading stubs are excluded from evidence. Undated sources retain an empty publication date; retrieval time is recorded separately. Generated crawlers retain the existing URL allowlist, source request policies and rate controls.

## Knowledge map

`/knowledge` and `/zh/knowledge` connect companies, products, concepts and questions. Selecting a node exposes related research and directed relationships. Each relationship includes its explanation, an original-article quotation and translation, citation numbers, source links, and a distinction between reported evidence, article analysis and conditional hypotheses. Co-occurrence alone does not establish a relationship.

Every published article can carry a bilingual reader guide and links to its concepts. Related-reading selection ranks shared reviewed nodes before falling back to category and recency. The public graph includes only independently approved metadata matching the exact current article and ordered sources. Draft and stale relationships are excluded.

Aurora stores `article_knowledge`, canonical `knowledge_nodes`, article-node memberships and `knowledge_edges`. `current_article_knowledge` filters public visibility. This scale does not require a separate graph database. Canonical English labels, common aliases and the existing vocabulary help merge equivalent entities; conceptual equivalence beyond these rules still depends on editorial review.

`article_revisions` retains the previous full article, source list, English edition, change summary and update review. The final update transaction rechecks the complete published catalogue and candidate, archives the previous version, saves the reviewed English edition and replaces the target's content and knowledge metadata atomically. Model inference happens before acquiring publication locks.

## Operations

The admin research view exposes assignment, reader value, hold reason and revision outcome in the selected language. The knowledge view loads independently and reports public mapping coverage. Public graph API: `GET /api/v1/knowledge?lang=en&node=<id>&q=<term>&offset=0`. Authenticated operational equivalent: `/api/admin/knowledge`. Results contain up to 250 matching nodes, 12 articles per page and 40 relationship records for a selected node; `hasMore` applies to articles. Selecting another node continues traversal.

Backfill existing pages without rewriting their bodies or changing their URLs:

```bash
set -a
source .env.aws
set +a
.venv/bin/python scripts/build_knowledge.py .review-runs/knowledge --apply --workers 4
```

The script creates the additive schema, snapshots published content, supplies archived evidence, independently reviews graph extraction, caches approved results, and validates the current article and sources before saving. Failed extractions remain unexposed. Rerunning resumes matching approved results. Running without `--apply` prepares review artifacts but still creates the additive schema.

Deploy the API/admin and runtime together, then update the indexing notifier with `scripts/provision_search_indexing.sh`. Knowledge pages appear in both language sitemaps and IndexNow notifications. An accepted submission is a discovery request, not confirmation of indexing or traffic growth. Recurring internal x402 purchases remain disabled.
