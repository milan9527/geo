# Installation and deployment

## Local development

Use Python 3.10 or newer (3.13 recommended), Docker with the Compose plugin, and a free local PostgreSQL port at `127.0.0.1:55432`. Node.js 24+ is required to build the crawler worker, not to serve the static frontend.

```bash
git clone https://github.com/milan9527/geo.git
cd geo
./scripts/start.sh
```

Run local development from a shell without production database variables. `scripts/start.sh` creates `.venv`, installs Python dependencies, starts the Compose PostgreSQL service, initializes the schema and development seed data, and launches:

| Service | Address |
| --- | --- |
| Public website | `http://127.0.0.1:4173` |
| Admin console | `http://127.0.0.1:4174` |
| API health | `http://127.0.0.1:8000/api/health` |
| PostgreSQL | `127.0.0.1:55432` |

The default database URL is the development-only value in `.env.example`. Custom values must be exported in the shell; `start.sh` does not automatically load `.env`.

In another terminal:

```bash
PYTHONPATH=. .venv/bin/python scripts/create_admin_user.py \
  --username admin --display-name "Aperture Administrator"
```

Enter a password of at least 12 characters. Reusing a username rotates its password and invalidates its sessions. The optional `--generate` flag prints a generated password once.

The interface defaults to English. Development seed articles may retain Chinese source text until reviewed English editions are created. Translation backfill requires a configured inference provider; it is not performed automatically by the local startup script.

To build the crawler worker:

```bash
cd agent_runtime
npm ci
npm run build
```

Use `Ctrl+C` to stop the application. Run `./scripts/stop.sh` from the repository root to stop the local PostgreSQL service.

## Configuration files

| File | Use |
| --- | --- |
| `.env.example` | Local database, session options, and optional payment/runtime variables |
| `.env.aws.example` | Template for an existing Aurora and AgentCore environment |
| `.env.deploy.aws.example` | Template for Web deployment, CloudFront, and seller settings |
| `.env.aws`, `.env.deploy.aws` | Ignored environment-specific files used by deployment scripts |

AWS access uses the standard credential chain and workload roles. Database and source secret values remain in Secrets Manager; the configuration files contain resource identifiers, not secret contents. Configure the real public seller wallet address before deploying paid endpoints.

## AWS prerequisites

The repository contains release scripts and selected service-provisioning helpers. It does **not** provide a complete, parameterized bootstrap for an empty AWS account.

Before the first release, create or supply:

1. An Aurora PostgreSQL cluster with Data API enabled, its managed Secret ARN, and database access policies.
2. Private public/admin S3 buckets, CloudFront distributions with OAC, and an ALB-backed ECS Fargate API service. Set up DNS and certificates for custom domains.
3. API/runtime ECR repositories and execution/task roles. The API image and runtime use Linux ARM64.
4. Bedrock inference access and an AgentCore Runtime with Browser/Code Interpreter resources as needed.
5. A scheduler Lambda bridge, IAM roles, a Scheduler group, and its SQS dead-letter queue.

The release workstation needs AWS CLI v2, Docker with ARM64 build support, `jq`, `zip`, and the repository's Python environment. Configure `BEDROCK_MODEL_ID` with a model or inference profile available to your account; the empty template value must be filled before runtime deployment.

Review `infrastructure/aws/*.json` and `scripts/provision_eventbridge.py` before using them in another account: several sample policies, resource ARNs, and schedule defaults are account-specific.

Copy and edit the environment templates:

```bash
cp .env.aws.example .env.aws
cp .env.deploy.aws.example .env.deploy.aws
```

The ALB should accept only the intended CloudFront-origin traffic and private origin verification header. ECS should accept API traffic only from the ALB. Keep both S3 buckets private.

## Release the application

```bash
set -a
source .env.aws
source .env.deploy.aws
set +a
./scripts/deploy_web_ecs.sh
./scripts/deploy_agent_runtime.sh --auto-publish
./scripts/deploy_scheduler_bridge.sh
```

The Web script loads both environment files, builds and pushes the API image, requires a completed ECR scan without High/Critical findings, registers a task definition, waits for ECS stability, updates public routing, uploads frontend assets, and requests CloudFront invalidation. `--public-only` updates the API and public frontend; `--api-only` updates only the API.

Runtime deployment scans its image and deploys it by digest. Without a publication flag, it preserves the current auto-publication setting. `--auto-publish` enables reviewed publication; `--no-auto-publish` disables it. Neither bypasses evidence, deduplication, or bilingual checks.

The Web script loads both environment files; the runtime script loads `.env.aws`; the bridge script uses already exported variables. Exporting both files above also makes custom runtime repository and bridge names available. The bridge deployment updates an existing Lambda function. Scheduler delivery has its own retry/DLQ policy, while Lambda asynchronous retries are disabled to reduce duplicate crawls and payment attempts.

For the sample environment, after reviewing resource identifiers:

```bash
set -a
source .env.aws
source .env.deploy.aws
set +a
.venv/bin/python scripts/provision_eventbridge.py
./scripts/provision_search_indexing.sh
./scripts/provision_traffic_analytics.sh
```

These commands change schedules and provision/update notification or log-processing resources. Do not rerun schedule provisioning as a routine release step: it applies the script's default schedules.

To run the local Web processes against the configured AWS database, use `./scripts/start_aws.sh`. This is connected to the cloud environment and is not an isolated test sandbox.

## Bilingual rollout and permanent URLs

For a new or imported catalogue, follow [bilingual publication](bilingual-publication.md). The translation tool can backfill reviewed English editions and enable the database bilingual policy once the complete published catalogue passes. Do not disable the policy or bypass review to publish a failed translation.

The public domain, published slugs, and established redirects are persistent interfaces. Release scripts update existing distributions rather than creating replacement public URLs. Retain ownership files, redirect mappings, and both language sitemap entries.

## Verify and roll back

- Check `/api/health`, the public homepage, a published article, and `/zh/`.
- Log in to the English admin console, then switch languages and verify the session remains active.
- Run `scripts/check_search_indexing.py` for sitemap, crawler, canonical, and bilingual-link checks. Add `--submit-indexnow` only when a notification is intended.
- Run regression suites against a disposable PostgreSQL database as described in [functional verification](functional-verification.md). Do not run the mutating smoke test against production.
- Check the actual runtime publication setting, enabled schedules, bilingual database policy, and image scan results. Settings-page preferences do not replace these controls.

For a Web rollback, restore a known compatible ECS task definition and matching static assets, then invalidate affected CloudFront paths. Restore a known runtime image for a runtime rollback. Preserve published rows, slugs, reviewed editions, and redirects; a code rollback is not a reason to delete content.
