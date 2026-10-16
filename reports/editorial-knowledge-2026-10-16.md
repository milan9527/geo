# Reader-value publishing and knowledge-map release

Verified on October 16, 2026. [Structured verification results](editorial-knowledge-2026-10-16.json).

Aperture now reviews the professional reader's question, decision and new contribution before drafting. The publication pipeline can commission an independent article, prepare a material update to an existing page, or hold an unsupported topic. Finished articles must pass a separate reader-value review alongside the existing factual, citation, source-diversity, full-catalogue and bilingual checks.

## Published catalogue and knowledge coverage

All **55 published articles** received reviewed bilingual reader guides and knowledge metadata, covering **281 nodes and 202 relationships**. Snapshot comparisons confirmed that their existing URLs, original publication dates and source article bodies were preserved.

The [English knowledge map](https://aperture.zhangwangshu.com/knowledge) and [Chinese knowledge map](https://aperture.zhangwangshu.com/zh/knowledge) support topic search, connected-node navigation and related research. Relationship cards distinguish reported evidence, article analysis and conditional hypotheses, with quotations, numbered sources and links to the original research. Article pages expose the reader guide and connected concepts; the open Agent JSON includes the knowledge document.

Graph extraction and review corrected unsupported causal wording, necessary-condition claims and source-attribution problems. Historical evidence lookup now prefers archives available at the article's revision date and records when only later evidence is available. Graph visibility requires an approved review and matching article/source hashes.

## Automation and page preservation

Runtime **54** is ready with automatic publication enabled. All six schedules remain enabled with `allowPayment: false`; the internal paid source remains paused. Verification executed no payments.

Reviewed updates preserve the existing slug, category and original publication date, save the previous article and English edition, and publish an update summary. The final transaction rechecks the candidate and the whole published catalogue. Independent second duplicate reviews are bounded; conflicting opinions retain the candidate for review.

Live commissioning checks held the finance and commerce proposals for insufficient supporting evidence. These checks did not publish new articles. Future scheduled jobs apply the deployed commissioning and publication rules.

## Verification

- Isolated SQL and model-mock tests covered topic holds, reader-value rejection, evidence grounding, historical archive selection, stale graph visibility, duplicate-review disagreement, atomic revisions, concurrent edits and published-page protection.
- Browser regression tests covered desktop/mobile graph dimensions and links, public navigation, bilingual controls and independent admin knowledge loading with error recovery.
- Live browser checks passed for both languages, node navigation, evidence expansion, topic search, mobile layout, article guides, Agent JSON, admin login, knowledge coverage and logout. No browser errors were recorded; the temporary verification administrator was removed.
- ECS task definition **42** completed deployment. Runtime and API image files, plus deployed public/admin assets, were compared with the reviewed workspace. Image scans reported no High or Critical findings.

## Search discovery

All **134 sitemap URLs**, including both knowledge-map editions and 110 article edition URLs, passed **402 page checks** across Bingbot, Googlebot and smartphone Googlebot, plus 26 catalogue/discovery checks. Canonicals, language alternates, H1s, descriptions, ownership files, real 404s and links to every published article passed.

Bing accepted the IndexNow submission for all 134 URLs with **HTTP 200**. Google-facing sitemaps, robots rules and ownership files were verified; Google does not consume IndexNow. This release does not claim that the search engines have completed indexing.

The knowledge map adds navigable research context. Reader engagement, search indexing and ranking remain outcomes to measure after release.

Implementation and operations: [reader value and connected research](../docs/editorial-knowledge.md).
