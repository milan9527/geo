# English and Chinese editions

English is served at the existing public URLs. Chinese editions use `/zh/`, including home, category, article, author and policy pages. Switching language retains the current page. Each edition has a self-referencing canonical and reciprocal `en`, `zh-CN` and `x-default` alternates. Both appear in the sitemap. RSS is available at `/feed.xml` and `/zh/feed.xml`.

Public and admin APIs default to English; append `lang=zh` for Chinese. The admin interface defaults to English and remembers an explicit language choice in the browser. Source evidence and unpublished drafts retain their original language; publication produces a reviewed English edition of the complete article.

## Publication requirements

The scheduled generator continues to produce a Chinese source article. Existing evidence/quality checks and whole-catalog semantic duplicate review run first. Only an independent article passing those gates proceeds to English translation and independent translation review. The review must confirm completeness and faithful meaning, score at least 90, and report no issues. Structural validation preserves paragraphs, table rows, citation markers, code and numeric fields.

The reviewed edition is stored in `article_translations`. Its revision hash must match the exact Chinese title, dek, summary, body JSON and keywords. Model work runs before the final publication transaction; the transaction rechecks the article and published catalogue. Missing, failed or stale translations leave the article under review. The database publication trigger independently enforces the bilingual requirement when `bilingual_policy.required` is enabled. It also prevents changing a published Chinese body without first storing the matching reviewed English revision in the same transaction. Existing protections continue to preserve published URLs and redirects.

`aws_runtime/publication.py` handles scheduled generation and draft rechecks. The reviewed manual publication/URL repair script also prepares the English revision before its write transaction. Both editions use the same underlying article ID for deduplication and analytics. The indexing notifier submits both URLs and invalidates both sets of pages.

## Backfill and locale maintenance

Load the deployment environment, then run:

```sh
.venv/bin/python scripts/translate_articles.py /private/review-directory --apply
```

The command saves a snapshot and individual translation/review records, resumes approved matching revisions, and never changes original article content, status or URLs. Once every published article has a matching approved edition, add `--enable-policy`; the final transaction checks the entire live catalogue before enabling enforcement. Do not disable the policy to bypass a failed review.

UI dictionaries are checked in at `backend/locales/en.json` and bundled for both frontends. To translate new source labels, run `scripts/build_locales.py`, review the resulting wording, then verify both interfaces. Unpublished article metadata must never be embedded in public JavaScript bundles. Reviewed article editions are stored separately from UI labels.

Regression checks include `scripts/test_bilingual.py`, scheduled publication tests, admin language/session browser tests, notifier tests and traffic classification tests. Database tests require an isolated PostgreSQL database through `PUBLICATION_TEST_DATABASE_URL`; never point that variable at production.
