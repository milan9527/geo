# Functional verification

Run tests with the repository virtual environment, Playwright Chromium and a disposable local PostgreSQL database. Do not load production database credentials for these tests.

```bash
export PUBLICATION_TEST_DATABASE_URL='postgresql://postgres:local-password@127.0.0.1:55432/geo'
export METRICS_TEST_DATABASE_URL="$PUBLICATION_TEST_DATABASE_URL"
export RESEARCH_TEST_DATABASE_URL="$PUBLICATION_TEST_DATABASE_URL"
for test_file in scripts/test_*.py; do
  .venv/bin/python "$test_file" || exit 1
done
```

Database suites create unique schemas and remove them after execution. Browser workflow suites use the real frontend assets with controlled API responses. API workflow tests use real HTTP, authentication and SQL; AWS secrets, scheduling, crawler invocation and payment settlement are mocked. Tests never launch production research jobs, publish test articles or transfer funds.

The settings page's collection/identification/access/payment switches currently persist preferences only. They are not wired to runtime behavior, and the interface states this limitation. Payment alert delivery and automatic analytics cleanup are not configured. Settings tests verify persistence, validation and error recovery; they do not claim these runtime controls or notifications work. Automatic publication has separate, enforced runtime and database controls.

| Feature | Regression suites |
| --- | --- |
| English/Chinese defaults, complete translations, preserved drafts/evidence/code, immutable publication URLs | `test_bilingual.py`, `test_publication_protection.py`, `test_admin_login.py` |
| Complete categories, pagination, search, Aurora response limits | `test_public_catalog.py`, `test_admin_metrics.py`, `test_admin_research.py` |
| Public navigation, head metadata, redirects, history, failed loads, safe search rendering, clipboard and RSS | `test_public_navigation.py`, `test_homepage.py`, `test_search_metadata.py` |
| Login, session recovery, creation/save retries, content filters, detail, bulk operations, export, settings, logout | `test_admin_login.py`, `test_admin_workflows.py`, `test_api_workflows.py` |
| Source registration/editing, credentials, assignments, connectivity polling, filtering, pause/delete, schedule editing and manual dispatch | `test_admin_workflows.py`, `test_api_workflows.py` |
| Agent language, payment challenge, rejected payment, settlement failure/success and event recording | `test_api_workflows.py` |
| Evidence quality, full-catalogue semantic deduplication, concurrent changes, automatic publication and index notification | `test_editorial_review.py`, `test_scheduled_publication.py`, `test_runtime_publication.py`, `test_bilingual.py`, `test_indexing_notifier.py` |
| Human/agent traffic, attribution, diagnostic exclusions and complete statistics | `test_growth.py`, `test_traffic_aggregator.py`, `test_admin_metrics.py` |

After deployment, verify public pages and existing redirects with read-only HTTP requests, compare category counts with the published catalogue, and exercise both languages in desktop/mobile browsers. Verify admin views using an authorized account; keep article/source/settings mutations in isolated tests. Check the live runtime's automatic-publication setting, enabled schedules and database bilingual policy.

Run the crawler audit before notifying Bing:

```bash
.venv/bin/python scripts/check_search_indexing.py \
  --output /private/review-directory/search-audit.json \
  --submit-indexnow
```

The audit fetches every sitemap URL as Bingbot, Googlebot and smartphone Googlebot. It checks HTTP status, robots rules, canonical URLs, document language, reciprocal bilingual alternates, titles, H1s, descriptions, verification files, genuine 404s, and links from home/category HTML to all published articles. Submission occurs only if every required check passes, using Bing's `https://www.bing.com/indexnow` endpoint.

HTTP 200/202 confirms that Bing accepted the notification. Actual indexing and ranking require search-engine processing; account-level indexed-page counts are not inferred from submission results.
