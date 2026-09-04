#!/usr/bin/env python3
from __future__ import annotations

import gzip
import json
import os
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
os.environ.setdefault(
    "TRAFFIC_HASH_SECRET_ARN",
    "arn:aws:secretsmanager:us-east-1:000000000000:secret:unit-test",
)
sys.path.insert(0, str(PROJECT_DIR))
sys.path.insert(0, str(PROJECT_DIR / "aws_traffic_aggregator"))

import lambda_function
from backend.analytics import empty_hll, hll_add, hll_count


def record(path: str, user_agent: str, ip: str, status: int = 200) -> dict:
    return {
        "date": "2026-09-04",
        "time": "12:34:56",
        "c-ip": ip,
        "cs-method": "GET",
        "cs-uri-stem": path,
        "sc-status": status,
        "cs(User-Agent)": user_agent,
        "sc-bytes": 2048,
    }


def main() -> None:
    registers = empty_hll()
    for index in range(1000):
        hll_add(registers, f"visitor-{index}".encode())
    estimate = hll_count(registers)
    assert 900 <= estimate <= 1100, estimate

    source = [
        record("/article/human-research", "Mozilla/5.0", "198.51.100.10"),
        record("/article/human-research", "Mozilla/5.0", "198.51.100.10"),
        record(
            "/article/agent-research",
            "Mozilla/5.0; compatible; OAI-SearchBot/1.0",
            "203.0.113.20",
        ),
        record(
            "/agent/v1/articles/paid-research/paid",
            "UnknownMachine/1.0",
            "203.0.113.30",
            402,
        ),
        record("/styles.css", "Mozilla/5.0", "198.51.100.10"),
    ]
    encoded = "\n".join(json.dumps(item) for item in source).encode()
    assert len(lambda_function.records_from_body(gzip.compress(encoded))) == 5
    lambda_function.hash_secret = lambda: b"unit-test-secret"
    aggregates = lambda_function.aggregate_records(source)
    assert len(aggregates) == 3

    human = next(
        value
        for key, value in aggregates.items()
        if key[1] == "human" and key[3] == "article"
    )
    assert human["requests"] == 2
    assert hll_count(human["hll"]) == 1

    search_agent_key = next(
        key for key in aggregates if key[2] == "OpenAI Search Crawler"
    )
    assert search_agent_key[1] == "agent"
    paid_key = next(key for key in aggregates if key[5] == "B")
    assert paid_key[1] == "agent"
    assert aggregates[paid_key]["successful"] == 0
    print("Traffic aggregator unit checks passed")


if __name__ == "__main__":
    main()
