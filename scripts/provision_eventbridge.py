#!/usr/bin/env python3
"""Create or update EventBridge Scheduler plans for GEO crawler agents."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone

import boto3
from botocore.exceptions import ClientError


REGION = "us-east-1"
GROUP_NAME = "geo-intelligence-crawlers"
RUNTIME_ARN = (
    "arn:aws:bedrock-agentcore:us-east-1:632930644527:"
    "runtime/geo_intelligence_agent-hyVRs073Db"
)
ROLE_ARN = "arn:aws:iam::632930644527:role/geo-intelligence-scheduler-role"
DLQ_ARN = "arn:aws:sqs:us-east-1:632930644527:geo-intelligence-scheduler-dlq"
TARGET_ARN = (
    "arn:aws:lambda:us-east-1:632930644527:"
    "function:geo-intelligence-scheduler-bridge"
)

SCHEDULES = [
    ("geo-research-coder", "research-coder", "cron(0/20 * * * ? *)", "ENABLED"),
    ("geo-render-scout", "render-scout", "cron(0/15 * * * ? *)", "ENABLED"),
    ("geo-market-signal", "market-signal", "cron(0 * * * ? *)", "ENABLED"),
    ("geo-evidence-verifier", "evidence-verifier", "cron(0/10 * * * ? *)", "ENABLED"),
    ("geo-cloud-release-watch", "cloud-release-watch", "cron(0 0/2 * * ? *)", "ENABLED"),
    ("geo-commerce-feed-miner", "commerce-feed-miner", "cron(0 0/12 * * ? *)", "ENABLED"),
]


def target(crawler_slug: str, marker: str = "eventbridge") -> dict:
    api_input = {
        "crawlerSlug": crawler_slug,
        "scheduledTime": marker,
        "allowPayment": crawler_slug == "commerce-feed-miner",
    }
    return {
        "Arn": TARGET_ARN,
        "RoleArn": ROLE_ARN,
        "Input": json.dumps(api_input, separators=(",", ":")),
        "DeadLetterConfig": {"Arn": DLQ_ARN},
        "RetryPolicy": {
            "MaximumEventAgeInSeconds": 300,
            "MaximumRetryAttempts": 2,
        },
    }


def upsert_schedule(
    scheduler,
    *,
    name: str,
    crawler_slug: str,
    expression: str,
    state: str,
    description: str,
    action_after_completion: str = "NONE",
) -> None:
    request = {
        "Name": name,
        "GroupName": GROUP_NAME,
        "Description": description,
        "ScheduleExpression": expression,
        "ScheduleExpressionTimezone": "UTC",
        "FlexibleTimeWindow": {"Mode": "OFF"},
        "Target": target(crawler_slug, name),
        "State": state,
        "ActionAfterCompletion": action_after_completion,
    }
    try:
        scheduler.get_schedule(Name=name, GroupName=GROUP_NAME)
    except scheduler.exceptions.ResourceNotFoundException:
        scheduler.create_schedule(**request)
        operation = "created"
    else:
        scheduler.update_schedule(**request)
        operation = "updated"
    print(f"{operation}: {name} {expression} {state}")


def provision_recurring(scheduler) -> None:
    for name, slug, expression, state in SCHEDULES:
        upsert_schedule(
            scheduler,
            name=name,
            crawler_slug=slug,
            expression=expression,
            state=state,
            description=f"Aperture GEO crawler schedule for {slug}",
        )


def provision_preflight(scheduler) -> None:
    run_at = datetime.now(timezone.utc) + timedelta(minutes=1)
    expression = f"at({run_at.strftime('%Y-%m-%dT%H:%M:%S')})"
    upsert_schedule(
        scheduler,
        name="geo-eventbridge-preflight",
        crawler_slug="research-coder",
        expression=expression,
        state="ENABLED",
        description="One-time EventBridge to AgentCore integration test",
        action_after_completion="DELETE",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="Create a one-time test one minute in the future instead of recurring schedules.",
    )
    args = parser.parse_args()
    scheduler = boto3.client("scheduler", region_name=REGION)
    if args.preflight:
        provision_preflight(scheduler)
    else:
        provision_recurring(scheduler)


if __name__ == "__main__":
    main()
