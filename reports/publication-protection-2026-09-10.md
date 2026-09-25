# Published-page protection and review of new articles

Dated snapshot: **2026-09-10**. This English summary was rewritten from the historical report; later releases may supersede its state.

The new policy preserves existing published pages. Future quality or duplicate checks cannot routinely delete, withdraw or rename them. Corrections can be made at the original URL with the required review.

Database guards enforce this across the admin API, scheduled jobs and maintenance scripts. Direct publish/bulk-publish controls were removed; APIs reject status changes that bypass review. Existing URLs take precedence over a newer duplicate even when the candidate has a higher score or more background. The old per-category-count withdrawal script was disabled, and old batch plans must be regenerated under `2026-09-10-preserve-published`.

At verification there were seventeen public articles: the original fifteen plus two newly approved scheduled drafts. All sixteen articles in the deployment snapshot retained their status, URL and content fingerprints.

Twelve batch-review tests, six protection/admin HTTP tests and thirteen scheduled-publication tests passed. Browser checks passed, and all 28 sitemap URLs passed 84 desktop/mobile Googlebot and Bingbot requests. The sitemap's seventeen articles matched the database.

Evidence: [database protection](publication-protection-2026-09-10.json), [admin browser](publication-protection-admin-2026-09-10.json), [admin deployment](publication-protection-admin-deployment-2026-09-10.json), [search checks](search-audit-after-publication-protection-2026-09-10.json).

[Structured evidence](publication-protection-2026-09-10.json).

[Original reference in Git history](https://github.com/milan9527/geo/blob/a2de0e830f363177b0138409d93aebc512de4237/reports/publication-protection-2026-09-10.md) · [Report index](README.md)
