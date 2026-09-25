# Actual English interface captures

These eleven PNGs were captured from the live public website and admin console on **September 25, 2026, 15:52–15:53 UTC** using Chromium. They show actual rendered English interfaces, with no replacement text, synthetic records or edited metrics. The language picker may name the alternative language in its native script.

The public captures include home, the complete Agent technology category, an engineering article and search. Admin captures include login, overview, published content, article detail, crawlers, sources and settings.

| File | View |
| --- | --- |
| [public-home-en.png](public-home-en.png) | English homepage |
| [public-category-en.png](public-category-en.png) | Agent technology category |
| [public-article-en.png](public-article-en.png) | Published engineering article |
| [public-search-en.png](public-search-en.png) | Actual search overlay |
| [admin-login-en.png](admin-login-en.png) | Login before credentials are entered |
| [admin-dashboard-en.png](admin-dashboard-en.png) | Live overview and testnet metrics |
| [admin-content-en.png](admin-content-en.png) | Normal Published filter selected |
| [admin-article-en.png](admin-article-en.png) | Reviewed English article detail |
| [admin-crawlers-en.png](admin-crawlers-en.png) | Crawlers and schedules |
| [admin-sources-en.png](admin-sources-en.png) | Source registry |
| [admin-settings-en.png](admin-settings-en.png) | Settings with feature-limit notices |

[manifest.json](manifest.json) records source URLs, UTC timestamps, capture regions and SHA-256 hashes. Viewport and element captures retain the original pixels; the deck scales them proportionally. The dashboard uses a shorter natural viewport, and content uses the actual Published filter. Original-language drafts and historical evidence were not rewritten to create screenshots.

A temporary administrator was created solely for capture and removed afterward. The capture did not publish content, edit sources/settings, launch research, or execute payments. Browser errors were absent.

## Recapture

This script targets the reference production origins and requires authorized database access to the corresponding initialized environment. It creates and removes a temporary administrator; do not point it at an unrelated database.

```bash
.venv/bin/pip install -r requirements.txt -r docs/presentation/requirements.txt
.venv/bin/python -m playwright install chromium
set -a
source .env.aws
set +a
.venv/bin/python scripts/capture_english_screenshots.py
```

Credentials are generated in memory and are not included in screenshots or the manifest. The capture script uses ordinary UI navigation and filters. Recheck the cleanup result and inspect all PNGs before committing refreshed captures.
