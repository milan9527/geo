# Admin research loading: Data API response limit

Dated snapshot: **2026-09-14**. This English summary was rewritten from the historical report; later releases may supersede its state.

Login succeeded, but `/api/admin/research` returned 502 because its query exceeded the Aurora Data API 1 MB result limit. The original 20-row query encoded 1,094,895 bytes; limiting row count did not bound the large review fields.

Research lists, details and evidence now read JSON in 8,192-character chunks, at most eight chunks per request, and reconstruct complete values. A read-only repeatable-read snapshot keeps related reads consistent; timestamp and ID ordering is deterministic.

Four PostgreSQL regressions covered oversized results, large individual records, Unicode reconstruction, empty/404 responses and concurrent reads. Live verification returned all 20 research records and 1,337,245 response bytes. All ten admin initialization requests returned 200, and seven views, session restoration and logout passed browser checks. The 24 published articles and 11 redirects were preserved; temporary test credentials were removed.

API task definition 32, image `20260914130915-2d5967f`, was deployed with a completed scan and no reported findings. See the [earlier metrics incident](admin-login-502-2026-09-13.md) for the related failure mode.

[Structured evidence](admin-data-load-2026-09-14.json).

[Original reference in Git history](https://github.com/milan9527/geo/blob/a2de0e830f363177b0138409d93aebc512de4237/reports/admin-data-load-2026-09-14.md) · [Report index](README.md)
