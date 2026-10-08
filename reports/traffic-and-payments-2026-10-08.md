# Internal payment stop and traffic improvements

Snapshot: October 8, 2026, UTC. The internal commerce purchase was disabled at **01:56:28 UTC**. Existing publications remain available, and the six daily research schedules continue to run.

[Structured verification results and redirect mappings](traffic-and-payments-2026-10-08.json)

## Internal x402 purchases

The daily `commerce-feed-miner` schedule had `allowPayment: true` and an assignment to this site's own x402 article endpoint through its older CloudFront domain. Source 12 is now paused, its assignment is disabled, and all six schedule inputs set payment permission to false.

Provisioning defaults, the Lambda bridge, and the runtime now prevent recurring purchases. Explicit manual purchases of external evidence remain available. The payment tool rejects this site's domains, configured aliases, and internal challenge resource URLs; it also rejects redirects rather than forwarding payment proofs. The public seller endpoint remains available to external buyers. Verification used mocked payments and read-only runtime calls, with no live purchase.

## Traffic diagnosis

The comparison uses complete UTC weeks and CloudFront content/discovery requests. Published [Google](https://developers.google.com/static/search/apis/ipranges/googlebot.json) and [Bing](https://www.bing.com/toolbox/bingbot.json) IP ranges were used to distinguish actual search crawlers from requests merely carrying their user-agent names.

| Observation | September 24–30 | October 1–7 |
| --- | ---: | ---: |
| Verified Google crawler requests | 25 | 30 |
| Verified Bing crawler requests | 68 | 89 |
| Browser-reported page views | 37 | 1 |
| Browser-reported sessions | 12 | 1 |

Earlier user-agent-based crawler totals included deployment checks. Browser events also include some earlier launch checks and are approximate; they are not independently verified people. The one browser-reported session in the later week came from Bing. These small counts cannot establish a ranking penalty or identify why an individual reader stopped visiting.

Across the two weeks, Bing received **66 HTTP 404 responses across 58 paths representing 56 old article slugs**. No search-crawler 5xx response was found in this audited request subset. Forty of the 42 scheduled research jobs in October 1–7 completed; two market-data jobs timed out. Research production was continuing, with 49 published articles and approved English editions at the audit snapshot.

## Changes

- Added **27 reviewed permanent redirects** from old article addresses to published articles covering their core content. The review compared each of the 56 old manuscripts with two candidate destinations: 112 full-text comparisons. A redirect required duplicate coverage, confidence of at least 0.9, no material independent contribution, and a recommendation to retain the published destination. The other 29 addresses remain unresolved; they were not redirected to an unrelated page.
- Preserved all 49 existing publications, their slugs, and their article content. Redirect records retain manuscript hashes and the full comparison decisions.
- Added a prominent bilingual homepage section for the original Aurora Data API and permanent-URL engineering cases. Those articles remain visible when they fall outside the newest 30 publications. Added an RSS entry point near the beginning of the homepage.
- Added contextual links to both cases in the GitHub README with campaign attribution. These are links from the project's own repository, not independent endorsements or proof that Bing's backlink advisory has cleared.
- Marked future search-audit requests with a diagnostic user agent so they are excluded from the edge traffic aggregation.
- Refreshed Alpine packages after the release scan identified the old zlib package. Both images use the patched package; daily build arguments refresh the operating-system package layer.

## Release verification

AgentCore runtime version 51 and API task revision 39 are deployed. The runtime is healthy, automatic publication is enabled, and the commerce crawler selects eight open sources and zero paid sources. At 02:12 UTC, the source remained paused after the API restart, no new x402 event had been recorded since the stop, and the next scheduled market-data job had started normally.

All 49 original article slugs and content hashes were preserved, with 49 matching, approved English editions. The 27 new redirects cover **34 of the 66 historical Bing 404 requests** in the audited window and bring the site's total reviewed redirect mappings to 46.

Fifty local tests passed, including payment-policy, publication-protection, catalogue, bilingual, browser-navigation, and attribution checks. Production checks passed for desktop English, mobile English and Chinese, RSS, case navigation, and 216 GET/HEAD redirect cases across HTML and open JSON routes. Both deployed image scans reported zero High or Critical findings.

Search checks passed for **120 canonical URLs**, including 98 article language editions, under three crawler user agents: 360 page checks. Bing IndexNow returned **HTTP 200 for 174 URLs**: the 120 canonical URLs plus 54 English/Chinese old article addresses. This confirms notification acceptance, not indexing. Google can discover the maintained sitemaps and redirects; no Google account-level indexing request was made.

## Measuring the result

Use the existing admin reader-attribution report to compare weekly browser sessions, engaged reads, RSS clicks, and returning sessions. The README links use `utm_source=github`, `utm_medium=referral`, and `utm_campaign=engineering_cases`. RSS clicks measure interest, not confirmed subscriptions.

Search crawling and notification acceptance do not prove indexing, rankings, independent backlinks, or increased readership. Google Search Console and Bing Webmaster account-level impressions, clicks, and index counts were not accessible in this run. No posts or messages were sent to outside communities.
