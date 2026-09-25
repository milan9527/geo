# Original case studies, URL repairs and reader attribution

Dated snapshot: **2026-09-20**. This English summary was rewritten from the historical report; later releases may supersede its state.

Two original engineering cases were reviewed and published: [Aurora Data API oversized responses](https://aperture.zhangwangshu.com/article/aurora-data-api-1mb-502-chunked-read) scored 95, and [permanent article URLs](https://aperture.zhangwangshu.com/article/preserve-article-urls-301-canonical-sitemap) scored 94. They passed 67 full-text comparisons against the final public catalogue; evidence-source counts were not treated as earned external citations.

Of nine additional legacy addresses, eight received relevant 301 mappings and one independent article was restored at its original URL. Three existing articles received reviewed additions to preserve useful material. All 32 existing URLs and first-publication timestamps remained intact; 29 bodies/source hashes were unchanged. The final catalogue contained 35 public articles and nineteen redirect mappings.

The admin console gained reader/channel attribution for anonymous sessions, engaged reads, return visits, RSS clicks and related-article clicks. Sessions renew after thirty minutes of inactivity; engaged reads require thirty foreground seconds and reaching half the article. Browser reporting is approximate; RSS clicks do not prove subscription. Diagnostic requests are excluded, and no production test events were used to manufacture growth.

Thirty-eight Python tests, browser checks and aggregator checks passed. All 35 articles passed public metadata checks. Nineteen mappings passed 152 route/parameter checks without payment settlement. API task definition 33, both frontends and the traffic Lambda were deployed. IndexNow accepted nineteen URL changes.

Four Chinese channel drafts were prepared, but no Juejin/Zhihu publication or automatic external posting was performed. Search-console performance data remained unavailable. The planned September 27 / October 4 reviews were not scheduled reminders. See the [distribution plan](../content/distribution/2026-09-20/README.md).

[Structured evidence](growth-launch-2026-09-20.json).

[Original reference in Git history](https://github.com/milan9527/geo/blob/a2de0e830f363177b0138409d93aebc512de4237/reports/growth-launch-2026-09-20.md) · [Report index](README.md)
