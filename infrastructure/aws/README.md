# AWS environment and policies

This directory contains IAM policy examples and the resource inventory for the reference deployment in `us-east-1`, account `632930644527`. Replace account-specific ARNs, names and origins for another environment. The repository's release scripts update existing services; these files are not a complete infrastructure bootstrap.

See [deployment instructions](../../docs/deployment.md) and the [editable architecture](../../docs/architecture/aperture-aws.drawio).

## Resource inventory

| Layer | Reference resource |
| --- | --- |
| Public website | <https://aperture.zhangwangshu.com/> |
| Admin console | <https://deu7vkdd3jf5.cloudfront.net/> |
| Public CloudFront | `E57TFN7Z03O69` |
| Admin CloudFront | `E1OMOLTZCN9KUQ` |
| Shared S3 OAC | `E2WLLGTAL5PGBQ` |
| Public bucket | `geo-intelligence-public-632930644527-us-east-1` |
| Admin bucket | `geo-intelligence-admin-632930644527-us-east-1` |
| ECS cluster / service | `geo-intelligence` / `geo-intelligence-api` |
| ALB | `geo-intelligence-alb-136542997.us-east-1.elb.amazonaws.com` |
| API / runtime ECR repositories | `geo-intelligence-api` / `geo-intelligence-agent` |
| Aurora cluster / writer | `geo-intelligence-demo` / `geo-intelligence-demo-writer` |
| Bedrock application inference profile | `8b2k32fwobdd` |
| AgentCore Runtime | `geo_intelligence_agent-hyVRs073Db` |
| AgentCore Browser | `geo_intelligence_browser-MmjFQMhTTf` |
| AgentCore Code Interpreter | `geo_intelligence_code-7mOodJooC0` |
| Scheduler group | `geo-intelligence-crawlers` |
| Scheduler bridge Lambda | `geo-intelligence-scheduler-bridge` |
| Scheduler SQS DLQ | `geo-intelligence-scheduler-dlq` |
| Indexing Lambda | `geo-intelligence-indexing-notifier` |
| Traffic Lambda | `geo-intelligence-traffic-aggregator` |

## Access and persistence

Both frontend buckets block public access and allow reads through CloudFront OAC. The ALB accepts origin traffic from the CloudFront origin-facing prefix list and checks a private origin header. ECS accepts port 8000 from the ALB security group.

Aurora PostgreSQL Serverless v2 is accessed through the Data API. The reference cluster has a 0.5–2 ACU range; its nonzero minimum prevents automatic pause. The database password stays in Secrets Manager. Configure resource and secret ARNs in ignored `.env.aws`; do not copy the secret value into the repository.

CloudFront routes server-rendered pages and APIs to ECS and static frontend assets to S3. API responses are uncached. x402 request and agent-attribution headers must reach the backend. Publication notifications invalidate affected English and Chinese content and discovery files. Edge logs flow through S3 and SQS to the traffic aggregator; raw-log S3 lifecycle retention is separate from database analytics cleanup.

## Releases and dated verification

Use `scripts/deploy_web_ecs.sh` for web releases and `scripts/deploy_agent_runtime.sh` for runtime releases. They build ARM64 containers, scan images and update services through AWS APIs. Runtime images are deployed by immutable digest. The runtime runs as UID `10001`, listens on port 8080 and exposes `GET /ping` and `POST /invocations`.

The **2026-09-25 verification snapshot** recorded API task definition `geo-intelligence-api:38`, AgentCore Runtime version `50` in READY state, automatic publication enabled and six enabled schedules. Both image scans completed with no High or Critical findings. These are dated observations, not permanent configuration values; release evidence is in the [functional coverage report](../../reports/functional-coverage-2026-09-25.md).

The legacy `geo-intelligence-web` CloudFormation stack still owns live resources but is no longer used for releases. **Do not update or delete that stack**: deletion can remove active infrastructure. Use the documented direct-API release workflow.

## Scheduler behavior

```text
EventBridge Scheduler -> Lambda bridge -> asynchronous AgentCore task -> Aurora
```

The bridge has a 60-second timeout and waits for runtime acceptance. Research continues in AgentCore after Lambda returns. Lambda asynchronous retries are disabled to avoid duplicate crawls, model usage and payments. Scheduler delivery retries and its DLQ are configured independently.

`provision_eventbridge.py` upserts schedule definitions and contains deployment-specific ARNs. Review and adapt it before provisioning; do not use it as a routine release command that overwrites operator-edited schedules.

## IAM files

| File | Purpose |
| --- | --- |
| `agentcore-trust-policy.json` | AgentCore service trust |
| `agentcore-runtime-policy.json` | Inference, Data API, secrets, tools, image pull and telemetry |
| `scheduler-trust-policy.json` | Scheduler service trust |
| `scheduler-runtime-policy.json` | Invoke the bridge and deliver failed events to SQS |
| `lambda-trust-policy.json` | Lambda service trust |
| `scheduler-bridge-policy.json` | Invoke the runtime DEFAULT endpoint and write logs |
| `ecs-api-runtime-policy.json` | API resources, schedule updates and constrained PassRole |

Grant only the resources needed by the target environment. Store credentials in AWS-managed secrets or the local credential chain, not policy examples or frontend assets.
