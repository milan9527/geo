from __future__ import annotations

import json
import os
import re
import secrets
from http.cookies import SimpleCookie
from datetime import date, datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

import boto3

from .auth import (
    generate_session_token,
    hash_password,
    session_token_hash,
    verify_password,
)
from .database import USE_AURORA_DATA_API, connection, init_db, utc_now


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
AGENT_PATTERNS = {
    "OpenAI Crawler": ("gptbot", "chatgpt-user", "openai"),
    "ClaudeBot": ("claudebot", "claude-web"),
    "PerplexityBot": ("perplexitybot", "perplexity-user"),
    "Google-Extended": ("google-extended", "gemini"),
    "Amazonbot": ("amazonbot",),
    "Common Crawl": ("ccbot",),
}


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


def sync_eventbridge_schedule(slug: str, status: str) -> str | None:
    if not USE_AURORA_DATA_API:
        return None
    scheduler = boto3.client("scheduler", region_name=AWS_REGION)
    name = f"geo-{slug}"
    current = scheduler.get_schedule(Name=name, GroupName=SCHEDULER_GROUP)
    request = {
        "Name": name,
        "GroupName": SCHEDULER_GROUP,
        "ScheduleExpression": current["ScheduleExpression"],
        "FlexibleTimeWindow": current["FlexibleTimeWindow"],
        "Target": current["Target"],
        "State": "DISABLED" if status == "paused" else "ENABLED",
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
    return request["State"]


def invoke_crawler_bridge(slug: str, *, asynchronous: bool = False) -> dict:
    client = boto3.client("lambda", region_name=AWS_REGION)
    response = client.invoke(
        FunctionName=SCHEDULER_BRIDGE_FUNCTION,
        InvocationType="Event" if asynchronous else "RequestResponse",
        Payload=json.dumps(
            {
                "crawlerSlug": slug,
                "scheduledTime": "admin-manual",
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
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Admin-Key")
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
        if path.startswith("/agent/v1/articles/"):
            self._agent_article(path.split("/")[-1])
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
        if path == "/api/admin/crawlers":
            if not self._require_admin():
                return
            self._admin_crawlers()
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
        crawler_match = re.fullmatch(r"/api/admin/crawlers/(\d+)/run", path)
        if crawler_match:
            if not self._require_admin():
                return
            self._run_crawler(int(crawler_match.group(1)))
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
        article_match = re.fullmatch(r"/api/admin/articles/(\d+)", path)
        if article_match:
            self._update_article(int(article_match.group(1)), payload)
            return
        crawler_match = re.fullmatch(r"/api/admin/crawlers/(\d+)", path)
        if crawler_match:
            self._update_crawler(int(crawler_match.group(1)), payload)
            return
        setting_match = re.fullmatch(r"/api/admin/settings/([a-z0-9_]+)", path)
        if setting_match:
            self._update_setting(setting_match.group(1), payload)
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

    def _agent_article(self, slug: str) -> None:
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
            conn.execute(
                """
                INSERT INTO traffic_events(event_type, visitor_type, agent_name, article_id, occurred_at, metadata)
                VALUES('agent_view', 'agent', %s, %s, %s, %s)
                """,
                (
                    identify_visitor(self.headers.get("User-Agent", ""))[1] or "Machine client",
                    row["id"],
                    utc_now(),
                    json.dumps({"endpoint": "agent"}, ensure_ascii=False),
                ),
            )
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
                "price": row["agent_price"],
                "currency": "USDC",
                "paymentProtocol": "x402" if row["agent_price"] else None,
            },
            "contentPolicy": {
                "citationAllowed": True,
                "attributionRequired": True,
                "trainingUse": "contact publisher",
            },
        }
        self._json(payload, extra_headers={"X-Robots-Tag": "index, follow"})

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
        days = {"7d": 7, "30d": 30, "90d": 90}.get(query.get("range", ["30d"])[0], 30)
        start = (date.today() - timedelta(days=days - 1)).isoformat()
        previous_start = (date.today() - timedelta(days=days * 2 - 1)).isoformat()
        with connection() as conn:
            daily = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM analytics_daily WHERE day >= %s ORDER BY day", (start,)
                ).fetchall()
            ]
            current = conn.execute(
                """
                SELECT SUM(human_views) human, SUM(agent_views) agent,
                       SUM(citations) citations, SUM(clicks) clicks,
                       SUM(payments) payments, SUM(revenue) revenue
                FROM analytics_daily WHERE day >= %s
                """,
                (start,),
            ).fetchone()
            previous = conn.execute(
                """
                SELECT SUM(human_views) human, SUM(agent_views) agent,
                       SUM(citations) citations, SUM(clicks) clicks,
                       SUM(payments) payments, SUM(revenue) revenue
                FROM analytics_daily WHERE day >= %s AND day < %s
                """,
                (previous_start, start),
            ).fetchone()
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
            sources = [
                {"name": name, "value": value}
                for name, value in [
                    ("OpenAI Crawler", 31.4),
                    ("ClaudeBot", 24.8),
                    ("PerplexityBot", 18.2),
                    ("Google-Extended", 14.6),
                    ("其他 Agent", 11.0),
                ]
            ]

        def growth(key: str) -> float:
            current_value = current[key] or 0
            previous_value = previous[key] or 0
            return round((current_value - previous_value) / previous_value * 100, 1) if previous_value else 0

        self._json(
            {
                "range": days,
                "summary": {
                    "humanViews": current["human"] or 0,
                    "agentViews": current["agent"] or 0,
                    "citations": current["citations"] or 0,
                    "clicks": current["clicks"] or 0,
                    "payments": current["payments"] or 0,
                    "revenue": round(current["revenue"] or 0, 2),
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

    def _admin_crawlers(self) -> None:
        with connection() as conn:
            rows = conn.execute("SELECT * FROM crawler_agents ORDER BY id").fetchall()
        self._json(
            [
                {
                    **dict(row),
                    "industries": parse_json(row["industries"], []),
                    "config": parse_json(row["config_json"], {}),
                    "eventbridge": {
                        "scheduleName": f"geo-{row['slug']}",
                        "state": "DISABLED" if row["status"] == "paused" else "ENABLED",
                        "group": SCHEDULER_GROUP,
                    }
                    if USE_AURORA_DATA_API
                    else None,
                }
                for row in rows
            ]
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
        self._json([dict(row) for row in rows])

    def _admin_research(self) -> None:
        with connection() as conn:
            runs = conn.execute(
                """
                SELECT r.*, a.name agent_name, a.kind agent_kind,
                       o.title article_title, o.slug article_slug,
                       o.status article_status, o.body_json,
                       COUNT(e.id) evidence_count
                FROM research_runs r
                JOIN crawler_agents a ON a.id = r.agent_id
                LEFT JOIN articles o ON o.id = r.output_article_id
                LEFT JOIN research_evidence e ON e.run_id = r.id
                GROUP BY r.id, a.name, a.kind, o.title, o.slug, o.status, o.body_json
                ORDER BY r.started_at DESC LIMIT 20
                """
            ).fetchall()
            result = []
            for run in runs:
                item = dict(run)
                item["analysisProcess"] = parse_json(
                    item.pop("analysis_process_json"), []
                )
                item["sections"] = parse_json(item.pop("body_json"), [])
                item["evidence"] = [
                    dict(evidence)
                    for evidence in conn.execute(
                        """
                        SELECT publisher, title, url, published_at, retrieved_at,
                               source_type, content_excerpt, data_json
                        FROM research_evidence
                        WHERE run_id = %s ORDER BY id
                        """,
                        (run["id"],),
                    ).fetchall()
                ]
                for evidence in item["evidence"]:
                    evidence["data"] = parse_json(evidence.pop("data_json"), {})
                result.append(item)
        self._json(result)

    def _admin_research_detail(self, run_id: int) -> None:
        with connection() as conn:
            run = conn.execute(
                """
                SELECT r.*, a.name agent_name, a.kind agent_kind,
                       o.title article_title, o.slug article_slug,
                       o.status article_status, o.body_json
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
            item["sections"] = parse_json(item.pop("body_json"), [])
            item["evidence"] = [
                dict(evidence)
                for evidence in conn.execute(
                    """
                    SELECT publisher, title, url, published_at, retrieved_at,
                           source_type, content_excerpt, data_json
                    FROM research_evidence WHERE run_id = %s ORDER BY id
                    """,
                    (run_id,),
                ).fetchall()
            ]
            for evidence in item["evidence"]:
                evidence["data"] = parse_json(evidence.pop("data_json"), {})
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
        status = payload.get("status")
        if status not in {"running", "paused", "idle"}:
            self._json({"error": "Invalid status"}, HTTPStatus.BAD_REQUEST)
            return
        with connection() as conn:
            crawler = conn.execute(
                "SELECT slug FROM crawler_agents WHERE id = %s", (crawler_id,)
            ).fetchone()
            if not crawler:
                self._json({"error": "Crawler not found"}, HTTPStatus.NOT_FOUND)
                return
        try:
            scheduler_state = sync_eventbridge_schedule(crawler["slug"], status)
        except Exception as error:
            self._json(
                {
                    "error": "EventBridge schedule update failed",
                    "detail": str(error),
                },
                HTTPStatus.BAD_GATEWAY,
            )
            return
        with connection() as conn:
            cursor = conn.execute(
                "UPDATE crawler_agents SET status = %s WHERE id = %s", (status, crawler_id)
            )
            if cursor.rowcount == 0:
                self._json({"error": "Crawler not found"}, HTTPStatus.NOT_FOUND)
                return
        self._json(
            {
                "ok": True,
                "crawlerId": crawler_id,
                "status": status,
                "eventbridgeState": scheduler_state,
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

    def _run_crawler(self, crawler_id: int) -> None:
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
                result = invoke_crawler_bridge(crawler["slug"])
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
                    "agent": crawler["name"],
                    "message": runtime_result.get("message", "AgentCore 任务已完成"),
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
        self.send_header("Cache-Control", "no-store")
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[geo-api] {self.address_string()} - {fmt % args}")


init_db()
