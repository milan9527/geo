from __future__ import annotations

import gzip
import hashlib
import hmac
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any
from urllib.parse import unquote, unquote_plus

import boto3

try:
    from analytics_shared import (
        decode_hll,
        empty_hll,
        encode_hll,
        hll_add,
        hll_merge,
        identify_visitor,
    )
    from data_api import DataApiConnection
except ImportError:
    from backend.analytics import (
        decode_hll,
        empty_hll,
        encode_hll,
        hll_add,
        hll_merge,
        identify_visitor,
    )
    from backend.data_api import DataApiConnection


REGION = os.environ.get("AWS_REGION", "us-east-1")
HASH_SECRET_ARN = os.environ["TRAFFIC_HASH_SECRET_ARN"]
s3 = boto3.client("s3", region_name=REGION)
secrets = boto3.client("secretsmanager", region_name=REGION)
_hash_secret: bytes | None = None


def hash_secret() -> bytes:
    global _hash_secret
    if _hash_secret is None:
        response = secrets.get_secret_value(SecretId=HASH_SECRET_ARN)
        value = response.get("SecretString")
        if value is None:
            value = response["SecretBinary"]
        if isinstance(value, str):
            value = value.encode("utf-8")
        _hash_secret = bytes(value)
    return _hash_secret


def field(record: dict[str, Any], *names: str, default: object = "") -> object:
    for name in names:
        if name in record and record[name] is not None:
            return record[name]
    return default


def records_from_body(body: bytes) -> list[dict[str, Any]]:
    if body[:2] == b"\x1f\x8b":
        body = gzip.decompress(body)
    text = body.decode("utf-8-sig").strip()
    if not text:
        return []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        records = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            if isinstance(item, dict):
                records.append(item)
        return records
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        nested = payload.get("records") or payload.get("Records")
        if isinstance(nested, list):
            return [item for item in nested if isinstance(item, dict)]
        return [payload]
    return []


def path_dimensions(path: str) -> tuple[str, str, str] | None:
    clean_path = unquote(path.split("?", 1)[0])
    if clean_path == "/":
        return "home", "", ""
    if clean_path == "/methodology":
        return "methodology", "", ""
    if clean_path in {
        "/robots.txt",
        "/sitemap.xml",
        "/sitemap-articles.xml",
        "/feed.xml",
        "/llms.txt",
    }:
        return "discovery", "", ""
    for prefix, group in (
        ("/article/", "article"),
        ("/category/", "category"),
    ):
        if clean_path.startswith(prefix):
            slug = clean_path[len(prefix) :].strip("/")[:240]
            return group, slug if group == "article" else "", ""
    if clean_path.startswith("/api/v1/articles/"):
        slug = clean_path[len("/api/v1/articles/") :].strip("/")[:240]
        return "article", slug, ""
    if clean_path.startswith("/agent/v1/articles/"):
        remainder = clean_path[len("/agent/v1/articles/") :].strip("/")
        paid = remainder.endswith("/paid")
        slug = remainder.removesuffix("/paid").strip("/")[:240]
        return "agent_api", slug, "B" if paid else "A"
    return None


def bucket_hour(record: dict[str, Any]) -> str | None:
    date_value = str(field(record, "date", default=""))
    time_value = str(field(record, "time", default=""))
    try:
        occurred = datetime.fromisoformat(
            f"{date_value}T{time_value.replace('Z', '')}+00:00"
        )
    except ValueError:
        raw_timestamp = field(record, "timestamp", "timestamp(ms)", default="")
        try:
            numeric = float(str(raw_timestamp))
            if numeric > 10_000_000_000:
                numeric /= 1000
            occurred = datetime.fromtimestamp(numeric, tz=timezone.utc)
        except (ValueError, OSError):
            return None
    return occurred.astimezone(timezone.utc).replace(
        minute=0, second=0, microsecond=0
    ).isoformat()


def integer_field(record: dict[str, Any], *names: str) -> int:
    try:
        return max(0, int(float(str(field(record, *names, default=0)))))
    except ValueError:
        return 0


def aggregate_records(records: list[dict[str, Any]]) -> dict[tuple[str, ...], dict]:
    aggregates: dict[tuple[str, ...], dict] = defaultdict(
        lambda: {
            "requests": 0,
            "successful": 0,
            "bytes": 0,
            "hll": empty_hll(),
        }
    )
    secret = hash_secret()
    for record in records:
        method = str(field(record, "cs-method", "method", default="")).upper()
        if method != "GET":
            continue
        path = str(field(record, "cs-uri-stem", "uri", "path", default=""))
        dimensions = path_dimensions(path)
        hour = bucket_hour(record)
        if not dimensions or not hour:
            continue
        path_group, article_slug, access_variant = dimensions
        user_agent = str(
            field(record, "cs(User-Agent)", "userAgent", "user_agent", default="")
        )
        visitor_type, agent_name = identify_visitor(user_agent)
        if path_group == "agent_api":
            visitor_type = "agent"
            agent_name = agent_name or "Machine client"
        agent_name = (agent_name or "")[:120]
        key = (
            hour,
            visitor_type,
            agent_name,
            path_group,
            article_slug,
            access_variant,
        )
        aggregate = aggregates[key]
        status = integer_field(record, "sc-status", "status")
        aggregate["requests"] += 1
        aggregate["successful"] += int(200 <= status < 400)
        aggregate["bytes"] += integer_field(record, "sc-bytes", "bytes")
        client_ip = str(field(record, "c-ip", "clientIp", default=""))
        visitor_digest = hmac.new(
            secret,
            f"{client_ip}\0{user_agent}".encode("utf-8", "replace"),
            hashlib.sha256,
        ).digest()
        hll_add(aggregate["hll"], visitor_digest)
    return dict(aggregates)


def process_object(bucket: str, key: str, etag: str) -> dict[str, Any]:
    response = s3.get_object(Bucket=bucket, Key=key)
    records = records_from_body(response["Body"].read())
    aggregates = aggregate_records(records)
    timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    conn = DataApiConnection()
    try:
        marker = conn.execute(
            """
            INSERT INTO traffic_log_objects(
                object_key, bucket_name, etag, record_count, processed_at
            ) VALUES(%s, %s, %s, 0, %s)
            ON CONFLICT(object_key) DO NOTHING
            RETURNING object_key
            """,
            (key, bucket, etag, timestamp),
        ).fetchone()
        if not marker:
            conn.rollback()
            return {"key": key, "duplicate": True, "records": 0}
        for dimensions, aggregate in aggregates.items():
            existing = conn.execute(
                """
                SELECT visitor_hll FROM traffic_hourly
                WHERE bucket_hour = %s AND visitor_type = %s
                  AND agent_name = %s AND path_group = %s
                  AND article_slug = %s AND access_variant = %s
                """,
                dimensions,
            ).fetchone()
            if existing:
                hll_merge(
                    aggregate["hll"],
                    [decode_hll(existing["visitor_hll"])],
                )
            conn.execute(
                """
                INSERT INTO traffic_hourly(
                    bucket_hour, visitor_type, agent_name, path_group,
                    article_slug, access_variant, requests,
                    successful_requests, bytes_sent, visitor_hll, updated_at
                ) VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(
                    bucket_hour, visitor_type, agent_name, path_group,
                    article_slug, access_variant
                ) DO UPDATE SET
                    requests = traffic_hourly.requests + EXCLUDED.requests,
                    successful_requests =
                        traffic_hourly.successful_requests
                        + EXCLUDED.successful_requests,
                    bytes_sent =
                        traffic_hourly.bytes_sent + EXCLUDED.bytes_sent,
                    visitor_hll = EXCLUDED.visitor_hll,
                    updated_at = EXCLUDED.updated_at
                """,
                (
                    *dimensions,
                    aggregate["requests"],
                    aggregate["successful"],
                    aggregate["bytes"],
                    encode_hll(aggregate["hll"]),
                    timestamp,
                ),
            )
        conn.execute(
            """
            UPDATE traffic_log_objects
            SET record_count = %s, processed_at = %s
            WHERE object_key = %s
            """,
            (len(records), timestamp, key),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {
        "key": key,
        "duplicate": False,
        "records": len(records),
        "aggregates": len(aggregates),
    }


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    results = []
    for message in event.get("Records", []):
        body = message.get("body")
        envelope = json.loads(body) if isinstance(body, str) else message
        for record in envelope.get("Records", []):
            if not str(record.get("eventName", "")).startswith("ObjectCreated:"):
                continue
            bucket = record["s3"]["bucket"]["name"]
            key = unquote_plus(record["s3"]["object"]["key"])
            etag = str(record["s3"]["object"].get("eTag") or "")
            results.append(process_object(bucket, key, etag))
    print(json.dumps({"requestId": getattr(context, "aws_request_id", None), "results": results}))
    return {"processed": len(results), "results": results}
