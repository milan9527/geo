# Functional coverage and Bing notification

Dated snapshot: **2026-09-25**. This English summary was rewritten from the historical report; later releases may supersede its state.

The review covered public navigation, seven major admin views, automatic review/publication and search discovery. **106 automated tests passed**, plus standalone indexing-notifier and traffic-classification checks. Mutating workflows used isolated databases or mocks; production tests did not publish or delete articles or execute payments.

All 41 published source articles and reviewed English editions were preserved. Nineteen legacy mappings worked in both languages. The public sitemap contained 104 URLs, including 82 article URLs. Both editions showed AI 12, Agent 10, cloud 10, commerce/media 4 and finance 5 articles.

Changes fixed complete-category navigation, API pagination and bounded catalogue reads; synchronized canonical/hreflang/structured data on navigation; preserved language in legacy and Agent links; corrected search races, clipboard errors and temporary-load handling; and improved admin save/retry and session recovery. Editing an English source label no longer overwrites original registry fields with display translations.

Bingbot, Googlebot and smartphone Googlebot completed 312 successful page checks, plus 26 site-level checks. On **2026-09-25 at 15:29:24 UTC**, Bing's `https://www.bing.com/indexnow` accepted all 104 URLs with HTTP 200. This is a submission receipt, not an indexed-page count; the Bing Webmaster account report was not read.

Known limits remain: collection/identification/access/payment switches save preferences without controlling runtime behavior; payment alerts and automatic database analytics cleanup are not configured. During concurrent checks, some uncached categories took about nine seconds versus roughly 0.05–0.08 seconds when cached. This was not a performance benchmark.

API task definition 38 and runtime version 50 were verified; runtime was READY with automatic publication enabled, six enabled schedules and the database bilingual gate active. Frontend assets matched the repository, and temporary users were removed. See [reproduction instructions](../docs/functional-verification.md).

[Structured evidence](functional-coverage-2026-09-25.json).

[Original reference in Git history](https://github.com/milan9527/geo/blob/a2de0e830f363177b0138409d93aebc512de4237/reports/functional-coverage-2026-09-25.md) · [Report index](README.md)
