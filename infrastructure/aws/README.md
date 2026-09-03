# AWS Resource Inventory

Provisioned on 2026-09-03 in `us-east-1`, account `632930644527`.

## Resource identifiers

- Aurora cluster: `arn:aws:rds:us-east-1:632930644527:cluster:geo-intelligence-demo`
- Aurora writer: `geo-intelligence-demo-writer`
- Bedrock profile: `arn:aws:bedrock:us-east-1:632930644527:application-inference-profile/jiuyqf37o67n`
- AgentCore Runtime: `arn:aws:bedrock-agentcore:us-east-1:632930644527:runtime/geo_intelligence_agent-hyVRs073Db`
- AgentCore Browser: `geo_intelligence_browser-MmjFQMhTTf`
- AgentCore Code Interpreter: `geo_intelligence_code-7mOodJooC0`
- ECR repository: `632930644527.dkr.ecr.us-east-1.amazonaws.com/geo-intelligence-agent`
- IAM role: `arn:aws:iam::632930644527:role/geo-intelligence-agentcore-role`
- Scheduler group: `arn:aws:scheduler:us-east-1:632930644527:schedule-group/geo-intelligence-crawlers`
- Scheduler bridge: `arn:aws:lambda:us-east-1:632930644527:function:geo-intelligence-scheduler-bridge`
- Scheduler DLQ: `arn:aws:sqs:us-east-1:632930644527:geo-intelligence-scheduler-dlq`
- Scheduler role: `arn:aws:iam::632930644527:role/geo-intelligence-scheduler-role`
- Bridge role: `arn:aws:iam::632930644527:role/geo-intelligence-scheduler-bridge-role`

## Web application deployment

Application releases are deployed directly with AWS CLI service APIs. The deployment script does
not call CloudFormation, CDK, or SAM:

- Public CloudFront: `E57TFN7Z03O69` / `d1tsbnft7iv51.cloudfront.net`
- Admin CloudFront: `E1OMOLTZCN9KUQ` / `deu7vkdd3jf5.cloudfront.net`
- Shared S3 OAC: `E2WLLGTAL5PGBQ`
- Public bucket: `geo-intelligence-public-632930644527-us-east-1`
- Admin bucket: `geo-intelligence-admin-632930644527-us-east-1`
- ECS cluster/service: `geo-intelligence` / `geo-intelligence-api`
- ALB: `geo-intelligence-alb-136542997.us-east-1.elb.amazonaws.com`
- ECR repository: `632930644527.dkr.ecr.us-east-1.amazonaws.com/geo-intelligence-api`
- Active task definition: `geo-intelligence-api:8`

Both buckets block every form of public access and grant object reads only to their CloudFront
distribution through OAC. The ALB security group accepts port 80 only from the AWS-managed
CloudFront origin-facing prefix list, and its listener forwards requests only when the private
origin verification header matches. ECS tasks accept port 8000 only from the ALB security group.

The deployed backend image uses pinned Python 3.13 Alpine on ARM64, runs as UID `10001`, and its
ECR scan completed with zero findings. The ECS service has one desired/running task and uses
Aurora PostgreSQL 17.7 exclusively through the Data API.

The resources were initially created before the direct-API deployment workflow was adopted. The
legacy `geo-intelligence-web` stack is not used for releases or updates and remains only because
deleting an active stack would also delete its live resources. Do not update or delete that stack.
All application releases must use `scripts/deploy_web_ecs.sh`.

The Aurora password is AWS-managed in Secrets Manager. Do not export or copy the secret value into
project files. Runtime and local AWS mode use the AWS credential chain plus the resource and Secret
ARNs in the ignored `.env.aws` file.

Aurora Serverless v2 is configured for `0.5–2 ACU`. Because the minimum is greater than zero, the
writer remains available and does not enter Serverless v2 auto-pause.

## Runtime contract

The container is Linux ARM64, runs as UID `10001`, listens on port `8080`, and implements:

- `GET /ping`
- `POST /invocations`

The ECR image is deployed by immutable digest. The deployed image scan completed with no Critical,
High, or Medium findings.

The active Runtime is version 18 and uses image digest
`sha256:cceba82eea931c4857cb553dba3fa3a35e13ba0209287311f64e54b2361dfa19`.
It runs Codex SDK through the Amazon Bedrock provider, AgentCore Code Interpreter, AgentCore
Browser with Web Bot Auth, evidence remediation, and budgeted AgentCore Payments.

CloudFront forwards the standard `PAYMENT-SIGNATURE`, legacy `X-PAYMENT`, and `X-Agent-Name`
headers to ECS for x402 settlement and Agent traffic attribution. API cache TTL is zero.

## Policy files

- `agentcore-trust-policy.json`: AgentCore service trust
- `agentcore-runtime-policy.json`: Bedrock, Aurora Data API, Secrets Manager, AgentCore tools, ECR
  pull, CloudWatch Logs, X-Ray, and metrics permissions
- `scheduler-trust-policy.json`: EventBridge Scheduler service trust
- `scheduler-runtime-policy.json`: invoke the Lambda bridge and publish failed events to SQS
- `lambda-trust-policy.json`: Lambda service trust
- `scheduler-bridge-policy.json`: invoke the AgentCore DEFAULT endpoint and write Lambda logs

## Scheduler

EventBridge Scheduler cannot use `InvokeAgentRuntime` as a universal AWS SDK target. The deployed
flow therefore uses:

```text
Scheduler -> Lambda bridge -> AgentCore Runtime -> Aurora
```

The one-time preflight created Aurora job `36`, completed a real AgentCore Code Interpreter session,
deleted itself after completion, and left zero messages in the DLQ.

The Lambda bridge timeout is 900 seconds. Its asynchronous retry count is zero so a long-running
AgentCore invocation cannot be replayed and create duplicate crawls, model usage, or x402 payments.
