from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import os
import re
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.request import Request, urlopen
from xml.etree import ElementTree

import boto3
from botocore.exceptions import ClientError
from crawler_tools import (
    build_codex_request,
    codex_request,
    run_browser_crawler,
    run_generated_crawler,
    run_x402_crawler,
)


REGION = os.environ.get("AWS_REGION", "us-east-1")
MODEL_ID = os.environ["BEDROCK_MODEL_ID"]
AURORA_RESOURCE_ARN = os.environ["AURORA_RESOURCE_ARN"]
AURORA_SECRET_ARN = os.environ["AURORA_SECRET_ARN"]
AURORA_DATABASE = os.environ.get("AURORA_DATABASE", "geo")
BROWSER_ID = os.environ.get("AGENTCORE_BROWSER_ID", "")
CODE_INTERPRETER_ID = os.environ.get("AGENTCORE_CODE_INTERPRETER_ID", "")
AUTO_PUBLISH_RESEARCH = os.environ.get(
    "RESEARCH_AUTO_PUBLISH", "true"
).lower() in {"1", "true", "yes"}
CRAWLER_CONTACT_URL = os.environ.get(
    "CRAWLER_CONTACT_URL",
    "https://d1tsbnft7iv51.cloudfront.net/",
)

bedrock = boto3.client("bedrock-runtime", region_name=REGION)
rds_data = boto3.client("rds-data", region_name=REGION)
agentcore = boto3.client("bedrock-agentcore", region_name=REGION)

SOURCE_PROFILES = {
    "research-coder": {
        "category": "ai",
        "crawlStyle": "research",
        "topic": "AI 基础模型、Agent 与云端 AI 基础设施的最新产业进展",
        "sources": [
            {
                "publisher": "OpenAI",
                "url": "https://openai.com/news/rss.xml",
                "sourceType": "官方发布",
            },
            {
                "publisher": "AWS Machine Learning Blog",
                "url": "https://aws.amazon.com/blogs/machine-learning/feed/",
                "sourceType": "官方技术博客",
            },
        ],
    },
    "render-scout": {
        "category": "commerce",
        "crawlStyle": "browser-rendered",
        "topic": "电商、支付与媒体平台采用 AI 和 Agent Commerce 的最新变化",
        "sources": [
            {
                "publisher": "Stripe",
                "url": "https://stripe.com/blog",
                "sourceType": "动态官方页面",
                "maxItems": 3,
            },
            {
                "publisher": "Shopify",
                "url": "https://www.shopify.com/news",
                "sourceType": "动态官方新闻",
                "maxItems": 3,
            },
        ],
    },
    "market-signal": {
        "category": "finance",
        "crawlStyle": "financial-timeseries",
        "topic": "科技股、利率与 AI 资本开支周期的最新市场研判",
        "sources": [
            {
                "publisher": "Federal Reserve Bank of St. Louis",
                "url": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=NASDAQCOM",
                "sourceType": "官方市场数据",
            },
            {
                "publisher": "Federal Reserve Bank of St. Louis",
                "url": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10",
                "sourceType": "官方利率数据",
            },
            {
                "publisher": "Federal Reserve Bank of St. Louis",
                "url": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=FEDFUNDS",
                "sourceType": "官方宏观数据",
            },
        ],
    },
    "evidence-verifier": {
        "category": "agent",
        "crawlStyle": "evidence-verification",
        "topic": "Agent 运行时、工具执行、证据治理与机器身份的技术进展",
        "sources": [
            {
                "publisher": "AWS",
                "url": "https://aws.amazon.com/bedrock/agentcore/",
                "sourceType": "官方产品资料",
            },
            {
                "publisher": "OpenAI",
                "url": "https://openai.com/news/rss.xml",
                "sourceType": "官方发布",
            },
        ],
    },
    "cloud-release-watch": {
        "category": "cloud",
        "crawlStyle": "browser-rendered",
        "topic": "主要云计算厂商最新服务发布及其对企业 AI 架构的影响",
        "sources": [
            {
                "publisher": "AWS What's New",
                "url": "https://aws.amazon.com/new/",
                "sourceType": "动态官方发布页面",
                "maxItems": 3,
            },
            {
                "publisher": "Google Cloud",
                "url": "https://cloud.google.com/release-notes",
                "sourceType": "动态官方发布说明",
                "maxItems": 3,
            },
        ],
    },
    "commerce-feed-miner": {
        "category": "commerce",
        "crawlStyle": "research",
        "topic": "电商平台、支付基础设施与 AI 购物 Agent 的商业模式变化",
        "sources": [
            {
                "publisher": "Stripe",
                "url": "https://stripe.com/blog/feed.rss",
                "sourceType": "官方发布",
            },
            {
                "publisher": "Shopify",
                "url": "https://www.shopify.com/news",
                "sourceType": "官方新闻",
            },
        ],
        "paidSources": [
            {
                "publisher": "Aperture GEO",
                "title": "Agent买方进入定价议程",
                "url": (
                    "https://d1tsbnft7iv51.cloudfront.net/agent/v1/articles/"
                    "commerce-research-20260903-0927-212/paid"
                ),
                "sourceType": "本站 x402 付费深度分析",
            }
        ],
    },
}


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def database_status() -> dict[str, Any]:
    response = rds_data.execute_statement(
        resourceArn=AURORA_RESOURCE_ARN,
        secretArn=AURORA_SECRET_ARN,
        database=AURORA_DATABASE,
        sql="SELECT COUNT(*) AS article_count FROM articles",
        includeResultMetadata=True,
    )
    count = response["records"][0][0].get("longValue", 0)
    return {"engine": "Aurora PostgreSQL", "articleCount": count}


def data_api_value(value: Any) -> dict[str, Any]:
    if value is None:
        return {"isNull": True}
    if isinstance(value, bool):
        return {"booleanValue": value}
    if isinstance(value, int):
        return {"longValue": value}
    if isinstance(value, float):
        return {"doubleValue": value}
    return {"stringValue": str(value)}


def data_api_field(field: dict[str, Any]) -> Any:
    if field.get("isNull"):
        return None
    for key in ("stringValue", "longValue", "doubleValue", "booleanValue", "blobValue"):
        if key in field:
            return field[key]
    return None


def execute_sql(
    statement: str,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    request: dict[str, Any] = {
        "resourceArn": AURORA_RESOURCE_ARN,
        "secretArn": AURORA_SECRET_ARN,
        "database": AURORA_DATABASE,
        "sql": statement,
        "includeResultMetadata": True,
    }
    if parameters:
        request["parameters"] = [
            {"name": name, "value": data_api_value(value)}
            for name, value in parameters.items()
        ]
    delays = (1, 2, 3, 4, 5)
    for attempt, delay in enumerate(delays, start=1):
        try:
            return rds_data.execute_statement(**request)
        except ClientError as error:
            code = error.response.get("Error", {}).get("Code")
            if code not in {"DatabaseResumingException", "DatabaseUnavailableException"}:
                raise
            if attempt == len(delays):
                raise
            time.sleep(delay)
    raise RuntimeError("Aurora Data API retry exhausted")


def rows(response: dict[str, Any]) -> list[dict[str, Any]]:
    names = [column["name"] for column in response.get("columnMetadata", [])]
    return [
        {name: data_api_field(field) for name, field in zip(names, record)}
        for record in response.get("records", [])
    ]


def strip_markup(value: str) -> str:
    value = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", value)
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def xml_text(node: ElementTree.Element, names: tuple[str, ...]) -> str:
    for child in node.iter():
        local_name = child.tag.rsplit("}", 1)[-1].lower()
        if local_name in names and child.text:
            return child.text.strip()
    return ""


def fetch_source(source: dict[str, str]) -> list[dict[str, Any]]:
    request = Request(
        source["url"],
        headers={
            "User-Agent": f"ApertureGEOResearchBot/1.0 (+{CRAWLER_CONTACT_URL})",
            "Accept": "application/rss+xml, application/xml, text/xml, text/csv, text/html",
        },
    )
    with urlopen(request, timeout=20) as response:
        raw = response.read(2_500_000)
        content_type = response.headers.get("Content-Type", "").lower()
        final_url = response.geturl()
    text = raw.decode("utf-8", errors="replace")
    retrieved_at = now()

    if "csv" in content_type or final_url.endswith(".csv"):
        records = list(csv.reader(io.StringIO(text)))
        values: list[tuple[str, float]] = []
        for record in records[1:]:
            if len(record) < 2 or record[1] in {"", "."}:
                continue
            try:
                values.append((record[0], float(record[1])))
            except ValueError:
                continue
        recent = values[-30:]
        if not recent:
            return []
        first_date, first_value = recent[0]
        last_date, last_value = recent[-1]
        change = ((last_value - first_value) / first_value * 100) if first_value else 0
        series_id = final_url.split("id=", 1)[-1].split("&", 1)[0]
        excerpt = (
            f"{series_id} 最新值 {last_value:g}（{last_date}）；"
            f"过去 {len(recent)} 个有效观测由 {first_value:g} 变为 {last_value:g}，"
            f"区间变化 {change:.2f}%。"
        )
        return [
            {
                "publisher": source["publisher"],
                "title": f"FRED {series_id} 时间序列",
                "url": final_url,
                "publishedAt": last_date,
                "retrievedAt": retrieved_at,
                "sourceType": source["sourceType"],
                "excerpt": excerpt,
                "data": {
                    "seriesId": series_id,
                    "latestDate": last_date,
                    "latestValue": last_value,
                    "startDate": first_date,
                    "startValue": first_value,
                    "changePercent": round(change, 2),
                    "observations": recent[-10:],
                },
            }
        ]

    if "xml" in content_type or "rss" in content_type or text.lstrip().startswith("<?xml"):
        root = ElementTree.fromstring(text)
        entries = [
            node
            for node in root.iter()
            if node.tag.rsplit("}", 1)[-1].lower() in {"item", "entry"}
        ]
        evidence = []
        for entry in entries[:4]:
            title = strip_markup(xml_text(entry, ("title",))) or "未命名官方发布"
            link = xml_text(entry, ("link",))
            if not link:
                for child in entry.iter():
                    if child.tag.rsplit("}", 1)[-1].lower() == "link":
                        link = child.attrib.get("href", "")
                        if link:
                            break
            published = xml_text(
                entry,
                ("pubdate", "published", "updated", "date"),
            )
            excerpt = strip_markup(
                xml_text(entry, ("description", "summary", "content"))
            )[:1800]
            if not excerpt:
                excerpt = title
            evidence.append(
                {
                    "publisher": source["publisher"],
                    "title": title,
                    "url": link or final_url,
                    "publishedAt": published or retrieved_at[:10],
                    "retrievedAt": retrieved_at,
                    "sourceType": source["sourceType"],
                    "excerpt": excerpt,
                    "data": {},
                }
            )
        return evidence

    page_title_match = re.search(r"(?is)<title[^>]*>(.*?)</title>", text)
    page_title = (
        strip_markup(page_title_match.group(1))
        if page_title_match
        else f"{source['publisher']} 官方页面"
    )
    excerpt = strip_markup(text)[:3000]
    return [
        {
            "publisher": source["publisher"],
            "title": page_title,
            "url": final_url,
            "publishedAt": retrieved_at[:10],
            "retrievedAt": retrieved_at,
            "sourceType": source["sourceType"],
            "excerpt": excerpt,
            "data": {},
        }
    ]


def profile_source_hash(profile: dict[str, Any]) -> str:
    payload = {
        "style": profile.get("crawlStyle"),
        "sources": profile.get("sources") or [],
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def load_or_generate_artifact(
    crawler: dict[str, Any],
    profile: dict[str, Any],
) -> dict[str, Any]:
    source_hash = profile_source_hash(profile)
    existing = rows(
        execute_sql(
            """
            SELECT id, thread_id, source_code, test_plan, safety_notes_json,
                   usage_json
            FROM crawler_artifacts
            WHERE agent_id = :agent_id AND source_hash = :source_hash
              AND status = 'active'
            ORDER BY id DESC LIMIT 1
            """,
            {"agent_id": crawler["id"], "source_hash": source_hash},
        )
    )
    if existing:
        item = existing[0]
        return {
            "id": item["id"],
            "threadId": item["thread_id"],
            "sourceCode": item["source_code"],
            "testPlan": item["test_plan"],
            "safetyNotes": json.loads(item["safety_notes_json"] or "[]"),
            "usage": json.loads(item["usage_json"] or "{}"),
            "sourceHash": source_hash,
            "cached": True,
        }

    artifact = codex_request(
        "generate",
        request=build_codex_request(profile),
    )
    created_at = now()
    persisted = rows(
        execute_sql(
            """
            INSERT INTO crawler_artifacts(
                agent_id, source_hash, crawl_style, thread_id, source_code,
                test_plan, safety_notes_json, usage_json, status,
                created_at, updated_at
            ) VALUES(
                :agent_id, :source_hash, :crawl_style, :thread_id, :source_code,
                :test_plan, :safety_notes_json, :usage_json, 'active',
                :created_at, :updated_at
            ) RETURNING id
            """,
            {
                "agent_id": crawler["id"],
                "source_hash": source_hash,
                "crawl_style": profile.get("crawlStyle", "research"),
                "thread_id": artifact["threadId"],
                "source_code": artifact["sourceCode"],
                "test_plan": artifact.get("testPlan", ""),
                "safety_notes_json": json.dumps(
                    artifact.get("safetyNotes") or [], ensure_ascii=False
                ),
                "usage_json": json.dumps(
                    artifact.get("usage") or {}, ensure_ascii=False
                ),
                "created_at": created_at,
                "updated_at": created_at,
            },
        )
    )[0]
    artifact.update(
        {
            "id": persisted["id"],
            "sourceHash": source_hash,
            "cached": False,
        }
    )
    return artifact


def update_artifact(artifact: dict[str, Any]) -> None:
    execute_sql(
        """
        UPDATE crawler_artifacts
        SET thread_id = :thread_id, source_code = :source_code,
            test_plan = :test_plan, safety_notes_json = :safety_notes_json,
            usage_json = :usage_json, updated_at = :updated_at,
            last_used_at = :last_used_at
        WHERE id = :artifact_id
        """,
        {
            "thread_id": artifact["threadId"],
            "source_code": artifact["sourceCode"],
            "test_plan": artifact.get("testPlan", ""),
            "safety_notes_json": json.dumps(
                artifact.get("safetyNotes") or [], ensure_ascii=False
            ),
            "usage_json": json.dumps(artifact.get("usage") or {}, ensure_ascii=False),
            "updated_at": now(),
            "last_used_at": now(),
            "artifact_id": artifact["id"],
        },
    )


def normalize_evidence(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    prioritized = sorted(
        evidence,
        key=lambda item: bool(
            isinstance(item, dict)
            and isinstance(item.get("data"), dict)
            and item["data"].get("paid")
        ),
        reverse=True,
    )
    for item in prioritized[:10]:
        if not isinstance(item, dict) or not item.get("url"):
            continue
        normalized.append(
            {
                "publisher": str(item.get("publisher") or "Unknown publisher")[:300],
                "title": str(item.get("title") or "Untitled source")[:800],
                "url": str(item["url"])[:4000],
                "publishedAt": str(item.get("publishedAt") or now()[:10])[:100],
                "retrievedAt": str(item.get("retrievedAt") or now())[:100],
                "sourceType": str(item.get("sourceType") or "官方来源")[:200],
                "excerpt": str(item.get("excerpt") or "")[:12_000],
                "data": item.get("data") if isinstance(item.get("data"), dict) else {},
            }
        )
    return normalized


def collect_evidence(
    crawler: dict[str, Any],
    payload: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str], dict[str, Any]]:
    slug = str(crawler["slug"])
    profile = SOURCE_PROFILES.get(slug)
    if not profile:
        raise ValueError(f"No source profile configured for {slug}")
    errors: list[str] = []
    session_name = f"{slug}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"

    if crawler["kind"] == "Browser Tool":
        evidence, trace = run_browser_crawler(
            profile,
            session_name=session_name,
        )
    else:
        artifact = load_or_generate_artifact(crawler, profile)
        evidence, trace, final_artifact = run_generated_crawler(
            profile,
            artifact,
            session_name=session_name,
        )
        final_artifact["id"] = artifact["id"]
        update_artifact(final_artifact)
        trace.update(
            {
                "codexThreadId": final_artifact["threadId"],
                "codexArtifactId": artifact["id"],
                "codexArtifactCached": artifact.get("cached", False),
                "codexUsage": final_artifact.get("usage") or {},
            }
        )

    payment_traces: list[dict[str, Any]] = []
    if payload.get("allowPayment"):
        for paid_source in profile.get("paidSources") or []:
            try:
                paid_evidence, payment_trace = run_x402_crawler(
                    paid_source,
                    session_name=session_name,
                )
                evidence.append(paid_evidence)
                payment_traces.append(payment_trace)
            except Exception as error:
                errors.append(
                    f"{paid_source['publisher']}: {type(error).__name__}: {error}"
                )
    if payment_traces:
        trace["payments"] = payment_traces
    return profile, normalize_evidence(evidence), errors, trace


def ensure_research_schema() -> None:
    statements = [
        "ALTER TABLE crawler_jobs ADD COLUMN IF NOT EXISTS research_run_id BIGINT",
        "ALTER TABLE crawler_jobs ADD COLUMN IF NOT EXISTS article_id BIGINT REFERENCES articles(id)",
        """
        CREATE TABLE IF NOT EXISTS research_runs (
            id BIGSERIAL PRIMARY KEY,
            agent_id BIGINT NOT NULL REFERENCES crawler_agents(id),
            job_id BIGINT REFERENCES crawler_jobs(id),
            status TEXT NOT NULL CHECK(status IN ('running', 'completed', 'skipped', 'failed')),
            category_slug TEXT NOT NULL,
            topic TEXT NOT NULL,
            evidence_hash TEXT,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            model_id TEXT,
            analysis_process_json TEXT NOT NULL DEFAULT '[]',
            summary TEXT NOT NULL DEFAULT '',
            output_article_id BIGINT REFERENCES articles(id),
            error_message TEXT NOT NULL DEFAULT '',
            tool_trace_json TEXT NOT NULL DEFAULT '{}',
            verification_status TEXT NOT NULL DEFAULT 'pending',
            verification_json TEXT NOT NULL DEFAULT '{}'
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS research_evidence (
            id BIGSERIAL PRIMARY KEY,
            run_id BIGINT NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
            publisher TEXT NOT NULL,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            published_at TEXT NOT NULL,
            retrieved_at TEXT NOT NULL,
            source_type TEXT NOT NULL,
            content_excerpt TEXT NOT NULL,
            data_json TEXT NOT NULL DEFAULT '{}'
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS crawler_artifacts (
            id BIGSERIAL PRIMARY KEY,
            agent_id BIGINT NOT NULL REFERENCES crawler_agents(id),
            source_hash TEXT NOT NULL,
            crawl_style TEXT NOT NULL,
            thread_id TEXT NOT NULL,
            source_code TEXT NOT NULL,
            test_plan TEXT NOT NULL DEFAULT '',
            safety_notes_json TEXT NOT NULL DEFAULT '[]',
            usage_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_used_at TEXT,
            UNIQUE(agent_id, source_hash)
        )
        """,
        "ALTER TABLE research_runs ADD COLUMN IF NOT EXISTS tool_trace_json TEXT NOT NULL DEFAULT '{}'",
        "ALTER TABLE research_runs ADD COLUMN IF NOT EXISTS verification_status TEXT NOT NULL DEFAULT 'pending'",
        "ALTER TABLE research_runs ADD COLUMN IF NOT EXISTS verification_json TEXT NOT NULL DEFAULT '{}'",
        "ALTER TABLE crawler_jobs ADD COLUMN IF NOT EXISTS tool_trace_json TEXT NOT NULL DEFAULT '{}'",
        "CREATE INDEX IF NOT EXISTS idx_research_runs_started ON research_runs(started_at)",
        "CREATE INDEX IF NOT EXISTS idx_research_evidence_run ON research_evidence(run_id)",
        "CREATE INDEX IF NOT EXISTS idx_crawler_artifacts_agent ON crawler_artifacts(agent_id)",
    ]
    for statement in statements:
        execute_sql(statement)


def parse_research_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("Model did not return a JSON object")
    return json.loads(cleaned[start : end + 1])


def fallback_research(
    text: str,
    profile: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    plain = strip_markup(re.sub(r"[#*_`>-]", " ", text))
    paragraphs = [
        paragraph.strip()
        for paragraph in re.split(r"\n{2,}|(?<=[。！？])\s+", plain)
        if len(paragraph.strip()) > 35
    ]
    while len(paragraphs) < 7:
        paragraphs.append(
            "该判断需要结合后续官方发布和新增数据继续验证，当前结论只适用于本次证据截止时间。"
        )
    evidence_rows = [
        [
            f"[S{index}] {item['publisher']}",
            item["excerpt"][:180],
            "作为事实或数据输入，不单独等同于最终行业判断。",
        ]
        for index, item in enumerate(evidence, start=1)
    ]
    return {
        "title": f"{profile['topic']}：证据驱动研判",
        "dek": "基于最新官方发布与数据，分析事实之间的产业联系、约束与未来观察指标。",
        "summary": " ".join(paragraphs[:2])[:900],
        "authorityScore": min(94, 84 + len(evidence)),
        "keywords": [profile["category"], "行业研究", "官方数据", "GEO"],
        "analysisProcess": [
            {
                "step": "证据采集",
                "method": "抓取官方 RSS、产品资料或时间序列",
                "evidence": f"[S1]-[S{len(evidence)}]",
                "result": f"获得 {len(evidence)} 条可追溯证据。",
            },
            {
                "step": "事实与观点分离",
                "method": "仅将来源原文和数据作为事实层，将因果解释标记为研究判断",
                "evidence": f"[S1]-[S{len(evidence)}]",
                "result": "形成事实、推断和风险三层分析。",
            },
            {
                "step": "产业影响推演",
                "method": "比较短期事件与中期结构性约束",
                "evidence": f"[S1]-[S{len(evidence)}]",
                "result": "提炼竞争影响、二阶效应和未来观察指标。",
            },
        ],
        "sections": [
            {
                "type": "lead",
                "heading": "核心观点",
                "paragraphs": paragraphs[:2],
            },
            {
                "type": "matrix",
                "heading": "数据与证据",
                "headers": ["证据", "观察", "研究含义"],
                "rows": evidence_rows,
            },
            {
                "type": "analysis",
                "heading": "分析过程：从事实到判断",
                "number": "01",
                "paragraphs": paragraphs[2:5],
            },
            {
                "type": "analysis",
                "heading": "专业观点与产业影响",
                "number": "02",
                "paragraphs": paragraphs[4:7],
                "quote": paragraphs[0][:180],
            },
            {
                "type": "outlook",
                "heading": "结论与未来观察",
                "bullets": [
                    paragraphs[-3][:260],
                    paragraphs[-2][:260],
                    paragraphs[-1][:260],
                ],
            },
        ],
    }


def normalize_research_output(
    output: dict[str, Any],
    profile: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    fallback = fallback_research("", profile, evidence)
    normalized = output if isinstance(output, dict) else {}

    for field in ("title", "dek", "summary"):
        if not str(normalized.get(field) or "").strip():
            normalized[field] = fallback[field]

    analysis_process = normalized.get("analysisProcess")
    if not isinstance(analysis_process, list) or not analysis_process:
        normalized["analysisProcess"] = fallback["analysisProcess"]

    sections = normalized.get("sections")
    if not isinstance(sections, list):
        sections = []
    sections = [section for section in sections if isinstance(section, dict)]

    required_sections = {
        "lead": fallback["sections"][0],
        "matrix": fallback["sections"][1],
        "analysis-process": fallback["sections"][2],
        "industry-analysis": fallback["sections"][3],
    }
    headings = [str(section.get("heading") or "") for section in sections]
    types = [str(section.get("type") or "") for section in sections]
    if "lead" not in types and not any("核心观点" in heading for heading in headings):
        sections.insert(0, required_sections["lead"])
    if "matrix" not in types and not any("数据与证据" in heading for heading in headings):
        sections.append(required_sections["matrix"])
    if not any("分析过程" in heading for heading in headings):
        sections.append(required_sections["analysis-process"])
    if not any("专业观点" in heading or "产业影响" in heading for heading in headings):
        sections.append(required_sections["industry-analysis"])

    has_outlook = any(
        section.get("type") == "outlook"
        or "结论" in str(section.get("heading") or "")
        or "未来观察" in str(section.get("heading") or "")
        for section in sections
    )
    if not has_outlook:
        source_labels = [
            str(item.get("data", {}).get("seriesId") or item.get("title") or "")
            for item in evidence[:4]
        ]
        source_labels = [label for label in source_labels if label]
        evidence_cutoff = max(
            (str(item.get("publishedAt") or "") for item in evidence),
            default=now()[:10],
        )
        conclusion = str(normalized.get("summary") or fallback["summary"]).strip()
        bullets = [
            f"结论：{conclusion[:520]}",
            (
                "未来观察指标：持续跟踪 "
                + "、".join(source_labels)
                + " 的新数据与官方发布，并检验当前判断是否得到新增证据支持。"
            ),
            (
                f"风险与边界：本次判断只基于截至 {evidence_cutoff} 的 "
                f"{len(evidence)} 条证据；来源口径、发布时间或样本窗口不一致时，"
                "不得将相关性解释为已证实的因果关系。"
            ),
        ]
        if profile.get("category") == "finance":
            bullets.append("声明：本文为行业研究与市场信息分析，不构成投资建议。")
        sections.append(
            {
                "type": "outlook",
                "heading": "结论与未来观察",
                "bullets": bullets,
            }
        )

    normalized["sections"] = sections
    normalized["authorityScore"] = max(
        80, min(98, int(normalized.get("authorityScore") or fallback["authorityScore"]))
    )
    if not isinstance(normalized.get("keywords"), list):
        normalized["keywords"] = fallback["keywords"]
    return normalized


def generate_deep_research(
    profile: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    evidence_text = "\n\n".join(
        (
            f"[S{index}] {item['publisher']} | {item['title']} | "
            f"{item['publishedAt']} | {item['url']}\n{item['excerpt']}"
        )
        for index, item in enumerate(evidence, start=1)
    )
    prompt = f"""
你是 Aperture Intelligence 的资深行业研究负责人。请基于下列一手证据撰写深度中文行业分析。
这不是新闻汇总。必须解释数据和事件之间的因果关系、产业约束、竞争影响和二阶效应。

研究主题：{profile['topic']}
数据截止：{now()}

硬性规则：
1. 只能使用给定证据中的事实和数字，不得虚构来源、日期、产品或市场数据。
2. 每个重要事实后用 [S1] 形式标出证据编号。
3. 明确区分“事实”“分析判断”“风险/不确定性”。
4. 必须包含：核心观点、数据与证据、分析过程、专业观点、结论、未来观察指标。
5. 金融内容必须声明“不构成投资建议”。
6. 输出严格 JSON，不要 Markdown 代码围栏。

JSON 结构：
{{
  "title": "有判断力而非事件罗列的标题",
  "dek": "一句话说明核心变化与重要性",
  "summary": "150-250字研究摘要",
  "authorityScore": 85到98的整数,
  "keywords": ["关键词"],
  "analysisProcess": [
    {{"step":"问题定义","method":"使用的方法","evidence":"使用哪些[S#]","result":"中间判断"}}
  ],
  "sections": [
    {{"type":"lead","heading":"核心观点","paragraphs":["至少两段"]}},
    {{"type":"matrix","heading":"数据与证据","headers":["证据","观察","研究含义"],"rows":[["[S1] ...","...","..."]]}},
    {{"type":"analysis","heading":"分析过程：从事实到判断","number":"01","paragraphs":["至少三段"]}},
    {{"type":"analysis","heading":"专业观点与产业影响","number":"02","paragraphs":["至少三段"],"quote":"一句可引用判断"}},
    {{"type":"outlook","heading":"结论与未来观察","bullets":["结论","指标","风险"]}}
  ]
}}

证据：
{evidence_text}
"""
    response = bedrock.converse(
        modelId=MODEL_ID,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": 5600},
    )
    text = "".join(
        part.get("text", "")
        for part in response["output"]["message"]["content"]
        if "text" in part
    )
    usage = response.get("usage", {})
    try:
        return normalize_research_output(
            parse_research_json(text), profile, evidence
        ), usage
    except (json.JSONDecodeError, ValueError):
        repair = bedrock.converse(
            modelId=MODEL_ID,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "text": (
                                "修复下面的 JSON，使其成为严格合法的 JSON。"
                                "不得删除分析内容，不要添加 Markdown 代码围栏，只返回 JSON：\n\n"
                                + text
                            )
                        }
                    ],
                }
            ],
            inferenceConfig={"maxTokens": 4000},
        )
        repaired_text = "".join(
            part.get("text", "")
            for part in repair["output"]["message"]["content"]
            if "text" in part
        )
        for key, value in repair.get("usage", {}).items():
            usage[key] = usage.get(key, 0) + value
        try:
            return normalize_research_output(
                parse_research_json(repaired_text), profile, evidence
            ), usage
        except (json.JSONDecodeError, ValueError):
            return normalize_research_output(
                fallback_research(text, profile, evidence), profile, evidence
            ), usage


def verify_research_output(
    output: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    audited_output = {
        key: value
        for key, value in output.items()
        if key not in {"authorityScore", "keywords"}
    }
    evidence_text = "\n".join(
        f"[S{index}] {item['publisher']} | {item['title']} | "
        f"{item['publishedAt']} | {item['excerpt'][:1200]}"
        for index, item in enumerate(evidence, start=1)
    )
    prompt = f"""
你是独立证据审计员。核验研究稿中的事实、数字、日期与给定证据是否一致。
不要评价文风，只检查可证实性、引用映射、时间边界和因果表述。

输出严格 JSON：
{{
  "status":"verified 或 needs_review",
  "score":0到100整数,
  "supportedClaims":整数,
  "unsupportedClaims":["最多8条，每条不超过180字"],
  "citationIssues":["最多8条，每条不超过180字"],
  "causalityRisks":["最多8条，每条不超过180字"],
  "notes":"不超过300字的审计结论"
}}

证据：
{evidence_text}

研究稿：
{json.dumps(audited_output, ensure_ascii=False)[:48_000]}
"""
    response = bedrock.converse(
        modelId=MODEL_ID,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": 3200},
    )
    text = "".join(
        part.get("text", "")
        for part in response["output"]["message"]["content"]
        if "text" in part
    )
    try:
        verification = parse_research_json(text)
    except (ValueError, json.JSONDecodeError):
        repair_prompt = f"""
把下面被截断或格式错误的证据审计压缩成一个合法 JSON 对象。不要增加新判断。
数组各保留最重要的最多8条，每条不超过180字，notes不超过300字。
只输出以下结构，不要 Markdown：
{{
  "status":"verified 或 needs_review",
  "score":0到100整数,
  "supportedClaims":整数,
  "unsupportedClaims":[],
  "citationIssues":[],
  "causalityRisks":[],
  "notes":""
}}

原始审计：
{text[:14_000]}
"""
        repair = bedrock.converse(
            modelId=MODEL_ID,
            messages=[{"role": "user", "content": [{"text": repair_prompt}]}],
            inferenceConfig={"maxTokens": 1800},
        )
        repair_text = "".join(
            part.get("text", "")
            for part in repair["output"]["message"]["content"]
            if "text" in part
        )
        try:
            verification = parse_research_json(repair_text)
        except (ValueError, json.JSONDecodeError):
            return {
                "status": "needs_review",
                "score": 0,
                "supportedClaims": 0,
                "unsupportedClaims": ["证据审计模型未返回合法 JSON"],
                "citationIssues": [],
                "causalityRisks": [],
                "notes": repair_text[:1000] or text[:1000],
                "usage": {
                    key: response.get("usage", {}).get(key, 0)
                    + repair.get("usage", {}).get(key, 0)
                    for key in set(response.get("usage", {}))
                    | set(repair.get("usage", {}))
                },
            }
        response_usage = {
            key: response.get("usage", {}).get(key, 0)
            + repair.get("usage", {}).get(key, 0)
            for key in set(response.get("usage", {}))
            | set(repair.get("usage", {}))
        }
    else:
        response_usage = response.get("usage", {})
    score = max(0, min(100, int(verification.get("score") or 0)))
    unsupported = verification.get("unsupportedClaims")
    citation_issues = verification.get("citationIssues")
    causality_risks = verification.get("causalityRisks")
    verification["score"] = score
    verification["unsupportedClaims"] = [
        str(item)[:500] for item in (unsupported if isinstance(unsupported, list) else [])[:8]
    ]
    verification["citationIssues"] = [
        str(item)[:500]
        for item in (citation_issues if isinstance(citation_issues, list) else [])[:8]
    ]
    verification["causalityRisks"] = [
        str(item)[:500]
        for item in (causality_risks if isinstance(causality_risks, list) else [])[:8]
    ]
    verification["status"] = (
        "verified"
        if score >= 85
        and not verification["unsupportedClaims"]
        and not verification["citationIssues"]
        else "needs_review"
    )
    verification["notes"] = str(verification.get("notes") or "")[:1200]
    verification["usage"] = response_usage
    return verification


def revise_research_output(
    profile: dict[str, Any],
    output: dict[str, Any],
    evidence: list[dict[str, Any]],
    verification: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    evidence_text = "\n\n".join(
        (
            f"[S{index}] {item['publisher']} | {item['title']} | "
            f"{item['publishedAt']} | {item['url']}\n{item['excerpt'][:1400]}"
        )
        for index, item in enumerate(evidence, start=1)
    )
    prompt = f"""
你是资深研究编辑。请根据独立证据审计，修订研究稿，使所有事实、数字、日期和引用
都能被给定证据直接支持。删除无法支持的行业泛化；因果关系证据不足时改成明确的条件性
分析判断。保留深度分析，但不得用更强措辞掩盖证据不足。

硬性要求：
1. 只能使用给定证据，所有重要事实保留正确的 [S#]。
2. 必须逐项解决 unsupportedClaims、citationIssues 和 causalityRisks。
3. 标题、导语和结论也必须符合证据边界。
4. 保留 title、dek、summary、authorityScore、keywords、analysisProcess、sections 结构。
5. 输出严格 JSON，不要 Markdown。

主题：{profile['topic']}

证据：
{evidence_text}

原研究稿：
{json.dumps(output, ensure_ascii=False)[:46_000]}

审计：
{json.dumps(verification, ensure_ascii=False)[:14_000]}
"""
    response = bedrock.converse(
        modelId=MODEL_ID,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": 5600},
    )
    text = "".join(
        part.get("text", "")
        for part in response["output"]["message"]["content"]
        if "text" in part
    )
    try:
        revised = normalize_research_output(
            parse_research_json(text), profile, evidence
        )
    except (ValueError, json.JSONDecodeError):
        return output, response.get("usage", {})
    return revised, response.get("usage", {})


def reverify_recent_research(exclude_run_id: int) -> list[dict[str, Any]]:
    candidates = rows(
        execute_sql(
            """
            SELECT r.id, r.output_article_id, r.category_slug, r.topic,
                   a.title, a.dek, a.summary, a.authority_score,
                   a.keywords, a.body_json
            FROM research_runs r
            JOIN articles a ON a.id = r.output_article_id
            WHERE r.status = 'completed' AND r.id != :exclude_run_id
            ORDER BY
                CASE r.verification_status
                    WHEN 'pending' THEN 0
                    WHEN 'needs_review' THEN 1
                    ELSE 2
                END,
                a.updated_at ASC,
                r.started_at DESC
            LIMIT 2
            """,
            {"exclude_run_id": exclude_run_id},
        )
    )
    audits: list[dict[str, Any]] = []
    for candidate in candidates:
        evidence_rows = rows(
            execute_sql(
                """
                SELECT publisher, title, url, published_at, retrieved_at,
                       source_type, content_excerpt, data_json
                FROM research_evidence
                WHERE run_id = :run_id
                ORDER BY id
                """,
                {"run_id": candidate["id"]},
            )
        )
        evidence = [
            {
                "publisher": item["publisher"],
                "title": item["title"],
                "url": item["url"],
                "publishedAt": item["published_at"],
                "retrievedAt": item["retrieved_at"],
                "sourceType": item["source_type"],
                "excerpt": item["content_excerpt"],
                "data": json.loads(item["data_json"] or "{}"),
            }
            for item in evidence_rows
        ]
        if not evidence:
            continue
        output = {
            "title": candidate["title"],
            "dek": candidate["dek"],
            "summary": candidate["summary"],
            "authorityScore": candidate["authority_score"],
            "keywords": json.loads(candidate["keywords"] or "[]"),
            "analysisProcess": [],
            "sections": json.loads(candidate["body_json"] or "[]"),
        }
        verification = verify_research_output(output, evidence)
        initial_verification = verification
        profile = {
            "category": candidate["category_slug"],
            "topic": candidate["topic"],
        }
        revision_usages: list[dict[str, Any]] = []
        for _ in range(2):
            if verification["status"] == "verified":
                break
            revised_output, revision_usage = revise_research_output(
                profile, output, evidence, verification
            )
            if revised_output == output:
                break
            output = revised_output
            revision_usages.append(revision_usage)
            verification = verify_research_output(output, evidence)
        if revision_usages:
            verification["revisionApplied"] = True
            verification["revisionPasses"] = len(revision_usages)
            verification["revisionUsage"] = revision_usages
            verification["initialAudit"] = {
                "score": initial_verification.get("score", 0),
                "unsupportedClaims": initial_verification.get(
                    "unsupportedClaims", []
                ),
                "citationIssues": initial_verification.get(
                    "citationIssues", []
                ),
                "causalityRisks": initial_verification.get(
                    "causalityRisks", []
                ),
            }
        article_status = (
            "published"
            if AUTO_PUBLISH_RESEARCH and verification["status"] == "verified"
            else "review"
        )
        execute_sql(
            """
            UPDATE research_runs
            SET verification_status = :verification_status,
                verification_json = :verification_json,
                summary = :summary,
                analysis_process_json = :analysis_process
            WHERE id = :run_id
            """,
            {
                "verification_status": verification["status"],
                "verification_json": json.dumps(verification, ensure_ascii=False),
                "summary": str(output.get("summary") or "")[:3000],
                "analysis_process": json.dumps(
                    output.get("analysisProcess") or [], ensure_ascii=False
                ),
                "run_id": candidate["id"],
            },
        )
        execute_sql(
            """
            UPDATE articles
            SET title = :title, dek = :dek, summary = :summary,
                authority_score = :authority_score, keywords = :keywords,
                body_json = :body_json, status = :status, updated_at = :updated_at
            WHERE id = :article_id
            """,
            {
                "title": str(output.get("title") or candidate["title"])[:240],
                "dek": str(output.get("dek") or candidate["dek"])[:500],
                "summary": str(
                    output.get("summary") or candidate["summary"]
                )[:3000],
                "authority_score": max(
                    80,
                    min(
                        98,
                        int(
                            output.get("authorityScore")
                            or candidate["authority_score"]
                            or 88
                        ),
                    ),
                ),
                "keywords": json.dumps(
                    output.get("keywords") or [], ensure_ascii=False
                ),
                "body_json": json.dumps(
                    output.get("sections") or [], ensure_ascii=False
                ),
                "status": article_status,
                "updated_at": now(),
                "article_id": candidate["output_article_id"],
            },
        )
        audits.append(
            {
                "researchRunId": candidate["id"],
                "articleId": candidate["output_article_id"],
                "status": verification["status"],
                "score": verification["score"],
            }
        )
    return audits


def persist_research_output(
    *,
    crawler: dict[str, Any],
    job_id: int,
    profile: dict[str, Any],
    evidence: list[dict[str, Any]],
    fetch_errors: list[str],
    force_analysis: bool,
    tool_trace: dict[str, Any],
) -> dict[str, Any]:
    digest_input = "\n".join(
        f"{item['url']}|{item['publishedAt']}|{item['excerpt']}"
        for item in evidence
    )
    evidence_hash = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()
    previous = rows(
        execute_sql(
            """
            SELECT output_article_id, summary, verification_status,
                   verification_json
            FROM research_runs
            WHERE agent_id = :agent_id AND evidence_hash = :evidence_hash
              AND status = 'completed'
            ORDER BY id DESC LIMIT 1
            """,
            {"agent_id": crawler["id"], "evidence_hash": evidence_hash},
        )
    )
    run_status = "running" if force_analysis or not previous else "skipped"
    run = rows(
        execute_sql(
            """
            INSERT INTO research_runs(
                agent_id, job_id, status, category_slug, topic, evidence_hash,
                started_at, model_id, summary, tool_trace_json
            ) VALUES(
                :agent_id, :job_id, :status, :category_slug, :topic,
                :evidence_hash, :started_at, :model_id, :summary, :tool_trace_json
            ) RETURNING id
            """,
            {
                "agent_id": crawler["id"],
                "job_id": job_id,
                "status": run_status,
                "category_slug": profile["category"],
                "topic": profile["topic"],
                "evidence_hash": evidence_hash,
                "started_at": now(),
                "model_id": MODEL_ID,
                "summary": "证据与上次运行相同，未重复生成内容。" if previous else "",
                "tool_trace_json": json.dumps(tool_trace, ensure_ascii=False),
            },
        )
    )[0]
    run_id = run["id"]
    for item in evidence:
        execute_sql(
            """
            INSERT INTO research_evidence(
                run_id, publisher, title, url, published_at, retrieved_at,
                source_type, content_excerpt, data_json
            ) VALUES(
                :run_id, :publisher, :title, :url, :published_at, :retrieved_at,
                :source_type, :content_excerpt, :data_json
            )
            """,
            {
                "run_id": run_id,
                "publisher": item["publisher"],
                "title": item["title"],
                "url": item["url"],
                "published_at": item["publishedAt"],
                "retrieved_at": item["retrievedAt"],
                "source_type": item["sourceType"],
                "content_excerpt": item["excerpt"],
                "data_json": json.dumps(item["data"], ensure_ascii=False),
            },
        )

    if previous and not force_analysis:
        article_id = previous[0]["output_article_id"]
        completed_at = now()
        execute_sql(
            """
            UPDATE research_runs
            SET completed_at = :completed_at, output_article_id = :article_id,
                verification_status = :verification_status,
                verification_json = :verification_json
            WHERE id = :run_id
            """,
            {
                "completed_at": completed_at,
                "article_id": article_id,
                "verification_status": previous[0]["verification_status"],
                "verification_json": previous[0]["verification_json"],
                "run_id": run_id,
            },
        )
        execute_sql(
            """
            UPDATE crawler_jobs
            SET status = 'completed', finished_at = :finished_at,
                documents = :documents, message = :message,
                research_run_id = :run_id, article_id = :article_id,
                tool_trace_json = :tool_trace_json
            WHERE id = :job_id
            """,
            {
                "finished_at": completed_at,
                "documents": len(evidence),
                "message": "证据无变化，已关联上次深度研究输出",
                "run_id": run_id,
                "article_id": article_id,
                "tool_trace_json": json.dumps(tool_trace, ensure_ascii=False),
                "job_id": job_id,
            },
        )
        return {
            "status": "skipped",
            "jobId": job_id,
            "researchRunId": run_id,
            "articleId": article_id,
            "documents": len(evidence),
            "toolTrace": tool_trace,
            "message": "证据无变化，未重复生成文章",
        }

    output, usage = generate_deep_research(profile, evidence)
    verification = verify_research_output(output, evidence)
    initial_verification = verification
    revision_usages: list[dict[str, Any]] = []
    for _ in range(2):
        if verification["status"] == "verified":
            break
        revised_output, revision_usage = revise_research_output(
            profile, output, evidence, verification
        )
        for key, value in revision_usage.items():
            usage[key] = usage.get(key, 0) + value
        if revised_output == output:
            break
        output = revised_output
        revision_usages.append(revision_usage)
        verification = verify_research_output(output, evidence)
    if revision_usages:
        verification["revisionApplied"] = True
        verification["revisionPasses"] = len(revision_usages)
        verification["revisionUsage"] = revision_usages
        verification["initialAudit"] = {
            "score": initial_verification.get("score", 0),
            "unsupportedClaims": initial_verification.get(
                "unsupportedClaims", []
            ),
            "citationIssues": initial_verification.get("citationIssues", []),
            "causalityRisks": initial_verification.get("causalityRisks", []),
        }
    category = rows(
        execute_sql(
            "SELECT id FROM categories WHERE slug = :slug",
            {"slug": profile["category"]},
        )
    )[0]
    published_at = now()
    article_slug = (
        f"{profile['category']}-research-"
        f"{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}-{job_id}"
    )
    sections = output.get("sections") or []
    analysis_process = output.get("analysisProcess") or []
    article_status = (
        "published"
        if AUTO_PUBLISH_RESEARCH and verification["status"] == "verified"
        else "review"
    )
    article = rows(
        execute_sql(
            """
            INSERT INTO articles(
                category_id, slug, title, dek, summary, author, author_role,
                read_minutes, published_at, updated_at, status, featured,
                hero_style, authority_score, citation_count, access_model,
                agent_price, keywords, body_json
            ) VALUES(
                :category_id, :slug, :title, :dek, :summary, :author, :author_role,
                :read_minutes, :published_at, :updated_at, :status, FALSE,
                :hero_style, :authority_score, :citation_count, 'open', 0,
                :keywords, :body_json
            ) RETURNING id
            """,
            {
                "category_id": category["id"],
                "slug": article_slug,
                "title": str(output.get("title") or profile["topic"])[:240],
                "dek": str(output.get("dek") or profile["topic"])[:500],
                "summary": str(output.get("summary") or "")[:3000],
                "author": "Aperture Research Agent",
                "author_role": f"{crawler['name']} · GPT-5.6 Sol",
                "read_minutes": max(8, min(20, len(json.dumps(sections, ensure_ascii=False)) // 900)),
                "published_at": published_at,
                "updated_at": published_at,
                "status": article_status,
                "hero_style": {
                    "ai": "orb",
                    "agent": "network",
                    "cloud": "blocks",
                    "commerce": "commerce",
                    "finance": "market",
                }.get(profile["category"], "evidence"),
                "authority_score": max(
                    80, min(98, int(output.get("authorityScore") or 88))
                ),
                "citation_count": len(evidence),
                "keywords": json.dumps(output.get("keywords") or [], ensure_ascii=False),
                "body_json": json.dumps(sections, ensure_ascii=False),
            },
        )
    )[0]
    article_id = article["id"]
    for item in evidence:
        execute_sql(
            """
            INSERT INTO sources(
                article_id, publisher, title, url, published_at, source_type
            ) VALUES(
                :article_id, :publisher, :title, :url, :published_at, :source_type
            )
            """,
            {
                "article_id": article_id,
                "publisher": item["publisher"],
                "title": item["title"],
                "url": item["url"],
                "published_at": item["publishedAt"],
                "source_type": item["sourceType"],
            },
        )
    completed_at = now()
    summary = str(output.get("summary") or "")
    execute_sql(
        """
        UPDATE research_runs
        SET status = 'completed', completed_at = :completed_at,
            analysis_process_json = :analysis_process, summary = :summary,
            output_article_id = :article_id, error_message = :errors,
            verification_status = :verification_status,
            verification_json = :verification_json
        WHERE id = :run_id
        """,
        {
            "completed_at": completed_at,
            "analysis_process": json.dumps(analysis_process, ensure_ascii=False),
            "summary": summary,
            "article_id": article_id,
            "errors": "; ".join(fetch_errors)[:2000],
            "verification_status": verification["status"],
            "verification_json": json.dumps(verification, ensure_ascii=False),
            "run_id": run_id,
        },
    )
    message = (
        f"已生成并{'自动发布' if article_status == 'published' else '提交审核'}"
        f"深度研究《{output.get('title', profile['topic'])}》；"
        f"{len(evidence)} 条证据，审计 {verification['status']} "
        f"({verification['score']})，{usage.get('totalTokens', 0)} tokens"
    )
    execute_sql(
        """
        UPDATE crawler_jobs
        SET status = 'completed', finished_at = :finished_at,
            documents = :documents, message = :message,
            research_run_id = :run_id, article_id = :article_id,
            tool_trace_json = :tool_trace_json
        WHERE id = :job_id
        """,
        {
            "finished_at": completed_at,
            "documents": len(evidence),
            "message": message,
            "run_id": run_id,
            "article_id": article_id,
            "tool_trace_json": json.dumps(tool_trace, ensure_ascii=False),
            "job_id": job_id,
        },
    )
    execute_sql(
        """
        UPDATE crawler_agents
        SET pages_today = pages_today + :documents
        WHERE id = :agent_id
        """,
        {"documents": len(evidence), "agent_id": crawler["id"]},
    )
    return {
        "status": "completed",
        "jobId": job_id,
        "researchRunId": run_id,
        "articleId": article_id,
        "articleSlug": article_slug,
        "articleStatus": article_status,
        "documents": len(evidence),
        "verification": verification,
        "toolTrace": tool_trace,
        "message": message,
        "finishedAt": completed_at,
    }


def run_scheduled_crawler(payload: dict[str, Any]) -> dict[str, Any]:
    slug = str(payload.get("crawlerSlug", "")).strip()
    if not slug:
        raise ValueError("crawlerSlug is required")
    ensure_research_schema()
    crawler_rows = rows(
        execute_sql(
            """
            SELECT id, name, slug, kind, industries, status
            FROM crawler_agents WHERE slug = :slug
            """,
            {"slug": slug},
        )
    )
    if not crawler_rows:
        raise ValueError(f"Crawler not found: {slug}")
    crawler = crawler_rows[0]
    if crawler["status"] == "paused" and not payload.get("overridePaused"):
        return {
            "status": "skipped",
            "crawler": slug,
            "reason": "crawler is paused",
            "timestamp": now(),
        }

    started_at = now()
    job = rows(
        execute_sql(
            """
            INSERT INTO crawler_jobs(agent_id, status, started_at, documents, message)
            VALUES(:agent_id, 'running', :started_at, 0, 'EventBridge Scheduler 已触发 AgentCore')
            RETURNING id
            """,
            {"agent_id": crawler["id"], "started_at": started_at},
        )
    )[0]
    job_id = job["id"]
    execute_sql(
        """
        UPDATE crawler_agents
        SET status = 'running', last_run = :started_at
        WHERE id = :agent_id
        """,
        {"started_at": started_at, "agent_id": crawler["id"]},
    )

    try:
        profile, evidence, fetch_errors, tool_trace = collect_evidence(
            crawler,
            payload,
        )
        if not evidence:
            raise RuntimeError(
                "No evidence collected. " + "; ".join(fetch_errors)
            )
        result = persist_research_output(
            crawler=crawler,
            job_id=job_id,
            profile=profile,
            evidence=evidence,
            fetch_errors=fetch_errors,
            force_analysis=bool(payload.get("forceAnalysis")),
            tool_trace=tool_trace,
        )
        execute_sql(
            "UPDATE crawler_agents SET status = 'idle' WHERE id = :agent_id",
            {"agent_id": crawler["id"]},
        )
        result["crawler"] = slug
        result["kind"] = crawler["kind"]
        result["toolMessage"] = (
            f"{tool_trace.get('provider')} 真实执行完成；"
            f"session {tool_trace.get('sessionId', 'n/a')}"
        )
        result["scheduledTime"] = payload.get("scheduledTime")
        if slug == "evidence-verifier" and result.get("researchRunId"):
            result["crossVerification"] = reverify_recent_research(
                int(result["researchRunId"])
            )
        return result
    except Exception as error:
        execute_sql(
            """
            UPDATE research_runs
            SET status = 'failed', completed_at = :completed_at,
                error_message = :error
            WHERE job_id = :job_id AND status = 'running'
            """,
            {
                "completed_at": now(),
                "error": f"{type(error).__name__}: {str(error)[:1800]}",
                "job_id": job_id,
            },
        )
        execute_sql(
            """
            UPDATE crawler_jobs
            SET status = 'failed', finished_at = :finished_at, message = :message
            WHERE id = :job_id
            """,
            {
                "finished_at": now(),
                "message": f"{type(error).__name__}: {str(error)[:300]}",
                "job_id": job_id,
            },
        )
        execute_sql(
            "UPDATE crawler_agents SET status = 'error' WHERE id = :agent_id",
            {"agent_id": crawler["id"]},
        )
        raise


def analyze(payload: dict[str, Any]) -> dict[str, Any]:
    topic = str(payload.get("topic") or "AI 与 Agent 技术的最新产业进展")
    evidence = payload.get("evidence") or []
    prompt = (
        "你是 Aperture GEO 的资深产业研究分析师。请基于给定主题与证据，"
        "输出专业、可引用的中文研究简报。明确区分事实、推断和风险，包含核心判断、"
        "关键证据、产业影响和未来 90 天观察点。不要虚构来源。\n\n"
        f"主题：{topic}\n"
        f"证据：{json.dumps(evidence, ensure_ascii=False)}"
    )
    response = bedrock.converse(
        modelId=MODEL_ID,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": int(payload.get("maxTokens", 1600))},
    )
    text = "".join(
        part.get("text", "")
        for part in response["output"]["message"]["content"]
        if "text" in part
    )
    return {
        "topic": topic,
        "analysis": text,
        "model": MODEL_ID,
        "usage": response.get("usage", {}),
        "generatedAt": now(),
    }


def invoke(payload: dict[str, Any]) -> dict[str, Any]:
    action = str(payload.get("action", "health"))
    if action == "health":
        return {
            "status": "healthy",
            "service": "geo-intelligence-agent",
            "database": database_status(),
            "model": MODEL_ID,
            "tools": {
                "browserId": BROWSER_ID,
                "codeInterpreterId": CODE_INTERPRETER_ID,
            },
            "timestamp": now(),
        }
    if action == "analyze":
        return analyze(payload)
    if action == "tool_config":
        return {
            "browserId": BROWSER_ID,
            "codeInterpreterId": CODE_INTERPRETER_ID,
            "browserSigning": True,
            "crawlerIndustries": ["AI", "云计算", "电商", "媒体", "金融"],
        }
    if action == "x402_fetch":
        crawler_slug = str(payload.get("crawlerSlug") or "commerce-feed-miner")
        profile = SOURCE_PROFILES.get(crawler_slug) or {}
        paid_sources = profile.get("paidSources") or []
        if not paid_sources:
            raise ValueError(f"No paid source configured for {crawler_slug}")
        source = paid_sources[0]
        evidence, trace = run_x402_crawler(
            source,
            session_name=f"{crawler_slug}-manual-x402-{int(time.time())}",
        )
        return {
            "status": "completed",
            "crawler": crawler_slug,
            "evidence": evidence,
            "payment": trace,
            "timestamp": now(),
        }
    if action == "scheduled_crawl":
        return run_scheduled_crawler(payload)
    raise ValueError(f"Unsupported action: {action}")


class RuntimeHandler(BaseHTTPRequestHandler):
    server_version = "ApertureGEO-AgentCore/1.0"

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") == "/ping":
            self._json({"status": "Healthy", "time": now()})
            return
        self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        if self.path.rstrip("/") != "/invocations":
            self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            self._json(invoke(payload))
        except Exception as error:
            self._json(
                {"error": type(error).__name__, "message": str(error), "time": now()},
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def _json(self, payload: dict[str, Any], status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[runtime] {self.address_string()} - {fmt % args}", flush=True)


if __name__ == "__main__":
    print("Starting GEO AgentCore runtime on 0.0.0.0:8080", flush=True)
    ThreadingHTTPServer(("0.0.0.0", 8080), RuntimeHandler).serve_forever()
