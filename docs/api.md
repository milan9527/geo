# API reference

The public origin is `https://aperture.zhangwangshu.com`. Local frontends proxy the same paths to the Python API. JSON endpoints default to English; append `lang=zh` for Chinese. HTML uses `/zh/` rather than a query parameter.

## Public content and discovery

| Method | Path | Result |
| --- | --- | --- |
| GET | `/api/health` | API/database health |
| GET | `/api/v1/site` | Site metadata |
| GET | `/api/v1/categories` | Categories and published article counts |
| GET | `/api/v1/articles` | Published article summaries |
| GET | `/api/v1/articles/{slug}` | Article, sections, and sources |
| GET | `/api/v1/search?q={term}` | Matching published articles |
| GET | `/article/{slug}`, `/category/{slug}` | Complete English HTML |
| GET | `/zh/article/{slug}`, `/zh/category/{slug}` | Complete Chinese HTML |
| GET | `/sitemap.xml`, `/sitemap-articles.xml` | Canonical URLs in both languages |
| GET | `/feed.xml`, `/zh/feed.xml` | English and Chinese RSS |
| GET | `/robots.txt`, `/llms.txt` | Crawler policy and machine discovery |

Article lists accept `category`, `featured=true`, `limit` (1–100; default 30), and `offset` (default 0). Responses remain JSON arrays. Invalid pagination values return 400. Category HTML includes the complete published category, independently of the API's default page size.

```bash
curl 'https://aperture.zhangwangshu.com/api/v1/articles?category=agent&limit=10&offset=0&lang=en'
curl 'https://aperture.zhangwangshu.com/api/v1/search?q=asynchronous&lang=en'
```

Established legacy article URLs return 301 to their canonical destination, retaining language and query parameters. Unknown public URLs return a real 404. Drafts and review candidates are not public article endpoints.

## Agent representations

| Method | Path | Access |
| --- | --- | --- |
| GET | `/agent/v1/articles/{slug}?lang=en` | Open variant A |
| GET | `/agent/v1/articles/{slug}/paid?lang=en` | x402-protected variant B |

Both representations include claims, sections, citations, language, license information, and links to the selected language's variants. The demonstration retains public access to the underlying research.

An unpaid B request receives HTTP 402 and `PAYMENT-REQUIRED`. A compatible, authorized buyer signs the payment and retries with `PAYMENT-SIGNATURE`. The seller returns 200 and `PAYMENT-RESPONSE` only after successful settlement. Failed verification or settlement does not return paid content. Paid responses are not search-indexed or publicly cached.

The configured demonstration uses Base Sepolia (`eip155:84532`) USDC, with a default price of 0.002 USDC. Reading a challenge is not a purchase; ordinary crawlers do not automatically pay.

## Admin sessions and operations

Use the separate admin origin for browser sessions. `POST /api/admin/auth/login` accepts a JSON username/password and establishes an HttpOnly, SameSite session cookie. Production cookies use Secure. `GET /api/admin/auth/me` reads the session; `POST /api/admin/auth/logout` invalidates it.

| Area | Endpoints |
| --- | --- |
| Content | GET/POST `/api/admin/articles`; GET/PATCH `/api/admin/articles/{id}`; PATCH `/api/admin/articles/batch` |
| Research | GET `/api/admin/research`, `/api/admin/research/{id}` |
| Sources | GET/POST `/api/admin/data-sources`; PATCH/DELETE `/api/admin/data-sources/{id}` |
| Source tests | POST `/api/admin/data-sources/{id}/test`, `/api/admin/data-sources/test-all`; GET `/api/admin/data-sources/test-batch` |
| Crawlers | GET `/api/admin/crawlers`; PATCH `/api/admin/crawlers/{id}`; POST `/api/admin/crawlers/{id}/run`, `/api/admin/crawlers/run-all` |
| Operations | GET `/api/admin/jobs`, `/api/admin/events`, `/api/admin/metrics` |
| Preferences | GET `/api/admin/settings`; PATCH `/api/admin/settings/{key}` |

New content can be created as draft or review. Manual publish shortcuts are rejected; reviewed automatic publication owns that transition. Published content cannot be deleted, withdrawn, or renamed. Source credential input is written to Secrets Manager, not returned as plaintext. Editable source fields and original research records retain their source language.

Metrics support named ranges or `start`/`end` dates. Settings switches currently persist preferences only; they do not control the runtime, alerts, or automatic data cleanup. See [functional verification](functional-verification.md).
