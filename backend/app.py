from __future__ import annotations

import json
import ipaddress
import os
import re
import secrets
import socket
import time
from http.cookies import SimpleCookie
from datetime import date, datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

import boto3

from .auth import (
    generate_session_token,
    hash_password,
    session_token_hash,
    verify_password,
)
from .database import USE_AURORA_DATA_API, connection, init_db, utc_now
from .x402_payment import (
    X402_NETWORK,
    X402_PAY_TO_ADDRESS,
    X402ConfigurationError,
    error_instructions,
    paid_price,
    process_paid_request,
    settle_paid_request,
)


ADMIN_KEY = os.environ.get("GEO_ADMIN_KEY", "geo-admin-demo")
ALLOW_ADMIN_KEY = os.environ.get(
    "GEO_ALLOW_ADMIN_KEY", "false"
).lower() in {"1", "true", "yes"}
ADMIN_COOKIE_NAME = "aperture_admin_session"
ADMIN_COOKIE_SECURE = os.environ.get(
    "GEO_ADMIN_COOKIE_SECURE", "false"
).lower() in {"1", "true", "yes"}
ADMIN_SESSION_HOURS = int(os.environ.get("GEO_ADMIN_SESSION_HOURS", "12"))
ADMIN_MAX_FAILED_ATTEMPTS = 5
ADMIN_LOCK_MINUTES = 15
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
SCHEDULER_GROUP = "geo-intelligence-crawlers"
SCHEDULER_BRIDGE_FUNCTION = "geo-intelligence-scheduler-bridge"
CRAWLER_SCHEDULE_PRESETS = {
    "*/10 * * * *": ("cron(0/10 * * * ? *)", "每 10 分钟"),
    "*/15 * * * *": ("cron(0/15 * * * ? *)", "每 15 分钟"),
    "*/20 * * * *": ("cron(0/20 * * * ? *)", "每 20 分钟"),
    "*/30 * * * *": ("cron(0/30 * * * ? *)", "每 30 分钟"),
    "0 * * * *": ("cron(0 * * * ? *)", "每小时"),
    "0 */2 * * *": ("cron(0 0/2 * * ? *)", "每 2 小时"),
    "0 */3 * * *": ("cron(0 0/3 * * ? *)", "每 3 小时"),
    "0 */6 * * *": ("cron(0 0/6 * * ? *)", "每 6 小时"),
    "0 */12 * * *": ("cron(0 0/12 * * ? *)", "每 12 小时"),
}
EVIDENCE_DATA_SQL = """
CASE
    WHEN octet_length(data_json) <= 40000 THEN data_json
    ELSE jsonb_strip_nulls(
        jsonb_build_object(
            '_compacted', TRUE,
            '_originalBytes', octet_length(data_json),
            'seriesId', data_json::jsonb -> 'seriesId',
            'observationCount', data_json::jsonb -> 'observationCount',
            'missingObservationCount',
                data_json::jsonb -> 'missingObservationCount',
            'startDate', data_json::jsonb -> 'startDate',
            'endDate', data_json::jsonb -> 'endDate',
            'latest', data_json::jsonb -> 'latest',
            'latestDate', data_json::jsonb -> 'latestDate',
            'latestValue', data_json::jsonb -> 'latestValue',
            'startValue', data_json::jsonb -> 'startValue',
            'changePercent', data_json::jsonb -> 'changePercent',
            'changeFromPrevious',
                data_json::jsonb -> 'changeFromPrevious',
            'changeFromFirst', data_json::jsonb -> 'changeFromFirst',
            '_preview', left(data_json, 2000)
        )
    )::text
END
"""
AGENT_PATTERNS = {
    "OpenAI Crawler": ("gptbot", "chatgpt-user", "openai"),
    "ClaudeBot": ("claudebot", "claude-web"),
    "PerplexityBot": ("perplexitybot", "perplexity-user"),
    "Google-Extended": ("google-extended", "gemini"),
    "Amazonbot": ("amazonbot",),
    "Common Crawl": ("ccbot",),
}
SOURCE_METHODS = {"feed", "web", "browser", "api", "timeseries", "x402"}
SOURCE_STATUSES = {"active", "paused", "error"}
SOURCE_ACCESS_MODELS = {"open", "authenticated", "x402"}
CRAWLER_CONTACT_URL = os.environ.get(
    "CRAWLER_CONTACT_URL",
    os.environ.get(
        "X402_PUBLIC_BASE_URL",
        "https://d1tsbnft7iv51.cloudfront.net/",
    ),
)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validated_public_https_url(value: object) -> str:
    raw = str(value or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username:
        raise ValueError("数据源 URL 必须是公开的 HTTPS 地址")
    if parsed.port not in {None, 443}:
        raise ValueError("数据源 URL 仅允许使用 HTTPS 443 端口")
    return raw


def assert_public_hostname(url: str) -> None:
    hostname = urlparse(url).hostname or ""
    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
        }
    except socket.gaierror as error:
        raise ValueError(f"域名解析失败：{error}") from error
    if not addresses:
        raise ValueError("域名没有可用 IP 地址")
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise ValueError("数据源不能解析到内网、环回或保留地址")


def normalized_source_config(value: object) -> dict:
    if not isinstance(value, dict):
        raise ValueError("config 必须是 JSON 对象")
    config = dict(value)
    raw_policy = config.get("requestPolicy")
    if raw_policy is None:
        return config
    if not isinstance(raw_policy, dict):
        raise ValueError("requestPolicy 必须是 JSON 对象")
    policy = dict(raw_policy)
    if "userAgent" in policy:
        user_agent = str(policy["userAgent"] or "").strip()
        if not user_agent or len(user_agent) > 500 or "\r" in user_agent or "\n" in user_agent:
            raise ValueError("requestPolicy.userAgent 无效")
        policy["userAgent"] = user_agent
    if "requestsPerSecond" in policy:
        requests_per_second = float(policy["requestsPerSecond"])
        if not 0.1 <= requests_per_second <= 10:
            raise ValueError("requestsPerSecond 必须在 0.1 到 10 之间")
        policy["requestsPerSecond"] = requests_per_second
    if "cacheTtlSeconds" in policy:
        cache_ttl = int(policy["cacheTtlSeconds"])
        if not 0 <= cache_ttl <= 604800:
            raise ValueError("cacheTtlSeconds 必须在 0 到 604800 之间")
        policy["cacheTtlSeconds"] = cache_ttl
    if "maxRetries" in policy:
        max_retries = int(policy["maxRetries"])
        if not 0 <= max_retries <= 5:
            raise ValueError("maxRetries 必须在 0 到 5 之间")
        policy["maxRetries"] = max_retries
    if "retryStatusCodes" in policy:
        statuses = policy["retryStatusCodes"]
        if not isinstance(statuses, list) or any(
            int(status) not in {408, 425, 429, 500, 502, 503, 504}
            for status in statuses
        ):
            raise ValueError("retryStatusCodes 包含不安全的状态码")
        policy["retryStatusCodes"] = list(dict.fromkeys(int(status) for status in statuses))
    if "maxRetryAfterSeconds" in policy:
        max_retry_after = int(policy["maxRetryAfterSeconds"])
        if not 1 <= max_retry_after <= 900:
            raise ValueError("maxRetryAfterSeconds 必须在 1 到 900 之间")
        policy["maxRetryAfterSeconds"] = max_retry_after
    config["requestPolicy"] = policy
    return config


def source_request_policy(config: dict) -> dict:
    policy = dict(config.get("requestPolicy") or {})
    user_agent = str(
        policy.get("userAgent")
        or "ApertureGEORegistryTest/2.0 (Aperture GEO; +{contactUrl})"
    )
    policy["userAgent"] = user_agent.replace("{contactUrl}", CRAWLER_CONTACT_URL)
    policy.setdefault("maxRetries", 1)
    policy.setdefault("retryStatusCodes", [429, 503])
    policy.setdefault("maxRetryAfterSeconds", 60)
    return policy


def test_data_source_url(
    url: str,
    *,
    config: dict | None = None,
    access_model: str = "open",
) -> dict[str, object]:
    current_url = validated_public_https_url(url)
    opener = build_opener(_NoRedirect())
    policy = source_request_policy(config or {})
    retries_remaining = int(policy["maxRetries"])
    redirects_remaining = 5
    while True:
        assert_public_hostname(current_url)
        request = Request(
            current_url,
            headers={
                "User-Agent": str(policy["userAgent"]),
                "Accept": "application/rss+xml, application/xml, text/csv, text/html, application/json",
                "Range": "bytes=0-4095",
            },
        )
        try:
            with opener.open(request, timeout=15) as response:
                response.read(4096)
                final_url = validated_public_https_url(response.geturl())
                assert_public_hostname(final_url)
                return {
                    "ok": True,
                    "statusCode": int(response.status),
                    "contentType": response.headers.get("Content-Type", ""),
                    "finalUrl": final_url,
                    "message": f"HTTP {response.status} · 来源可访问",
                }
        except HTTPError as error:
            if error.code in {301, 302, 303, 307, 308}:
                if redirects_remaining <= 0:
                    raise ValueError("重定向次数超过限制") from error
                location = error.headers.get("Location", "")
                if not location:
                    raise ValueError(f"HTTP {error.code} 缺少重定向地址") from error
                current_url = validated_public_https_url(urljoin(current_url, location))
                redirects_remaining -= 1
                continue
            if error.code == 402 and access_model == "x402":
                return {
                    "ok": True,
                    "statusCode": error.code,
                    "contentType": error.headers.get("Content-Type", ""),
                    "finalUrl": current_url,
                    "message": "HTTP 402 · x402 付费挑战正常",
                }
            if error.code in set(policy["retryStatusCodes"]) and retries_remaining > 0:
                raw_retry_after = str(error.headers.get("Retry-After", "")).strip()
                retry_after = int(raw_retry_after) if raw_retry_after.isdigit() else 1
                time.sleep(min(retry_after, int(policy["maxRetryAfterSeconds"])))
                retries_remaining -= 1
                continue
            raise ValueError(f"来源返回 HTTP {error.code}") from error
        except URLError as error:
            raise ValueError(f"连接失败：{error.reason}") from error


def parse_json(value: str | None, fallback):
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def identify_visitor(user_agent: str) -> tuple[str, str | None]:
    normalized = user_agent.lower()
    for name, patterns in AGENT_PATTERNS.items():
        if any(pattern in normalized for pattern in patterns):
            return "agent", name
    return "human", None


def normalize_crawler_schedule(value: object) -> dict[str, str]:
    schedule = re.sub(r"\s+", " ", str(value or "").strip())
    if schedule == "0 */1 * * *":
        schedule = "0 * * * *"
    preset = CRAWLER_SCHEDULE_PRESETS.get(schedule)
    if preset:
        return {
            "schedule": schedule,
            "eventbridgeExpression": preset[0],
            "label": preset[1],
        }
    daily = re.fullmatch(r"([0-5]?\d) ([01]?\d|2[0-3]) \* \* \*", schedule)
    if daily:
        minute = int(daily.group(1))
        hour = int(daily.group(2))
        return {
            "schedule": f"{minute} {hour} * * *",
            "eventbridgeExpression": f"cron({minute} {hour} * * ? *)",
            "label": f"每天 {hour:02d}:{minute:02d}",
        }
    raise ValueError(
        "仅支持每 10/15/20/30 分钟、每 1/2/3/6/12 小时或每天固定时间"
    )


def eventbridge_schedule_states(slugs: list[str]) -> dict[str, str]:
    if not USE_AURORA_DATA_API:
        return {}
    scheduler = boto3.client("scheduler", region_name=AWS_REGION)
    states: dict[str, str] = {}
    for slug in slugs:
        name = f"geo-{slug}"
        try:
            response = scheduler.get_schedule(
                Name=name,
                GroupName=SCHEDULER_GROUP,
            )
            states[name] = str(response["State"])
        except scheduler.exceptions.ResourceNotFoundException:
            states[name] = "MISSING"
    return states


def sync_eventbridge_schedule(
    slug: str,
    *,
    status: str | None = None,
    schedule_expression: str | None = None,
) -> dict[str, str] | None:
    if not USE_AURORA_DATA_API:
        return None
    scheduler = boto3.client("scheduler", region_name=AWS_REGION)
    name = f"geo-{slug}"
    current = scheduler.get_schedule(Name=name, GroupName=SCHEDULER_GROUP)
    request = {
        "Name": name,
        "GroupName": SCHEDULER_GROUP,
        "ScheduleExpression": schedule_expression or current["ScheduleExpression"],
        "FlexibleTimeWindow": current["FlexibleTimeWindow"],
        "Target": current["Target"],
        "State": (
            "DISABLED"
            if status == "paused"
            else "ENABLED"
            if status is not None
            else current["State"]
        ),
    }
    for key in (
        "Description",
        "StartDate",
        "EndDate",
        "ScheduleExpressionTimezone",
        "KmsKeyArn",
        "ActionAfterCompletion",
    ):
        if key in current:
            request[key] = current[key]
    scheduler.update_schedule(**request)
    return {
        "state": request["State"],
        "scheduleExpression": request["ScheduleExpression"],
        "timezone": str(request.get("ScheduleExpressionTimezone") or "UTC"),
    }


def invoke_crawler_bridge(
    slug: str,
    *,
    asynchronous: bool = False,
    allow_payment: bool = False,
    force_analysis: bool = False,
) -> dict:
    client = boto3.client("lambda", region_name=AWS_REGION)
    response = client.invoke(
        FunctionName=SCHEDULER_BRIDGE_FUNCTION,
        InvocationType="Event" if asynchronous else "RequestResponse",
        Payload=json.dumps(
            {
                "crawlerSlug": slug,
                "scheduledTime": "admin-manual",
                "allowPayment": allow_payment,
                "forceAnalysis": force_analysis,
                "overridePaused": True,
            }
        ).encode("utf-8"),
    )
    if asynchronous:
        return {"submitted": response["StatusCode"] == 202}
    body = json.loads(response["Payload"].read().decode("utf-8"))
    if response.get("FunctionError"):
        raise RuntimeError(body.get("errorMessage", "Lambda bridge failed"))
    return body


def public_article(row: dict, *, detailed: bool = False) -> dict:
    result = {
        "id": row["id"],
        "slug": row["slug"],
        "title": row["title"],
        "dek": row["dek"],
        "summary": row["summary"],
        "author": row["author"],
        "authorRole": row["author_role"],
        "readMinutes": row["read_minutes"],
        "publishedAt": row["published_at"],
        "updatedAt": row["updated_at"],
        "featured": bool(row["featured"]),
        "heroStyle": row["hero_style"],
        "authorityScore": row["authority_score"],
        "citationCount": row["citation_count"],
        "accessModel": row["access_model"],
        "agentPrice": row["agent_price"],
        "keywords": parse_json(row["keywords"], []),
        "category": {
            "slug": row["category_slug"],
            "name": row["category_name"],
            "eyebrow": row["category_eyebrow"],
            "accent": row["category_accent"],
        },
    }
    if detailed:
        result["sections"] = parse_json(row["body_json"], [])
        result["sources"] = row.get("sources", [])
    return result


class ApiHandler(BaseHTTPRequestHandler):
    server_version = "ApertureGEO-API/2.0"

    def end_headers(self) -> None:
        origin = self.headers.get("Origin", "*")
        allowed = origin if origin.startswith(("http://127.0.0.1:", "http://localhost:")) else "*"
        self.send_header("Access-Control-Allow-Origin", allowed)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, X-Admin-Key, X-Agent-Name, PAYMENT-SIGNATURE, X-PAYMENT",
        )
        self.send_header(
            "Access-Control-Expose-Headers",
            "PAYMENT-REQUIRED, PAYMENT-RESPONSE",
        )
        if allowed != "*":
            self.send_header("Access-Control-Allow-Credentials", "true")
        self.send_header("Vary", "Origin, User-Agent")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        super().end_headers()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)

        if path == "/api/ping":
            self._json(
                {
                    "status": "ok",
                    "service": "aperture-geo-api",
                    "timestamp": utc_now(),
                }
            )
            return
        if path == "/api/health":
            self._health()
            return
        if path == "/api/v1/site":
            self._site_info()
            return
        if path == "/api/v1/categories":
            self._categories()
            return
        if path == "/api/v1/articles":
            self._articles(query)
            return
        if path.startswith("/api/v1/articles/"):
            self._article(path.split("/")[-1])
            return
        if path == "/api/v1/search":
            self._search(query)
            return
        paid_article_match = re.fullmatch(r"/agent/v1/articles/([^/]+)/paid", path)
        if paid_article_match:
            self._agent_article(paid_article_match.group(1), paid=True)
            return
        if path.startswith("/agent/v1/articles/"):
            self._agent_article(path.split("/")[-1], paid=False)
            return
        if path == "/api/admin/auth/me":
            self._auth_me()
            return
        if path == "/api/admin/metrics":
            if not self._require_admin():
                return
            self._admin_metrics(query)
            return
        if path == "/api/admin/articles":
            if not self._require_admin():
                return
            self._admin_articles()
            return
        admin_article_match = re.fullmatch(r"/api/admin/articles/(\d+)", path)
        if admin_article_match:
            if not self._require_admin():
                return
            self._admin_article_detail(int(admin_article_match.group(1)))
            return
        if path == "/api/admin/crawlers":
            if not self._require_admin():
                return
            self._admin_crawlers()
            return
        if path == "/api/admin/data-sources":
            if not self._require_admin():
                return
            self._admin_data_sources()
            return
        if path == "/api/admin/jobs":
            if not self._require_admin():
                return
            self._admin_jobs()
            return
        if path == "/api/admin/research":
            if not self._require_admin():
                return
            self._admin_research()
            return
        research_match = re.fullmatch(r"/api/admin/research/(\d+)", path)
        if research_match:
            if not self._require_admin():
                return
            self._admin_research_detail(int(research_match.group(1)))
            return
        if path == "/api/admin/events":
            if not self._require_admin():
                return
            self._admin_events()
            return
        if path == "/api/admin/settings":
            if not self._require_admin():
                return
            self._admin_settings()
            return
        self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def _health(self) -> None:
        try:
            with connection() as conn:
                database = conn.execute(
                    """
                    SELECT current_database() AS name,
                           current_setting('server_version') AS version
                    """
                ).fetchone()
                article_count = conn.execute(
                    "SELECT COUNT(*) AS count FROM articles"
                ).fetchone()["count"]
            self._json(
                {
                    "status": "healthy",
                    "service": "aperture-geo-api",
                    "database": "aurora-postgresql" if USE_AURORA_DATA_API else "postgresql",
                    "databaseMode": "data-api" if USE_AURORA_DATA_API else "direct",
                    "databaseName": database["name"],
                    "databaseVersion": database["version"],
                    "articleCount": article_count,
                    "timestamp": utc_now(),
                }
            )
        except Exception as error:
            self._json(
                {
                    "status": "unhealthy",
                    "service": "aperture-geo-api",
                    "database": "aurora-postgresql" if USE_AURORA_DATA_API else "postgresql",
                    "databaseMode": "data-api" if USE_AURORA_DATA_API else "direct",
                    "error": str(error),
                    "timestamp": utc_now(),
                },
                HTTPStatus.SERVICE_UNAVAILABLE,
            )

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/")
        payload = self._read_json()
        if path == "/api/v1/track":
            self._track(payload)
            return
        if path == "/api/admin/auth/login":
            self._login(payload)
            return
        if path == "/api/admin/auth/logout":
            self._logout()
            return
        if path == "/api/admin/articles":
            if not self._require_admin():
                return
            self._create_article(payload)
            return
        if path == "/api/admin/data-sources":
            if not self._require_admin():
                return
            self._create_data_source(payload)
            return
        source_test_match = re.fullmatch(
            r"/api/admin/data-sources/(\d+)/test", path
        )
        if source_test_match:
            if not self._require_admin():
                return
            self._test_data_source(int(source_test_match.group(1)))
            return
        crawler_match = re.fullmatch(r"/api/admin/crawlers/(\d+)/run", path)
        if crawler_match:
            if not self._require_admin():
                return
            self._run_crawler(int(crawler_match.group(1)), payload)
            return
        if path == "/api/admin/crawlers/run-all":
            if not self._require_admin():
                return
            self._run_all_crawlers()
            return
        self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def _session_token(self) -> str | None:
        raw_cookie = self.headers.get("Cookie", "")
        if not raw_cookie:
            return None
        try:
            cookie = SimpleCookie()
            cookie.load(raw_cookie)
            morsel = cookie.get(ADMIN_COOKIE_NAME)
            return morsel.value if morsel else None
        except Exception:
            return None

    def _current_admin(self) -> dict | None:
        token = self._session_token()
        if not token:
            return None
        token_digest = session_token_hash(token)
        timestamp = utc_now()
        with connection() as conn:
            user = conn.execute(
                """
                SELECT u.id, u.username, u.display_name, u.role, s.id session_id,
                       s.expires_at
                FROM admin_sessions s
                JOIN admin_users u ON u.id = s.user_id
                WHERE s.token_hash = %s AND s.expires_at > %s
                  AND u.status = 'active'
                """,
                (token_digest, timestamp),
            ).fetchone()
            if not user:
                return None
            conn.execute(
                "UPDATE admin_sessions SET last_seen_at = %s WHERE id = %s",
                (timestamp, user["session_id"]),
            )
        return dict(user)

    def _auth_me(self) -> None:
        user = self._current_admin()
        if not user:
            self._json({"error": "Authentication required"}, HTTPStatus.UNAUTHORIZED)
            return
        self._json(
            {
                "authenticated": True,
                "user": {
                    "id": user["id"],
                    "username": user["username"],
                    "displayName": user["display_name"],
                    "role": user["role"],
                    "sessionExpiresAt": user["expires_at"],
                },
            }
        )

    def _login(self, payload: dict) -> None:
        username = str(payload.get("username", "")).strip().lower()
        password = str(payload.get("password", ""))
        if not username or not password or len(username) > 120 or len(password) > 1024:
            self._json(
                {"error": "请输入用户名和密码"},
                HTTPStatus.BAD_REQUEST,
            )
            return

        now_dt = datetime.now(timezone.utc).replace(microsecond=0)
        timestamp = now_dt.isoformat()
        with connection() as conn:
            user = conn.execute(
                """
                SELECT id, username, display_name, role, password_hash,
                       password_salt, password_iterations, status,
                       failed_attempts, locked_until
                FROM admin_users WHERE username = %s
                """,
                (username,),
            ).fetchone()

            if not user:
                hash_password(password, salt=b"aperture-login!")
                self._json(
                    {"error": "用户名或密码错误"},
                    HTTPStatus.UNAUTHORIZED,
                )
                return

            locked_until = user["locked_until"]
            if locked_until:
                try:
                    if datetime.fromisoformat(locked_until) > now_dt:
                        self._json(
                            {"error": "登录失败次数过多，请稍后再试"},
                            HTTPStatus.UNAUTHORIZED,
                        )
                        return
                except ValueError:
                    pass

            valid = (
                user["status"] == "active"
                and verify_password(
                    password,
                    user["password_hash"],
                    user["password_salt"],
                    int(user["password_iterations"]),
                )
            )
            if not valid:
                failed_attempts = int(user["failed_attempts"] or 0) + 1
                next_locked_until = None
                if failed_attempts >= ADMIN_MAX_FAILED_ATTEMPTS:
                    next_locked_until = (
                        now_dt + timedelta(minutes=ADMIN_LOCK_MINUTES)
                    ).isoformat()
                conn.execute(
                    """
                    UPDATE admin_users
                    SET failed_attempts = %s, locked_until = %s, updated_at = %s
                    WHERE id = %s
                    """,
                    (
                        failed_attempts,
                        next_locked_until,
                        timestamp,
                        user["id"],
                    ),
                )
                self._json(
                    {"error": "用户名或密码错误"},
                    HTTPStatus.UNAUTHORIZED,
                )
                return

            token = generate_session_token()
            expires_at = (
                now_dt + timedelta(hours=ADMIN_SESSION_HOURS)
            ).isoformat()
            conn.execute(
                "DELETE FROM admin_sessions WHERE expires_at <= %s",
                (timestamp,),
            )
            conn.execute(
                """
                INSERT INTO admin_sessions(
                    user_id, token_hash, created_at, expires_at, last_seen_at,
                    user_agent, ip_address
                ) VALUES(%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    user["id"],
                    session_token_hash(token),
                    timestamp,
                    expires_at,
                    timestamp,
                    self.headers.get("User-Agent", "")[:500],
                    self.client_address[0][:100],
                ),
            )
            conn.execute(
                """
                UPDATE admin_users
                SET failed_attempts = 0, locked_until = NULL, last_login = %s,
                    updated_at = %s
                WHERE id = %s
                """,
                (timestamp, timestamp, user["id"]),
            )

        cookie = (
            f"{ADMIN_COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Strict; "
            f"Max-Age={ADMIN_SESSION_HOURS * 3600}"
        )
        if ADMIN_COOKIE_SECURE:
            cookie += "; Secure"
        self._json(
            {
                "authenticated": True,
                "user": {
                    "id": user["id"],
                    "username": user["username"],
                    "displayName": user["display_name"],
                    "role": user["role"],
                    "sessionExpiresAt": expires_at,
                },
            },
            extra_headers={"Set-Cookie": cookie},
        )

    def _logout(self) -> None:
        token = self._session_token()
        if token:
            with connection() as conn:
                conn.execute(
                    "DELETE FROM admin_sessions WHERE token_hash = %s",
                    (session_token_hash(token),),
                )
        cookie = (
            f"{ADMIN_COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Strict; "
            "Max-Age=0"
        )
        if ADMIN_COOKIE_SECURE:
            cookie += "; Secure"
        self._json(
            {"ok": True},
            extra_headers={"Set-Cookie": cookie},
        )

    def do_PATCH(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/")
        payload = self._read_json()
        if not self._require_admin():
            return
        if path == "/api/admin/articles/batch":
            self._batch_articles(payload)
            return
        article_match = re.fullmatch(r"/api/admin/articles/(\d+)", path)
        if article_match:
            self._update_article(int(article_match.group(1)), payload)
            return
        crawler_match = re.fullmatch(r"/api/admin/crawlers/(\d+)", path)
        if crawler_match:
            self._update_crawler(int(crawler_match.group(1)), payload)
            return
        source_match = re.fullmatch(r"/api/admin/data-sources/(\d+)", path)
        if source_match:
            self._update_data_source(int(source_match.group(1)), payload)
            return
        setting_match = re.fullmatch(r"/api/admin/settings/([a-z0-9_]+)", path)
        if setting_match:
            self._update_setting(setting_match.group(1), payload)
            return
        self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def do_DELETE(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/")
        if not self._require_admin():
            return
        source_match = re.fullmatch(r"/api/admin/data-sources/(\d+)", path)
        if source_match:
            self._delete_data_source(int(source_match.group(1)))
            return
        self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def _site_info(self) -> None:
        with connection() as conn:
            article_count = conn.execute(
                "SELECT COUNT(*) AS count FROM articles WHERE status = 'published'"
            ).fetchone()["count"]
            source_count = conn.execute(
                "SELECT COUNT(*) AS count FROM sources"
            ).fetchone()["count"]
            latest = conn.execute(
                """
                SELECT MAX(updated_at) AS latest
                FROM articles WHERE status = 'published'
                """
            ).fetchone()["latest"]
        self._json(
            {
                "name": "Aperture Intelligence",
                "description": "面向 AI 时代的技术与商业研究",
                "articleCount": article_count,
                "sourceCount": source_count,
                "lastUpdated": latest,
                "machineAccess": "/agent/v1/articles/{slug}",
            }
        )

    def _categories(self) -> None:
        with connection() as conn:
            rows = conn.execute(
                """
                SELECT c.*, COUNT(a.id) AS article_count
                FROM categories c
                LEFT JOIN articles a ON a.category_id = c.id AND a.status = 'published'
                GROUP BY c.id
                ORDER BY c.sort_order
                """
            ).fetchall()
        self._json(
            [
                {
                    "slug": row["slug"],
                    "name": row["name"],
                    "eyebrow": row["eyebrow"],
                    "description": row["description"],
                    "accent": row["accent"],
                    "articleCount": row["article_count"],
                }
                for row in rows
            ]
        )

    def _articles(self, query: dict) -> None:
        category = query.get("category", [None])[0]
        featured = query.get("featured", [None])[0]
        limit = min(max(int(query.get("limit", ["30"])[0]), 1), 100)
        clauses = ["a.status = 'published'"]
        params: list[object] = []
        if category:
            clauses.append("c.slug = %s")
            params.append(category)
        if featured in ("1", "true"):
            clauses.append("a.featured = TRUE")
        params.append(limit)
        with connection() as conn:
            rows = conn.execute(
                f"""
                SELECT a.*, c.slug category_slug, c.name category_name,
                       c.eyebrow category_eyebrow, c.accent category_accent
                FROM articles a
                JOIN categories c ON c.id = a.category_id
                WHERE {' AND '.join(clauses)}
                ORDER BY a.featured DESC, a.published_at DESC
                LIMIT %s
                """,
                params,
            ).fetchall()
        self._json([public_article(dict(row)) for row in rows])

    def _article(self, slug: str) -> None:
        with connection() as conn:
            row = conn.execute(
                """
                SELECT a.*, c.slug category_slug, c.name category_name,
                       c.eyebrow category_eyebrow, c.accent category_accent
                FROM articles a JOIN categories c ON c.id = a.category_id
                WHERE a.slug = %s AND a.status = 'published'
                """,
                (slug,),
            ).fetchone()
            if not row:
                self._json({"error": "Article not found"}, HTTPStatus.NOT_FOUND)
                return
            sources = [
                dict(source)
                for source in conn.execute(
                    """
                    SELECT publisher, title, url, published_at publishedAt,
                           source_type sourceType
                    FROM sources WHERE article_id = %s ORDER BY id
                    """,
                    (row["id"],),
                ).fetchall()
            ]
            article_row = dict(row)
            article_row["sources"] = sources
            related = conn.execute(
                """
                SELECT a.*, c.slug category_slug, c.name category_name,
                       c.eyebrow category_eyebrow, c.accent category_accent
                FROM articles a JOIN categories c ON c.id = a.category_id
                WHERE a.status = 'published' AND a.id != %s
                ORDER BY CASE WHEN a.category_id = %s THEN 0 ELSE 1 END,
                         a.published_at DESC LIMIT 3
                """,
                (row["id"], row["category_id"]),
            ).fetchall()
            visitor_type, agent_name = identify_visitor(self.headers.get("User-Agent", ""))
            conn.execute(
                """
                INSERT INTO traffic_events(event_type, visitor_type, agent_name, article_id, occurred_at, metadata)
                VALUES(%s, %s, %s, %s, %s, '{}')
                """,
                (
                    "agent_view" if visitor_type == "agent" else "human_view",
                    visitor_type,
                    agent_name,
                    row["id"],
                    utc_now(),
                ),
            )
        response = public_article(article_row, detailed=True)
        response["related"] = [public_article(dict(item)) for item in related]
        self._json(response)

    def _record_event(
        self,
        conn,
        *,
        event_type: str,
        article_id: int,
        agent_name: str,
        metadata: dict,
    ) -> None:
        conn.execute(
            """
            INSERT INTO traffic_events(
                event_type, visitor_type, agent_name, article_id, occurred_at, metadata
            )
            VALUES(%s, 'agent', %s, %s, %s, %s)
            """,
            (
                event_type,
                agent_name,
                article_id,
                utc_now(),
                json.dumps(metadata, ensure_ascii=False),
            ),
        )

    def _agent_article(self, slug: str, *, paid: bool) -> None:
        with connection() as conn:
            row = conn.execute(
                """
                SELECT a.*, c.slug category_slug, c.name category_name,
                       c.eyebrow category_eyebrow, c.accent category_accent
                FROM articles a JOIN categories c ON c.id = a.category_id
                WHERE a.slug = %s AND a.status = 'published'
                """,
                (slug,),
            ).fetchone()
            if not row:
                self._json({"error": "Article not found"}, HTTPStatus.NOT_FOUND)
                return
            sources = [
                dict(source)
                for source in conn.execute(
                    "SELECT publisher, title, url, published_at, source_type FROM sources WHERE article_id = %s",
                    (row["id"],),
                ).fetchall()
            ]
        agent_name = (
            identify_visitor(self.headers.get("User-Agent", ""))[1]
            or self.headers.get("X-Agent-Name")
            or "Machine client"
        )[:120]
        sections = parse_json(row["body_json"], [])
        claims = []
        for section in sections:
            for paragraph in section.get("paragraphs", []):
                claims.append({"text": paragraph, "section": section.get("heading")})
            for bullet in section.get("bullets", []):
                claims.append({"text": bullet, "section": section.get("heading")})
        payload = {
            "@context": "https://schema.org",
            "@type": "AnalysisNewsArticle",
            "identifier": row["slug"],
            "headline": row["title"],
            "description": row["dek"],
            "abstract": row["summary"],
            "datePublished": row["published_at"],
            "dateModified": row["updated_at"],
            "author": {"@type": "Person", "name": row["author"], "jobTitle": row["author_role"]},
            "about": parse_json(row["keywords"], []),
            "category": row["category_name"],
            "authorityScore": row["authority_score"],
            "claims": claims,
            "sections": sections,
            "citations": sources,
            "license": {
                "model": row["access_model"],
                "price": paid_price(row["agent_price"]),
                "currency": "USDC",
                "paymentProtocol": "x402",
                "network": X402_NETWORK,
                "payTo": X402_PAY_TO_ADDRESS,
                "variants": {
                    "A": f"/agent/v1/articles/{row['slug']}",
                    "B": f"/agent/v1/articles/{row['slug']}/paid",
                },
            },
            "accessVariant": "B" if paid else "A",
            "contentPolicy": {
                "citationAllowed": True,
                "attributionRequired": True,
                "trainingUse": "contact publisher",
            },
        }
        if not paid:
            with connection() as conn:
                self._record_event(
                    conn,
                    event_type="agent_view",
                    article_id=row["id"],
                    agent_name=agent_name,
                    metadata={
                        "endpoint": "agent",
                        "variant": "A",
                        "access": "free",
                    },
                )
            self._json(payload, extra_headers={"X-Robots-Tag": "index, follow"})
            return

        paid_path = f"/agent/v1/articles/{row['slug']}/paid"
        try:
            payment_request = process_paid_request(
                self,
                path=paid_path,
                title=row["title"],
                configured_price=row["agent_price"],
            )
        except Exception as error:
            with connection() as conn:
                self._record_event(
                    conn,
                    event_type="x402_service_error",
                    article_id=row["id"],
                    agent_name=agent_name,
                    metadata={"variant": "B", "error": str(error)[:500]},
                )
            self._send_instructions(error_instructions(error))
            return

        result = payment_request.process_result
        if result.type != "payment-verified":
            supplied_payment = bool(
                self.headers.get("PAYMENT-SIGNATURE") or self.headers.get("X-PAYMENT")
            )
            with connection() as conn:
                self._record_event(
                    conn,
                    event_type=(
                        "x402_verification_failed"
                        if supplied_payment
                        else "x402_challenge"
                    ),
                    article_id=row["id"],
                    agent_name=agent_name,
                    metadata={
                        "variant": "B",
                        "priceUsd": payment_request.price_usd,
                        "network": X402_NETWORK,
                    },
                )
            self._send_instructions(
                result.response
                or error_instructions(X402ConfigurationError("Payment required"))
            )
            return

        settlement = settle_paid_request(payment_request, response_body=payload)
        if not settlement.success:
            with connection() as conn:
                self._record_event(
                    conn,
                    event_type="x402_settlement_failed",
                    article_id=row["id"],
                    agent_name=agent_name,
                    metadata={
                        "variant": "B",
                        "priceUsd": payment_request.price_usd,
                        "network": settlement.network or X402_NETWORK,
                        "error": (settlement.error_reason or "")[:500],
                    },
                )
            self._send_instructions(
                settlement.response
                or error_instructions(
                    RuntimeError(settlement.error_reason or "Settlement failed")
                )
            )
            return

        requirements = result.payment_requirements
        payload["settlement"] = {
            "success": True,
            "transactionHash": settlement.transaction,
            "network": settlement.network or requirements.network,
            "payer": settlement.payer,
            "amountBaseUnits": requirements.amount,
            "amountUsd": payment_request.price_usd,
        }
        with connection() as conn:
            self._record_event(
                conn,
                event_type="x402_payment",
                article_id=row["id"],
                agent_name=agent_name,
                metadata={
                    "variant": "B",
                    "priceUsd": payment_request.price_usd,
                    "amountUsd": payment_request.price_usd,
                    "amountBaseUnits": requirements.amount,
                    "asset": requirements.asset,
                    "network": settlement.network or requirements.network,
                    "payer": settlement.payer,
                    "payTo": requirements.pay_to,
                    "transactionHash": settlement.transaction,
                },
            )
        self._json(
            payload,
            extra_headers={
                **settlement.headers,
                "X-Robots-Tag": "noindex, noarchive",
                "Cache-Control": "private, no-store",
            },
        )

    def _search(self, query: dict) -> None:
        term = query.get("q", [""])[0].strip()
        if not term:
            self._json([])
            return
        pattern = f"%{term}%"
        with connection() as conn:
            rows = conn.execute(
                """
                SELECT a.*, c.slug category_slug, c.name category_name,
                       c.eyebrow category_eyebrow, c.accent category_accent
                FROM articles a JOIN categories c ON c.id = a.category_id
                WHERE a.status = 'published'
                  AND (a.title LIKE %s OR a.dek LIKE %s OR a.summary LIKE %s OR a.keywords LIKE %s)
                ORDER BY a.authority_score DESC LIMIT 20
                """,
                (pattern, pattern, pattern, pattern),
            ).fetchall()
        self._json([public_article(dict(row)) for row in rows])

    def _track(self, payload: dict) -> None:
        event_type = str(payload.get("eventType", "human_view"))[:40]
        slug = payload.get("articleSlug")
        visitor_type, agent_name = identify_visitor(self.headers.get("User-Agent", ""))
        with connection() as conn:
            article_id = None
            if slug:
                row = conn.execute("SELECT id FROM articles WHERE slug = %s", (slug,)).fetchone()
                article_id = row["id"] if row else None
            conn.execute(
                """
                INSERT INTO traffic_events(event_type, visitor_type, agent_name, article_id, occurred_at, metadata)
                VALUES(%s, %s, %s, %s, %s, %s)
                """,
                (
                    event_type,
                    visitor_type,
                    agent_name,
                    article_id,
                    utc_now(),
                    json.dumps(payload.get("metadata", {}), ensure_ascii=False),
                ),
            )
        self._json({"ok": True}, HTTPStatus.CREATED)

    def _admin_metrics(self, query: dict) -> None:
        range_key = query.get("range", ["30d"])[0]
        custom_start = query.get("start", [""])[0]
        custom_end = query.get("end", [""])[0]
        if custom_start or custom_end:
            try:
                start_date = date.fromisoformat(custom_start)
                end_date = date.fromisoformat(custom_end)
            except ValueError:
                self._json(
                    {"error": "start 和 end 必须是 YYYY-MM-DD"},
                    HTTPStatus.BAD_REQUEST,
                )
                return
            days = (end_date - start_date).days + 1
            if days < 1 or days > 366:
                self._json(
                    {"error": "统计时间范围必须为 1 到 366 天"},
                    HTTPStatus.BAD_REQUEST,
                )
                return
            range_key = "custom"
        else:
            days = {"7d": 7, "30d": 30, "90d": 90}.get(range_key, 30)
            end_date = date.today()
            start_date = end_date - timedelta(days=days - 1)
        previous_start_date = start_date - timedelta(days=days)
        next_date = end_date + timedelta(days=1)
        start = start_date.isoformat()
        end = end_date.isoformat()
        previous_start = previous_start_date.isoformat()
        with connection() as conn:
            events = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT event_type, visitor_type, agent_name, occurred_at, metadata
                    FROM traffic_events
                    WHERE occurred_at >= %s AND occurred_at < %s
                    ORDER BY occurred_at
                    """,
                    (previous_start, next_date.isoformat()),
                ).fetchall()
            ]
            status_counts = {
                row["status"]: row["count"]
                for row in conn.execute(
                    "SELECT status, COUNT(*) count FROM articles GROUP BY status"
                )
            }
            agents = conn.execute(
                """
                SELECT COUNT(*) total,
                       SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) running,
                       SUM(pages_today) pages
                FROM crawler_agents
                """
            ).fetchone()
        agent_event_types = {
            "agent_view",
            "x402_challenge",
            "x402_payment",
            "x402_verification_failed",
            "x402_settlement_failed",
        }

        def empty_totals() -> dict[str, float]:
            return {
                "human": 0,
                "agent": 0,
                "citations": 0,
                "clicks": 0,
                "payments": 0,
                "revenue": 0.0,
                "variantA": 0,
                "variantB": 0,
                "challenges": 0,
            }

        def add_event(totals: dict[str, float], event: dict) -> None:
            event_type = event["event_type"]
            metadata = parse_json(event["metadata"], {})
            if event_type == "human_view":
                totals["human"] += 1
            if event_type in agent_event_types:
                totals["agent"] += 1
            if event_type == "citation":
                totals["citations"] += 1
            if event_type == "human_click":
                totals["clicks"] += 1
            if event_type == "x402_payment":
                totals["payments"] += 1
                totals["revenue"] += float(
                    metadata.get("amountUsd", metadata.get("amount", 0)) or 0
                )
            if metadata.get("variant") == "A" or (
                event_type == "agent_view" and not metadata.get("variant")
            ):
                totals["variantA"] += 1
            if metadata.get("variant") == "B":
                totals["variantB"] += 1
            if event_type == "x402_challenge":
                totals["challenges"] += 1

        current = empty_totals()
        previous = empty_totals()
        daily_map = {
            (start_date + timedelta(days=offset)).isoformat(): empty_totals()
            for offset in range(days)
        }
        source_counts: dict[str, int] = {}
        for event in events:
            event_day = str(event["occurred_at"])[:10]
            target = current if start <= event_day <= end else previous
            add_event(target, event)
            if event_day in daily_map:
                add_event(daily_map[event_day], event)
            if start <= event_day <= end and event["event_type"] in agent_event_types:
                name = event["agent_name"] or "Machine client"
                source_counts[name] = source_counts.get(name, 0) + 1

        daily = [
            {
                "day": day,
                "human_views": int(values["human"]),
                "agent_views": int(values["agent"]),
                "citations": int(values["citations"]),
                "clicks": int(values["clicks"]),
                "payments": int(values["payments"]),
                "revenue": round(values["revenue"], 6),
            }
            for day, values in daily_map.items()
        ]
        source_total = sum(source_counts.values())
        sources = [
            {
                "name": name,
                "count": count,
                "value": round(count / max(1, source_total) * 100, 1),
            }
            for name, count in sorted(
                source_counts.items(), key=lambda item: item[1], reverse=True
            )[:5]
        ]

        def growth(key: str) -> float:
            current_value = current[key]
            previous_value = previous[key]
            return round((current_value - previous_value) / previous_value * 100, 1) if previous_value else 0

        self._json(
            {
                "range": days,
                "rangeKey": range_key,
                "startDate": start,
                "endDate": end,
                "summary": {
                    "humanViews": int(current["human"]),
                    "agentViews": int(current["agent"]),
                    "citations": int(current["citations"]),
                    "clicks": int(current["clicks"]),
                    "payments": int(current["payments"]),
                    "revenue": round(current["revenue"], 6),
                    "agentShare": round(
                        (current["agent"] or 0)
                        / max(1, (current["human"] or 0) + (current["agent"] or 0))
                        * 100,
                        1,
                    ),
                    "citationRate": round((current["citations"] or 0) / max(1, current["agent"] or 0) * 100, 1),
                },
                "growth": {key: growth(key) for key in ("human", "agent", "citations", "revenue")},
                "daily": daily,
                "agentSources": sources,
                "abTest": {
                    "variantAViews": int(current["variantA"]),
                    "variantBViews": int(current["variantB"]),
                    "challenges": int(current["challenges"]),
                    "payments": int(current["payments"]),
                    "conversionRate": round(
                        current["payments"]
                        / max(1, current["challenges"])
                        * 100,
                        1,
                    ),
                    "revenue": round(current["revenue"], 6),
                },
                "content": status_counts,
                "crawlers": {
                    "total": agents["total"] or 0,
                    "running": agents["running"] or 0,
                    "pagesToday": agents["pages"] or 0,
                },
            }
        )

    def _admin_articles(self) -> None:
        with connection() as conn:
            rows = conn.execute(
                """
                SELECT a.id, a.slug, a.title, a.status, a.featured, a.author,
                       a.updated_at, a.authority_score, a.citation_count,
                       a.access_model, a.agent_price, c.name category_name
                FROM articles a JOIN categories c ON c.id = a.category_id
                ORDER BY a.updated_at DESC
                """
            ).fetchall()
        self._json(
            [
                {
                    **dict(row),
                    "featured": bool(row["featured"]),
                }
                for row in rows
            ]
        )

    def _admin_article_detail(self, article_id: int) -> None:
        with connection() as conn:
            row = conn.execute(
                """
                SELECT a.*, c.slug category_slug, c.name category_name,
                       c.eyebrow category_eyebrow
                FROM articles a JOIN categories c ON c.id = a.category_id
                WHERE a.id = %s
                """,
                (article_id,),
            ).fetchone()
            if not row:
                self._json({"error": "Article not found"}, HTTPStatus.NOT_FOUND)
                return
            sources = conn.execute(
                """
                SELECT publisher, title, url, published_at, source_type
                FROM sources WHERE article_id = %s ORDER BY id
                """,
                (article_id,),
            ).fetchall()
        item = dict(row)
        item["featured"] = bool(item["featured"])
        item["keywords"] = parse_json(item["keywords"], [])
        item["sections"] = parse_json(item.pop("body_json"), [])
        item["sources"] = [dict(source) for source in sources]
        self._json(item)

    def _batch_articles(self, payload: dict) -> None:
        raw_ids = payload.get("ids")
        if not isinstance(raw_ids, list):
            self._json({"error": "ids must be an array"}, HTTPStatus.BAD_REQUEST)
            return
        try:
            article_ids = sorted(
                {
                    int(article_id)
                    for article_id in raw_ids
                    if not isinstance(article_id, bool) and int(article_id) > 0
                }
            )
        except (TypeError, ValueError):
            self._json({"error": "ids contains an invalid value"}, HTTPStatus.BAD_REQUEST)
            return
        if not article_ids or len(article_ids) > 100:
            self._json(
                {"error": "请选择 1 到 100 篇内容"},
                HTTPStatus.BAD_REQUEST,
            )
            return
        action = str(payload.get("action", "")).strip().lower()
        if action not in {"publish", "review", "delete"}:
            self._json({"error": "Unsupported batch action"}, HTTPStatus.BAD_REQUEST)
            return
        if action == "delete" and payload.get("confirm") is not True:
            self._json(
                {"error": "删除操作需要 confirm=true"},
                HTTPStatus.BAD_REQUEST,
            )
            return

        placeholders = ", ".join(["%s"] * len(article_ids))
        timestamp = utc_now()
        with connection() as conn:
            existing = conn.execute(
                f"SELECT id FROM articles WHERE id IN ({placeholders})",
                article_ids,
            ).fetchall()
            existing_ids = [int(row["id"]) for row in existing]
            if not existing_ids:
                self._json({"error": "未找到所选内容"}, HTTPStatus.NOT_FOUND)
                return
            existing_placeholders = ", ".join(["%s"] * len(existing_ids))
            if action == "delete":
                conn.execute(
                    f"""
                    UPDATE research_runs SET output_article_id = NULL
                    WHERE output_article_id IN ({existing_placeholders})
                    """,
                    existing_ids,
                )
                conn.execute(
                    f"""
                    UPDATE crawler_jobs SET article_id = NULL
                    WHERE article_id IN ({existing_placeholders})
                    """,
                    existing_ids,
                )
                conn.execute(
                    f"""
                    UPDATE traffic_events SET article_id = NULL
                    WHERE article_id IN ({existing_placeholders})
                    """,
                    existing_ids,
                )
                conn.execute(
                    f"DELETE FROM articles WHERE id IN ({existing_placeholders})",
                    existing_ids,
                )
            else:
                status = "published" if action == "publish" else "review"
                conn.execute(
                    f"""
                    UPDATE articles SET status = %s, updated_at = %s
                    WHERE id IN ({existing_placeholders})
                    """,
                    [status, timestamp, *existing_ids],
                )
            conn.execute(
                """
                INSERT INTO traffic_events(
                    event_type, visitor_type, agent_name, article_id,
                    occurred_at, metadata
                ) VALUES(%s, 'human', %s, NULL, %s, %s)
                """,
                (
                    f"admin_batch_{action}",
                    self.admin_user.get("username", "administrator"),
                    timestamp,
                    json.dumps(
                        {"articleIds": existing_ids, "count": len(existing_ids)},
                        ensure_ascii=False,
                    ),
                ),
            )
        self._json(
            {
                "ok": True,
                "action": action,
                "count": len(existing_ids),
                "articleIds": existing_ids,
            }
        )

    def _admin_data_sources(self) -> None:
        with connection() as conn:
            sources = conn.execute(
                """
                SELECT ds.*, c.name category_name
                FROM data_sources ds
                JOIN categories c ON c.slug = ds.category_slug
                ORDER BY ds.status, ds.trust_tier, ds.publisher, ds.name
                """
            ).fetchall()
            assignments = conn.execute(
                """
                SELECT asa.source_id, asa.agent_id, asa.enabled, asa.priority,
                       asa.last_selected_at, asa.selection_count,
                       ca.name agent_name, ca.slug agent_slug, ca.kind agent_kind
                FROM agent_source_assignments asa
                JOIN crawler_agents ca ON ca.id = asa.agent_id
                ORDER BY asa.source_id, asa.priority, ca.id
                """
            ).fetchall()
        by_source: dict[int, list[dict]] = {}
        for row in assignments:
            assignment = dict(row)
            by_source.setdefault(int(assignment["source_id"]), []).append(
                assignment
            )
        result = []
        for row in sources:
            item = dict(row)
            item["respect_robots"] = bool(item["respect_robots"])
            item["config"] = parse_json(item.pop("config_json", "{}"), {})
            item["assignments"] = by_source.get(int(item["id"]), [])
            item["agentIds"] = [
                assignment["agent_id"]
                for assignment in item["assignments"]
                if assignment["enabled"]
            ]
            result.append(item)
        self._json(result)

    def _source_values(
        self,
        payload: dict,
        *,
        partial: bool,
    ) -> tuple[dict[str, object], list[int] | None]:
        field_map = {
            "publisher": "publisher",
            "name": "name",
            "url": "url",
            "categorySlug": "category_slug",
            "sourceType": "source_type",
            "ingestionMethod": "ingestion_method",
            "status": "status",
            "trustTier": "trust_tier",
            "maxItems": "max_items",
            "respectRobots": "respect_robots",
            "accessModel": "access_model",
            "secretArn": "secret_arn",
            "notes": "notes",
            "config": "config_json",
        }
        required = {
            "publisher",
            "name",
            "url",
            "categorySlug",
            "sourceType",
            "ingestionMethod",
        }
        if not partial:
            missing = [field for field in required if not str(payload.get(field, "")).strip()]
            if missing:
                raise ValueError(f"缺少必填字段：{', '.join(sorted(missing))}")
        values: dict[str, object] = {}
        for input_name, column in field_map.items():
            if input_name not in payload:
                continue
            value: object = payload[input_name]
            if input_name == "url":
                value = validated_public_https_url(value)
            elif input_name in {
                "publisher",
                "name",
                "categorySlug",
                "sourceType",
                "secretArn",
                "notes",
            }:
                value = str(value or "").strip()
            elif input_name == "ingestionMethod":
                value = str(value or "").strip()
                if value not in SOURCE_METHODS:
                    raise ValueError("不支持的采集方式")
            elif input_name == "status":
                value = str(value or "").strip()
                if value not in SOURCE_STATUSES:
                    raise ValueError("不支持的数据源状态")
            elif input_name == "accessModel":
                value = str(value or "").strip()
                if value not in SOURCE_ACCESS_MODELS:
                    raise ValueError("不支持的访问模式")
            elif input_name == "trustTier":
                value = int(value)
                if not 1 <= value <= 4:
                    raise ValueError("可信等级必须在 1 到 4 之间")
            elif input_name == "maxItems":
                value = int(value)
                if not 1 <= value <= 50:
                    raise ValueError("单次条数必须在 1 到 50 之间")
            elif input_name == "respectRobots":
                if not isinstance(value, bool):
                    raise ValueError("respectRobots 必须是布尔值")
            elif input_name == "config":
                value = json.dumps(
                    normalized_source_config(value),
                    ensure_ascii=False,
                )
            values[column] = value
        secret_arn = str(values.get("secret_arn", ""))
        if secret_arn and not secret_arn.startswith("arn:aws:secretsmanager:"):
            raise ValueError("凭据必须引用 AWS Secrets Manager ARN")
        agent_ids = None
        if "agentIds" in payload:
            if not isinstance(payload["agentIds"], list):
                raise ValueError("agentIds 必须是数组")
            try:
                agent_ids = list(dict.fromkeys(int(item) for item in payload["agentIds"]))
            except (TypeError, ValueError) as error:
                raise ValueError("agentIds 包含无效 Agent ID") from error
            if len(agent_ids) > 50:
                raise ValueError("单个来源最多分配给 50 个 Agent")
        return values, agent_ids

    def _replace_source_assignments(
        self,
        conn,
        source_id: int,
        agent_ids: list[int],
        timestamp: str,
    ) -> None:
        if agent_ids:
            placeholders = ", ".join("%s" for _ in agent_ids)
            existing = conn.execute(
                f"SELECT id FROM crawler_agents WHERE id IN ({placeholders})",
                agent_ids,
            ).fetchall()
            if len(existing) != len(agent_ids):
                raise ValueError("分配列表包含不存在的 Agent")
        conn.execute(
            "DELETE FROM agent_source_assignments WHERE source_id = %s",
            (source_id,),
        )
        for priority, agent_id in enumerate(agent_ids, start=1):
            conn.execute(
                """
                INSERT INTO agent_source_assignments(
                    agent_id, source_id, enabled, priority, created_at, updated_at
                ) VALUES(%s, %s, TRUE, %s, %s, %s)
                """,
                (agent_id, source_id, priority * 10, timestamp, timestamp),
            )

    def _create_data_source(self, payload: dict) -> None:
        try:
            values, agent_ids = self._source_values(payload, partial=False)
        except (TypeError, ValueError) as error:
            self._json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            return
        timestamp = utc_now()
        values.setdefault("status", "active")
        values.setdefault("trust_tier", 1)
        values.setdefault("max_items", 4)
        values.setdefault("respect_robots", True)
        values.setdefault("access_model", "open")
        values.setdefault("secret_arn", "")
        values.setdefault("notes", "")
        values.setdefault("config_json", "{}")
        values["created_at"] = timestamp
        values["updated_at"] = timestamp
        columns = list(values)
        placeholders = ", ".join("%s" for _ in columns)
        try:
            with connection() as conn:
                source_id = conn.execute(
                    f"""
                    INSERT INTO data_sources({", ".join(columns)})
                    VALUES({placeholders})
                    RETURNING id
                    """,
                    [values[column] for column in columns],
                ).fetchone()["id"]
                self._replace_source_assignments(
                    conn,
                    int(source_id),
                    agent_ids or [],
                    timestamp,
                )
        except ValueError as error:
            self._json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            return
        except Exception as error:
            if "unique" in str(error).lower() or "duplicate" in str(error).lower():
                self._json({"error": "该 URL 已存在于数据源注册中心"}, HTTPStatus.CONFLICT)
                return
            raise
        self._json(
            {"ok": True, "sourceId": source_id},
            HTTPStatus.CREATED,
        )

    def _update_data_source(self, source_id: int, payload: dict) -> None:
        try:
            values, agent_ids = self._source_values(payload, partial=True)
        except (TypeError, ValueError) as error:
            self._json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            return
        if not values and agent_ids is None:
            self._json({"error": "没有可更新字段"}, HTTPStatus.BAD_REQUEST)
            return
        timestamp = utc_now()
        try:
            with connection() as conn:
                source = conn.execute(
                    "SELECT id FROM data_sources WHERE id = %s",
                    (source_id,),
                ).fetchone()
                if not source:
                    self._json({"error": "数据源不存在"}, HTTPStatus.NOT_FOUND)
                    return
                if values:
                    values["updated_at"] = timestamp
                    assignments = ", ".join(
                        f"{column} = %s" for column in values
                    )
                    conn.execute(
                        f"UPDATE data_sources SET {assignments} WHERE id = %s",
                        [*values.values(), source_id],
                    )
                if agent_ids is not None:
                    self._replace_source_assignments(
                        conn,
                        source_id,
                        agent_ids,
                        timestamp,
                    )
        except ValueError as error:
            self._json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            return
        except Exception as error:
            if "unique" in str(error).lower() or "duplicate" in str(error).lower():
                self._json({"error": "该 URL 已存在于数据源注册中心"}, HTTPStatus.CONFLICT)
                return
            raise
        self._json({"ok": True, "sourceId": source_id})

    def _delete_data_source(self, source_id: int) -> None:
        with connection() as conn:
            cursor = conn.execute(
                "DELETE FROM data_sources WHERE id = %s",
                (source_id,),
            )
            if cursor.rowcount == 0:
                self._json({"error": "数据源不存在"}, HTTPStatus.NOT_FOUND)
                return
        self._json({"ok": True, "sourceId": source_id})

    def _test_data_source(self, source_id: int) -> None:
        with connection() as conn:
            source = conn.execute(
                """
                SELECT url, access_model, config_json
                FROM data_sources
                WHERE id = %s
                """,
                (source_id,),
            ).fetchone()
        if not source:
            self._json({"error": "数据源不存在"}, HTTPStatus.NOT_FOUND)
            return
        timestamp = utc_now()
        try:
            result = test_data_source_url(
                str(source["url"]),
                config=normalized_source_config(
                    parse_json(source["config_json"], {})
                ),
                access_model=str(source["access_model"]),
            )
            test_status = "success"
            response_status = HTTPStatus.OK
        except (TypeError, ValueError) as error:
            result = {
                "ok": False,
                "statusCode": None,
                "finalUrl": source["url"],
                "message": str(error),
            }
            test_status = "failed"
            response_status = HTTPStatus.BAD_GATEWAY
        with connection() as conn:
            conn.execute(
                """
                UPDATE data_sources
                SET last_tested_at = %s, last_test_status = %s,
                    last_test_message = %s, updated_at = %s
                WHERE id = %s
                """,
                (
                    timestamp,
                    test_status,
                    str(result["message"])[:1000],
                    timestamp,
                    source_id,
                ),
            )
        self._json(
            {"sourceId": source_id, "testedAt": timestamp, **result},
            response_status,
        )

    def _admin_crawlers(self) -> None:
        with connection() as conn:
            rows = conn.execute("SELECT * FROM crawler_agents ORDER BY id").fetchall()
            source_rows = conn.execute(
                """
                SELECT asa.agent_id, ds.id source_id, ds.name, ds.status,
                       ds.access_model
                FROM agent_source_assignments asa
                JOIN data_sources ds ON ds.id = asa.source_id
                WHERE asa.enabled = TRUE
                ORDER BY asa.agent_id, asa.priority, ds.id
                """
            ).fetchall()
        sources_by_agent: dict[int, list[dict]] = {}
        for source in source_rows:
            item = dict(source)
            sources_by_agent.setdefault(int(item["agent_id"]), []).append(item)
        schedule_states = eventbridge_schedule_states(
            [str(row["slug"]) for row in rows]
        )
        result = []
        for row in rows:
            item = dict(row)
            try:
                schedule_details = normalize_crawler_schedule(item["schedule"])
            except ValueError:
                schedule_details = {
                    "schedule": item["schedule"],
                    "eventbridgeExpression": "",
                    "label": item["schedule"],
                }
            item["industries"] = parse_json(item["industries"], [])
            item["config"] = parse_json(item["config_json"], {})
            item["dataSources"] = sources_by_agent.get(int(item["id"]), [])
            item["sourceCount"] = len(
                [
                    source
                    for source in item["dataSources"]
                    if source["status"] == "active"
                ]
            )
            item["scheduleLabel"] = schedule_details["label"]
            item["eventbridge"] = (
                {
                    "scheduleName": f"geo-{item['slug']}",
                    "state": schedule_states.get(
                        f"geo-{item['slug']}", "UNKNOWN"
                    ),
                    "group": SCHEDULER_GROUP,
                    "scheduleExpression": schedule_details[
                        "eventbridgeExpression"
                    ],
                    "timezone": "UTC",
                }
                if USE_AURORA_DATA_API
                else None
            )
            result.append(item)
        self._json(
            result
        )

    def _admin_jobs(self) -> None:
        with connection() as conn:
            rows = conn.execute(
                """
                SELECT j.*, a.name agent_name, a.kind agent_kind
                FROM crawler_jobs j JOIN crawler_agents a ON a.id = j.agent_id
                ORDER BY j.started_at DESC LIMIT 20
                """
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["toolTrace"] = parse_json(item.pop("tool_trace_json", "{}"), {})
            result.append(item)
        self._json(result)

    def _admin_research(self) -> None:
        with connection() as conn:
            runs = conn.execute(
                """
                SELECT r.*, a.name agent_name, a.kind agent_kind,
                       o.title article_title, o.slug article_slug,
                       o.status article_status,
                       CASE
                           WHEN octet_length(o.body_json) <= 20000
                           THEN o.body_json
                           ELSE '[]'
                       END body_json,
                       (
                           SELECT COUNT(*)
                           FROM research_evidence e
                           WHERE e.run_id = r.id
                       ) evidence_count
                FROM research_runs r
                JOIN crawler_agents a ON a.id = r.agent_id
                LEFT JOIN articles o ON o.id = r.output_article_id
                ORDER BY r.started_at DESC LIMIT 20
                """
            ).fetchall()
            result = []
            for run in runs:
                item = dict(run)
                item["analysisProcess"] = parse_json(
                    item.pop("analysis_process_json"), []
                )
                item["toolTrace"] = parse_json(item.pop("tool_trace_json"), {})
                item["verification"] = parse_json(
                    item.pop("verification_json"), {}
                )
                item["sections"] = parse_json(item.pop("body_json"), [])
                item["evidence"] = [
                    dict(evidence)
                    for evidence in conn.execute(
                        """
                        SELECT publisher, title, url, published_at, retrieved_at,
                               source_type, content_excerpt
                        FROM research_evidence
                        WHERE run_id = %s ORDER BY id
                        """,
                        (run["id"],),
                    ).fetchall()
                ]
                result.append(item)
        self._json(result)

    def _admin_research_detail(self, run_id: int) -> None:
        with connection() as conn:
            run = conn.execute(
                """
                SELECT r.*, a.name agent_name, a.kind agent_kind,
                       o.title article_title, o.slug article_slug,
                       o.status article_status,
                       CASE
                           WHEN octet_length(o.body_json) <= 200000
                           THEN o.body_json
                           ELSE '[]'
                       END body_json
                FROM research_runs r
                JOIN crawler_agents a ON a.id = r.agent_id
                LEFT JOIN articles o ON o.id = r.output_article_id
                WHERE r.id = %s
                """,
                (run_id,),
            ).fetchone()
            if not run:
                self._json({"error": "Research run not found"}, HTTPStatus.NOT_FOUND)
                return
            item = dict(run)
            item["analysisProcess"] = parse_json(
                item.pop("analysis_process_json"), []
            )
            item["toolTrace"] = parse_json(item.pop("tool_trace_json"), {})
            item["verification"] = parse_json(item.pop("verification_json"), {})
            item["sections"] = parse_json(item.pop("body_json"), [])
            evidence_rows = [
                dict(evidence)
                for evidence in conn.execute(
                    """
                    SELECT id, publisher, title, url, published_at, retrieved_at,
                           source_type, content_excerpt
                    FROM research_evidence WHERE run_id = %s ORDER BY id
                    """,
                    (run_id,),
                ).fetchall()
            ]
            item["evidence"] = []
            for evidence in evidence_rows:
                payload = conn.execute(
                    (
                        "SELECT "
                        + EVIDENCE_DATA_SQL
                        + " AS data_json "
                        "FROM research_evidence WHERE id = %s"
                    ),
                    (evidence.pop("id"),),
                ).fetchone()
                evidence["data"] = parse_json(
                    payload["data_json"] if payload else "{}", {}
                )
                item["evidence"].append(evidence)
        self._json(item)

    def _admin_events(self) -> None:
        with connection() as conn:
            rows = conn.execute(
                """
                SELECT e.*, a.title article_title
                FROM traffic_events e LEFT JOIN articles a ON a.id = e.article_id
                ORDER BY e.occurred_at DESC LIMIT 30
                """
            ).fetchall()
        self._json(
            [{**dict(row), "metadata": parse_json(row["metadata"], {})} for row in rows]
        )

    def _admin_settings(self) -> None:
        with connection() as conn:
            rows = conn.execute(
                "SELECT key, value, value_type, updated_at FROM app_settings ORDER BY key"
            ).fetchall()
        settings = {}
        for row in rows:
            value: object = row["value"]
            if row["value_type"] == "boolean":
                value = row["value"].lower() == "true"
            elif row["value_type"] == "integer":
                value = int(row["value"])
            settings[row["key"]] = {
                "value": value,
                "type": row["value_type"],
                "updatedAt": row["updated_at"],
            }
        self._json(settings)

    def _create_article(self, payload: dict) -> None:
        title = str(payload.get("title", "")).strip()
        category_slug = str(payload.get("categorySlug", "")).strip()
        author = str(payload.get("author", "")).strip()
        dek = str(payload.get("dek", "")).strip()
        if not all((title, category_slug, author, dek)):
            self._json(
                {"error": "title, categorySlug, author and dek are required"},
                HTTPStatus.BAD_REQUEST,
            )
            return
        requested_slug = str(payload.get("slug", "")).strip().lower()
        slug = re.sub(r"[^a-z0-9-]+", "-", requested_slug).strip("-")
        if not slug:
            slug = f"research-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
        status = payload.get("status", "draft")
        if status not in {"draft", "review", "published"}:
            status = "draft"
        now = utc_now()
        summary = str(payload.get("summary") or dek)
        sections = payload.get("sections")
        if not isinstance(sections, list) or not sections:
            sections = [
                {
                    "type": "lead",
                    "heading": "研究摘要",
                    "paragraphs": [summary],
                }
            ]
        with connection() as conn:
            category = conn.execute(
                "SELECT id FROM categories WHERE slug = %s", (category_slug,)
            ).fetchone()
            if not category:
                self._json({"error": "Category not found"}, HTTPStatus.BAD_REQUEST)
                return
            existing = conn.execute(
                "SELECT id FROM articles WHERE slug = %s", (slug,)
            ).fetchone()
            if existing:
                self._json({"error": "Slug already exists"}, HTTPStatus.CONFLICT)
                return
            article_id = conn.execute(
                """
                INSERT INTO articles(
                    category_id, slug, title, dek, summary, author, author_role,
                    read_minutes, published_at, updated_at, status, featured,
                    hero_style, authority_score, citation_count, access_model,
                    agent_price, keywords, body_json
                ) VALUES(
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, FALSE,
                    %s, %s, 0, %s, %s, %s, %s
                )
                RETURNING id
                """,
                (
                    category["id"],
                    slug,
                    title,
                    dek,
                    summary,
                    author,
                    str(payload.get("authorRole") or "研究分析师"),
                    int(payload.get("readMinutes") or 6),
                    now,
                    now,
                    status,
                    str(payload.get("heroStyle") or "evidence"),
                    int(payload.get("authorityScore") or 70),
                    str(payload.get("accessModel") or "open"),
                    float(payload.get("agentPrice") or 0),
                    json.dumps(payload.get("keywords") or [], ensure_ascii=False),
                    json.dumps(sections, ensure_ascii=False),
                ),
            ).fetchone()["id"]
        self._json(
            {"ok": True, "articleId": article_id, "slug": slug, "status": status},
            HTTPStatus.CREATED,
        )

    def _update_article(self, article_id: int, payload: dict) -> None:
        allowed = {"status", "featured", "access_model", "agent_price"}
        updates = {key: value for key, value in payload.items() if key in allowed}
        if not updates:
            self._json({"error": "No supported fields"}, HTTPStatus.BAD_REQUEST)
            return
        updates["updated_at"] = utc_now()
        columns = ", ".join(f"{key} = %s" for key in updates)
        with connection() as conn:
            cursor = conn.execute(
                f"UPDATE articles SET {columns} WHERE id = %s",
                [*updates.values(), article_id],
            )
            if cursor.rowcount == 0:
                self._json({"error": "Article not found"}, HTTPStatus.NOT_FOUND)
                return
        self._json({"ok": True, "articleId": article_id, "updated": updates})

    def _update_crawler(self, crawler_id: int, payload: dict) -> None:
        has_status = "status" in payload
        has_schedule = "schedule" in payload
        if not has_status and not has_schedule:
            self._json(
                {"error": "status or schedule is required"},
                HTTPStatus.BAD_REQUEST,
            )
            return
        status = payload.get("status") if has_status else None
        if has_status and status not in {"running", "paused", "idle"}:
            self._json({"error": "Invalid status"}, HTTPStatus.BAD_REQUEST)
            return
        schedule_details = None
        if has_schedule:
            try:
                schedule_details = normalize_crawler_schedule(
                    payload.get("schedule")
                )
            except ValueError as error:
                self._json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
                return
        with connection() as conn:
            crawler = conn.execute(
                "SELECT slug, status, schedule FROM crawler_agents WHERE id = %s",
                (crawler_id,),
            ).fetchone()
            if not crawler:
                self._json({"error": "Crawler not found"}, HTTPStatus.NOT_FOUND)
                return
        try:
            scheduler_result = sync_eventbridge_schedule(
                crawler["slug"],
                status=status if has_status else None,
                schedule_expression=(
                    schedule_details["eventbridgeExpression"]
                    if schedule_details
                    else None
                ),
            )
        except Exception as error:
            self._json(
                {
                    "error": "EventBridge schedule update failed",
                    "detail": str(error),
                },
                HTTPStatus.BAD_GATEWAY,
            )
            return
        next_status = status if has_status else crawler["status"]
        next_schedule = (
            schedule_details["schedule"]
            if schedule_details
            else crawler["schedule"]
        )
        with connection() as conn:
            cursor = conn.execute(
                """
                UPDATE crawler_agents SET status = %s, schedule = %s
                WHERE id = %s
                """,
                (next_status, next_schedule, crawler_id),
            )
            if cursor.rowcount == 0:
                self._json({"error": "Crawler not found"}, HTTPStatus.NOT_FOUND)
                return
        self._json(
            {
                "ok": True,
                "crawlerId": crawler_id,
                "status": next_status,
                "schedule": next_schedule,
                "scheduleLabel": (
                    schedule_details["label"]
                    if schedule_details
                    else normalize_crawler_schedule(next_schedule)["label"]
                ),
                "eventbridge": scheduler_result,
                "eventbridgeState": (
                    scheduler_result["state"] if scheduler_result else None
                ),
            }
        )

    def _update_setting(self, key: str, payload: dict) -> None:
        if "value" not in payload:
            self._json({"error": "value is required"}, HTTPStatus.BAD_REQUEST)
            return
        with connection() as conn:
            current = conn.execute(
                "SELECT value_type FROM app_settings WHERE key = %s", (key,)
            ).fetchone()
            if not current:
                self._json({"error": "Setting not found"}, HTTPStatus.NOT_FOUND)
                return
            value = payload["value"]
            if current["value_type"] == "boolean":
                value = "true" if bool(value) else "false"
            elif current["value_type"] == "integer":
                try:
                    value = str(int(value))
                except (TypeError, ValueError):
                    self._json({"error": "Invalid integer"}, HTTPStatus.BAD_REQUEST)
                    return
            else:
                value = str(value)
            conn.execute(
                "UPDATE app_settings SET value = %s, updated_at = %s WHERE key = %s",
                (value, utc_now(), key),
            )
        self._json({"ok": True, "key": key, "value": payload["value"]})

    def _run_crawler(self, crawler_id: int, payload: dict) -> None:
        now = utc_now()
        with connection() as conn:
            crawler = conn.execute(
                "SELECT id, name, slug FROM crawler_agents WHERE id = %s", (crawler_id,)
            ).fetchone()
            if not crawler:
                self._json({"error": "Crawler not found"}, HTTPStatus.NOT_FOUND)
                return
        if USE_AURORA_DATA_API:
            try:
                result = invoke_crawler_bridge(
                    crawler["slug"],
                    allow_payment=bool(payload.get("allowPayment")),
                    force_analysis=bool(payload.get("forceAnalysis")),
                )
            except Exception as error:
                self._json(
                    {"error": "AgentCore invocation failed", "detail": str(error)},
                    HTTPStatus.BAD_GATEWAY,
                )
                return
            runtime_result = result.get("result", {})
            self._json(
                {
                    "ok": True,
                    "jobId": runtime_result.get("jobId"),
                    "dispatchId": runtime_result.get("dispatchId"),
                    "agent": crawler["name"],
                    "message": runtime_result.get(
                        "message", "AgentCore Runtime 已接收后台任务"
                    ),
                    "runtimeSessionId": result.get("runtimeSessionId"),
                },
                HTTPStatus.ACCEPTED,
            )
            return
        with connection() as conn:
            job_id = conn.execute(
                """
                INSERT INTO crawler_jobs(agent_id, status, started_at, documents, message)
                VALUES(%s, 'running', %s, 0, '已写入 PostgreSQL 任务队列')
                RETURNING id
                """,
                (crawler_id, now),
            ).fetchone()["id"]
            conn.execute(
                "UPDATE crawler_agents SET status = 'running', last_run = %s WHERE id = %s",
                (now, crawler_id),
            )
        self._json(
            {
                "ok": True,
                "jobId": job_id,
                "agent": crawler["name"],
                "message": "任务已写入 PostgreSQL 队列",
            },
            HTTPStatus.ACCEPTED,
        )

    def _run_all_crawlers(self) -> None:
        now = utc_now()
        jobs = []
        with connection() as conn:
            crawlers = conn.execute(
                "SELECT id, name, slug FROM crawler_agents WHERE status != 'paused'"
            ).fetchall()
        if USE_AURORA_DATA_API:
            for crawler in crawlers:
                try:
                    result = invoke_crawler_bridge(crawler["slug"], asynchronous=True)
                    jobs.append(
                        {
                            "agent": crawler["name"],
                            "submitted": result["submitted"],
                        }
                    )
                except Exception as error:
                    jobs.append(
                        {
                            "agent": crawler["name"],
                            "submitted": False,
                            "error": str(error),
                        }
                    )
            self._json({"ok": True, "jobs": jobs}, HTTPStatus.ACCEPTED)
            return
        with connection() as conn:
            for crawler in crawlers:
                job_id = conn.execute(
                    """
                    INSERT INTO crawler_jobs(agent_id, status, started_at, documents, message)
                    VALUES(%s, 'running', %s, 0, '批量增量抓取')
                    RETURNING id
                    """,
                    (crawler["id"], now),
                ).fetchone()["id"]
                jobs.append({"jobId": job_id, "agent": crawler["name"]})
            conn.execute(
                "UPDATE crawler_agents SET status = 'running', last_run = %s WHERE status != 'paused'",
                (now,),
            )
        self._json({"ok": True, "jobs": jobs}, HTTPStatus.ACCEPTED)

    def _require_admin(self) -> bool:
        user = self._current_admin()
        if user:
            self.admin_user = user
            return True
        if ALLOW_ADMIN_KEY and secrets.compare_digest(
            self.headers.get("X-Admin-Key", ""),
            ADMIN_KEY,
        ):
            self.admin_user = {
                "id": 0,
                "username": "service-key",
                "display_name": "Service API Key",
                "role": "service",
            }
            return True
        self._json({"error": "Unauthorized"}, HTTPStatus.UNAUTHORIZED)
        return False

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, json.JSONDecodeError):
            return {}

    def _json(
        self,
        payload,
        status: HTTPStatus = HTTPStatus.OK,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if not extra_headers or not any(
            key.lower() == "cache-control" for key in extra_headers
        ):
            self.send_header("Cache-Control", "no-store")
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_instructions(self, instructions) -> None:
        payload = instructions.body
        if instructions.is_html:
            body = (
                payload.encode("utf-8")
                if isinstance(payload, str)
                else bytes(payload or b"")
            )
            content_type = "text/html; charset=utf-8"
        else:
            body = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
            content_type = "application/json; charset=utf-8"
        self.send_response(instructions.status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in instructions.headers.items():
            if key.lower() not in {"content-type", "content-length"}:
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[geo-api] {self.address_string()} - {fmt % args}")


init_db()
