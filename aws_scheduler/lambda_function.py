from __future__ import annotations

import json
import os
from typing import Any

import boto3


REGION = os.environ.get("AWS_REGION", "us-east-1")
RUNTIME_ARN = os.environ["AGENTCORE_RUNTIME_ARN"]
agentcore = boto3.client("bedrock-agentcore", region_name=REGION)


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    crawler_slug = str(event.get("crawlerSlug", "")).strip()
    if not crawler_slug:
        raise ValueError("crawlerSlug is required")
    payload = {
        "action": "scheduled_crawl",
        "crawlerSlug": crawler_slug,
        "scheduledTime": event.get("scheduledTime", "eventbridge"),
        "scheduleName": event.get("scheduleName"),
        "requestId": getattr(context, "aws_request_id", None),
    }
    response = agentcore.invoke_agent_runtime(
        agentRuntimeArn=RUNTIME_ARN,
        qualifier="DEFAULT",
        contentType="application/json",
        accept="application/json",
        payload=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    )
    raw = response["response"].read()
    body = json.loads(raw.decode("utf-8")) if raw else {}
    if response["statusCode"] >= 300:
        raise RuntimeError(f"AgentCore returned {response['statusCode']}: {body}")
    return {
        "statusCode": response["statusCode"],
        "runtimeSessionId": response.get("runtimeSessionId"),
        "crawler": crawler_slug,
        "result": body,
    }
