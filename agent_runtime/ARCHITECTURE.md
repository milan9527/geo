# Research runtime

Aperture runs scheduled research in Amazon Bedrock AgentCore Runtime. This document describes the implemented service boundaries; the [system architecture](../docs/architecture.md) covers delivery, search and analytics.

![Research and publication gates](../docs/architecture/publication-pipeline.png)

## Invocation and source selection

EventBridge Scheduler invokes the Lambda bridge, which submits an asynchronous AgentCore invocation. The bridge waits for acceptance, not completion of the research task. Aurora stores job progress, evidence, research output and verification results.

Six enabled schedules were verified on 2026-09-25. Scheduler delivery has its own retry policy and SQS dead-letter queue. Lambda asynchronous retries are disabled to avoid replaying research or payments; these are separate retry mechanisms.

Each invocation loads source profiles and assignments from Aurora `data_sources` and `agent_source_assignments`. Selection timestamps rotate eligible open sources across runs. The IAM-authenticated `source_registry` action provides a read-only inspection path. Configuration and source counts can change independently of an image release.

## Tool boundaries

| Component | Responsibility | Implementation |
| --- | --- | --- |
| Python orchestrator | Job lifecycle, source selection, evidence collection, analysis and review | `aws_runtime/app.py` |
| Codex SDK worker | Generate and repair source-specific crawler code in an isolated workspace | `agent_runtime/codex_crawler.ts`, `codex_cli.ts` |
| Code Interpreter | Execute validated crawler code and parse retrieved material | `aws_runtime/crawler_tools.py` |
| Browser | Retrieve interactive or JavaScript-rendered sources; use configured Web Bot Auth | `aws_runtime/crawler_tools.py`, `agent_runtime/browser_cli.ts` |
| Bedrock inference | Generate research and independently review evidence, duplicates and English editions | `aws_runtime/publication.py`, backend translation modules |
| Publisher | Recheck reviewed content and commit an eligible article with its English edition | `aws_runtime/publication.py` |

The Codex worker uses the Amazon Bedrock provider; `BEDROCK_CODEX_MODEL` selects the model. It produces code before network retrieval is executed by the runtime tools. Generated Python passes AST checks, and crawler request wrappers apply source policies such as allowed domains, request pacing and bounded retries. Tool sessions, generated artifacts and usage are recorded for diagnosis. These controls do not imply arbitrary generated code is inherently safe.

Evidence is stored with research records and source references in Aurora. The implementation does not require a separate entity graph or vector database. Model-generated conclusions remain subject to evidence and editorial review.

## Publication contract

1. Generate a Chinese source article from saved evidence; revise when required and independently audit it.
2. Require sufficient source diversity, archived evidence, valid citations, complete content and the configured quality score. Unsupported facts, causal claims and placeholders block approval.
3. Compare the complete candidate with every published article, across categories and dates. Shared core content or containment can be duplicate content even when titles differ. Materially new evidence or analysis can justify a separate article.
4. Generate the complete English edition and independently review its accuracy and completeness. Missing, failed or stale translations block publication.
5. In the final database transaction, recheck article/source fingerprints and the published catalogue. Concurrent changes invalidate the decision rather than silently publishing stale work.
6. Commit eligible content and notify the indexing worker. Otherwise retain the draft under review with its audit record.

Model uncertainty and failures do not qualify for automatic publication. Semantic review reduces duplicate publication but cannot prove mathematical uniqueness. Existing published URLs remain protected even if a later review finds a problem; corrections stay at the same URL.

`RESEARCH_AUTO_PUBLISH` controls scheduled publication. Runtime deployment preserves its current value unless `--auto-publish` or `--no-auto-publish` is supplied. The database independently enforces bilingual publication when `bilingual_policy.required` is enabled. Admin preference switches do not replace these controls.

See [bilingual publication](../docs/bilingual-publication.md) for revision hashes and backfills, and [functional verification](../docs/functional-verification.md) for isolated regression tests.

## Payment boundaries

The optional research buyer in `aws_runtime/crawler_tools.py` uses budgeted AgentCore Payments to obtain a supported paid resource. It checks the network, token and quoted price before spending.

The website seller is a different component: `backend/x402_payment.py` verifies and settles a signed request through an **external facilitator**, then returns the paid response. The demonstration uses Base Sepolia testnet USDC, with a default price of 0.002 USDC. Public research and the open JSON route remain available. Mainnet revenue, refunds and financial reconciliation are not implemented as production services.

## Build and release

```bash
cd agent_runtime
npm ci
npm run build
cd ..
set -a
source .env.aws
source .env.deploy.aws
set +a
./scripts/deploy_agent_runtime.sh --auto-publish
./scripts/deploy_scheduler_bridge.sh
```

Build requires Node.js 24+. Deployment requires an existing configured AWS environment and publishes a scanned ARM64 image by immutable digest. See the [deployment guide](../docs/deployment.md) before running release or provisioning scripts.
