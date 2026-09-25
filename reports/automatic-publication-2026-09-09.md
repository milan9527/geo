# Scheduled review and automatic publication

Dated snapshot: **2026-09-09**. This English summary was rewritten from the historical report; later releases may supersede its state.

Runtime version 47 was READY with `RESEARCH_AUTO_PUBLISH=true`, six enabled schedules, a quality threshold of 90 and deduplication policy `2026-09-09-v2-core-coverage`.

The pipeline collected evidence, generated and independently reviewed research, then compared the complete candidate against every published article across categories and dates. It required sufficient body content, citations, archived evidence, at least five source URLs and three source organizations. Duplicate, uncertain and failed comparisons retained the draft under review. The final transaction rechecked content, sources and the public catalogue before publishing and notifying the indexing worker.

Ten batch-deduplication checks, thirteen publication/database checks and five runtime-flow checks passed. A production Scheduler invocation completed task 1185 / research 935: the draft scored 98 but duplicated article 206 after all fifteen public-article comparisons, so it was not published. Two associated drafts also scored 98 and remained under review as duplicates.

Deployments preserve automatic-publication configuration unless an explicit flag changes it. The later [bilingual launch](bilingual-launch-2026-09-25.md) added mandatory reviewed English editions.

[Structured evidence](automatic-publication-2026-09-09.json).

[Original reference in Git history](https://github.com/milan9527/geo/blob/a2de0e830f363177b0138409d93aebc512de4237/reports/automatic-publication-2026-09-09.md) · [Report index](README.md)
