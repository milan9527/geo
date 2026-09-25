# Legacy article URL repair

Dated snapshot: **2026-09-14**. This English summary was rewritten from the historical report; later releases may supersede its state.

Thirteen confirmed historical article addresses were repaired at 03:23:25 UTC: eleven received specific permanent redirects and two were restored as reviewed articles at their original URLs. All 22 existing public URLs and original publication timestamps were retained. Three articles received reviewed additions; the catalogue increased to 24 without deleting manuscripts.

Five revised articles scored 92–95 and passed 105 full-text comparisons against the final catalogue. Redirects also passed content-coverage checks with at least 0.90 confidence. The write transaction locked articles, sources and mappings and rechecked fingerprints and catalogue state before committing.

HTML, article JSON and Agent routes share the persisted mappings. Unrelated unknown URLs still return 404. Published pages cannot be deleted, withdrawn or renamed; redirected source records also cannot be deleted, renamed or republished by scheduled tasks.

Forty-nine local regressions passed. All eleven legacy GET/HEAD routes returned 301, and all 24 public articles returned 200 with one H1 and valid canonical metadata. Browser checks covered JavaScript on/off, internal navigation and history. API task definition 31 and runtime version 48 were deployed, with six schedules and automatic publication enabled.

IndexNow accepted 26 article/category URL updates. No Search Console request was submitted. Mapping targets and review/deployment details are in the accompanying JSON evidence.

[Structured evidence](legacy-url-repair-2026-09-14.json).

[Original reference in Git history](https://github.com/milan9527/geo/blob/a2de0e830f363177b0138409d93aebc512de4237/reports/legacy-url-repair-2026-09-14.md) · [Report index](README.md)
