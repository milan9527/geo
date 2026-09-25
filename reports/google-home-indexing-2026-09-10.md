# Google homepage indexing investigation

Dated snapshot: **2026-09-10**. This English summary was rewritten from the historical report; later releases may supersede its state.

The supplied Search Console snapshot recorded a successful smartphone Googlebot fetch on September 9 at 11:48:12, crawling/indexing allowed and a matching canonical. “Crawled — currently not indexed” did not identify a robots, noindex or canonical conflict, nor did it establish the specific reason for non-indexing.

The initial homepage HTML lacked article links and summaries; JavaScript loaded them later. The deployed fix server-rendered fifteen unique published articles with matching ItemList data. Home lists up to thirty articles; complete categories provide discovery for the remainder. Publication notifications invalidate home, article, category, sitemap and RSS caches.

CloudFront's exact `/` route was directed to ECS, `/index.html` retained a 301, and unknown pages retained real 404s. API task definition 28 and public assets were deployed. Both sitemap files were valid HTTP 200 XML; the reported temporary processing error was not reproduced during the audit.

IndexNow acceptance did not prove indexing, and Google does not use IndexNow. No authorized Search Console account action was performed. The proposed follow-up was to check sitemap status, run a live URL test for server-rendered article links, then request indexing through the owner's account.

Evidence: [before audit](search-audit-2026-09-10-before.json), [before responses](search-responses-2026-09-10-before.json), [after audit](search-audit-2026-09-10.json), [browser checks](home-browser-2026-09-10.json), [deployment](home-deployment-2026-09-10.json).

[Original reference in Git history](https://github.com/milan9527/geo/blob/a2de0e830f363177b0138409d93aebc512de4237/reports/google-home-indexing-2026-09-10.md) · [Report index](README.md)
