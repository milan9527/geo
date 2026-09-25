# Traffic review and historical 404s

Dated snapshot: **2026-09-14**. This English summary was rewritten from the historical report; later releases may supersede its state.

The inspection used a September 14 02:52 UTC database snapshot and 270 processed CloudFront log objects containing 4,852 requests. No authenticated Google/Bing search-console report was accessed.

Earlier peaks included substantial maintenance traffic: 572 of 746 content requests on September 9 and 236 of 298 on September 10 came from the inspection environment. These requests did not establish reader growth. For September 11–13, 42 requests matched official Google/Bing crawler IP ranges; ten returned 404. Thirteen historical article addresses pointed to retained `review` records and were no longer public under the earlier policy.

User-Agent classification and HLL visitor estimates are approximate; unknown scripts can appear human, and daily unique estimates cannot simply be summed. Referrer evidence did not demonstrate a stable external traffic source, but missing referrers do not prove zero organic clicks.

The log pipeline and queues were healthy. Public discovery files, home and a recent article returned 200 with valid metadata. The snapshot contained 22 public articles, all linked from home and included in the article sitemap. Automatic publication still produced approved content despite individual task failures and review rejections.

The recommended order was diagnostic filtering, reviewed legacy URL handling and useful original content informed by actual search performance. The [same-day repair](legacy-url-repair-2026-09-14.md) subsequently resolved all thirteen historical addresses. Counts here remain the earlier snapshot.

[Original reference in Git history](https://github.com/milan9527/geo/blob/a2de0e830f363177b0138409d93aebc512de4237/reports/traffic-review-2026-09-14.md) · [Report index](README.md)
