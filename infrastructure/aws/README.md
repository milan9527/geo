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
