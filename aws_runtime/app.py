from __future__ import annotations

import asyncio
import csv
import hashlib
import html
import io
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen
from xml.etree import ElementTree

import boto3
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from botocore.config import Config
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
ARTICLE_UPDATE_WINDOW_HOURS = int(
    os.environ.get("RESEARCH_ARTICLE_UPDATE_WINDOW_HOURS", "24")
)
ARTICLE_SOURCE_OVERLAP_THRESHOLD = float(
    os.environ.get("RESEARCH_ARTICLE_SOURCE_OVERLAP_THRESHOLD", "0.5")
)
MAX_OPEN_SOURCES_PER_RUN = max(
    1,
    min(20, int(os.environ.get("CRAWLER_MAX_OPEN_SOURCES_PER_RUN", "8"))),
)
CRAWLER_CONTACT_URL = os.environ.get(
    "CRAWLER_CONTACT_URL",
    "https://d1tsbnft7iv51.cloudfront.net/",
)

bedrock = boto3.client(
    "bedrock-runtime",
    region_name=REGION,
    config=Config(
        connect_timeout=10,
        read_timeout=int(os.environ.get("BEDROCK_READ_TIMEOUT_SECONDS", "900")),
        retries={"total_max_attempts": 2, "mode": "standard"},
    ),
)
rds_data = boto3.client("rds-data", region_name=REGION)
runtime_app = BedrockAgentCoreApp()
background_tasks: set[asyncio.Task[Any]] = set()
EVIDENCE_DATA_MAX_BYTES = 40_000
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

WRITING_STYLES = {
    "mechanism": {
        "name": "机制解释",
        "lens": "像研究技术变迁的产业组织学者，追问现象背后的约束、激励与反馈回路",
        "shape": "从一个关键问题切入，依次解释机制、受益者与受损者、反例和可验证预测",
        "voice": "用日常中文解释专业概念；短句优先，术语首次出现时用一句话说明",
        "byline": "产业组织与技术变迁研究",
    },
    "comparative": {
        "name": "比较研究",
        "lens": "像做比较案例研究的学者，比较不同公司、技术路线或制度安排为何产生不同结果",
        "shape": "先给比较标准，再分析相同点、关键差异、边界条件和能够推广的结论",
        "voice": "不堆产品名，不写公关稿；把差异翻译成读者能用于判断的具体影响",
        "byline": "比较技术与产业研究",
    },
    "data_note": {
        "name": "数据研究札记",
        "lens": "像严谨的应用经济学研究者，从数据口径、变化幅度和替代解释出发",
        "shape": "先说明数据回答什么，再解释趋势、竞争性解释、情景推演和失效条件",
        "voice": "数字必须带时间和口径；不用夸张形容词，用通俗语言说明数字意味着什么",
        "byline": "应用经济与市场数据研究",
    },
    "field_note": {
        "name": "行业田野观察",
        "lens": "像长期观察平台、商家和消费者行为的数字经济学者，从真实参与者决策出发",
        "shape": "以一个具体行为变化开场，沿价值链分析动机、摩擦、分配结果与后续信号",
        "voice": "多用具体主体和动作，少用空泛趋势词；让非专业读者也能顺着因果链读下去",
        "byline": "数字经济与平台治理研究",
    },
    "critical_review": {
        "name": "批判性综述",
        "lens": "像科技政策与科学方法研究者，检查证据强弱、测量偏差和被忽略的反证",
        "shape": "先界定可确认事实，再审视证据缺口、争议解释、治理含义和下一步验证方法",
        "voice": "不把不确定性藏在套话里；直接说明我们知道什么、不知道什么、为什么重要",
        "byline": "科技政策与证据治理研究",
    },
    "architecture": {
        "name": "架构决策分析",
        "lens": "像兼具分布式系统研究和工程经验的学者，从可靠性、成本和组织能力审视新服务",
        "shape": "从架构问题出发，解释技术机制、迁移代价、适用边界、替代方案和决策清单",
        "voice": "把复杂架构说清楚，不用厂商口号；每个结论都落到工程选择和业务后果",
        "byline": "云架构与分布式系统研究",
    },
}

HUMAN_TITLE_FALLBACKS = {
    "ai": "AI产业的新分工：模型、应用与基础设施如何重新组合",
    "agent": "Agent进入生产环境：执行控制、数据连接与治理如何协同",
    "cloud": "企业AI架构的关键变化：智能体、数据、运行时与计费",
    "commerce": "AI正在改变电商入口、交易流程与平台分工",
    "finance": "利率与科技股重新定价：AI资本开支进入回报检验期",
}
TITLE_AUDIT_LANGUAGE = (
    "给定材料",
    "给定证据",
    "给定摘录",
    "现有材料",
    "现有证据",
    "证据不足",
    "未展示",
    "未提供",
    "无法确认",
    "无法形成",
    "仍待核验",
    "本研究",
    "证据链细节",
)

SOURCE_PROFILES = {
    "research-coder": {
        "category": "ai",
        "crawlStyle": "research",
        "topic": "AI 基础模型、Agent 与云端 AI 基础设施的最新产业进展",
        "writingStyles": ["mechanism", "comparative", "critical_review"],
    },
    "render-scout": {
        "category": "commerce",
        "crawlStyle": "browser-rendered",
        "topic": "电商、支付与媒体平台采用 AI 和 Agent Commerce 的最新变化",
        "writingStyles": ["field_note", "comparative", "mechanism"],
    },
    "market-signal": {
        "category": "finance",
        "crawlStyle": "financial-timeseries",
        "topic": "科技股、利率与 AI 资本开支周期的最新市场研判",
        "writingStyles": ["data_note", "mechanism", "comparative"],
    },
    "evidence-verifier": {
        "category": "agent",
        "crawlStyle": "evidence-verification",
        "topic": "Agent 运行时、工具执行、证据治理与机器身份的技术进展",
        "writingStyles": ["critical_review", "mechanism", "comparative"],
    },
    "cloud-release-watch": {
        "category": "cloud",
        "crawlStyle": "browser-rendered",
        "topic": "主要云计算厂商最新服务发布及其对企业 AI 架构的影响",
        "writingStyles": ["architecture", "comparative", "mechanism"],
    },
    "commerce-feed-miner": {
        "category": "commerce",
        "crawlStyle": "research",
        "topic": "电商平台、支付基础设施与 AI 购物 Agent 的商业模式变化",
        "writingStyles": ["field_note", "mechanism", "comparative"],
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


def load_source_profile(
    crawler: dict[str, Any],
    *,
    mark_selected: bool = False,
) -> dict[str, Any]:
    slug = str(crawler["slug"])
    base_profile = SOURCE_PROFILES.get(slug)
    if not base_profile:
        raise ValueError(f"No research profile configured for {slug}")
    registered = rows(
        execute_sql(
            """
            SELECT ds.id, ds.publisher, ds.name, ds.url, ds.source_type,
                   ds.ingestion_method, ds.max_items, ds.respect_robots,
                   ds.access_model, ds.config_json, ds.trust_tier,
                   asa.priority, asa.last_selected_at, asa.selection_count
            FROM agent_source_assignments asa
            JOIN data_sources ds ON ds.id = asa.source_id
            WHERE asa.agent_id = :agent_id
              AND asa.enabled = TRUE
              AND ds.status = 'active'
            ORDER BY asa.last_selected_at NULLS FIRST,
                     ds.trust_tier, asa.priority, ds.id
            """,
            {"agent_id": crawler["id"]},
        )
    )
    if not registered:
        raise ValueError(f"No active registered sources assigned to {slug}")

    open_registered = [
        item
        for item in registered
        if item["access_model"] != "x402" and item["ingestion_method"] != "x402"
    ]
    paid_registered = [
        item
        for item in registered
        if item["access_model"] == "x402" or item["ingestion_method"] == "x402"
    ]
    eligible_open: list[dict[str, Any]] = []
    deferred_by_cache_ttl = 0
    selected_at_dt = datetime.now(timezone.utc)
    for item in open_registered:
        try:
            config = json.loads(str(item.get("config_json") or "{}"))
        except json.JSONDecodeError:
            config = {}
        policy = config.get("requestPolicy") if isinstance(config, dict) else {}
        cache_ttl = int(
            policy.get("cacheTtlSeconds", 0)
            if isinstance(policy, dict)
            else 0
        )
        last_selected = item.get("last_selected_at")
        if cache_ttl > 0 and last_selected:
            try:
                last_selected_dt = datetime.fromisoformat(
                    str(last_selected).replace("Z", "+00:00")
                )
                if (selected_at_dt - last_selected_dt).total_seconds() < cache_ttl:
                    deferred_by_cache_ttl += 1
                    continue
            except ValueError:
                pass
        eligible_open.append(item)
    selected_open = eligible_open[:MAX_OPEN_SOURCES_PER_RUN]
    selected = selected_open + paid_registered
    if mark_selected and selected_open:
        selected_at = now()
        for item in selected_open:
            execute_sql(
                """
                UPDATE agent_source_assignments
                SET last_selected_at = :selected_at,
                    selection_count = selection_count + 1,
                    updated_at = :selected_at
                WHERE agent_id = :agent_id AND source_id = :source_id
                """,
                {
                    "selected_at": selected_at,
                    "agent_id": crawler["id"],
                    "source_id": item["id"],
                },
            )

    profile = dict(base_profile)
    profile["sources"] = []
    profile["paidSources"] = []
    profile["sourceRegistry"] = {
        "mode": "aurora",
        "sourceIds": [item["id"] for item in selected],
        "activeAssigned": len(registered),
        "selectedOpen": len(selected_open),
        "paidAssigned": len(paid_registered),
        "deferredByCacheTtl": deferred_by_cache_ttl,
        "maxOpenPerRun": MAX_OPEN_SOURCES_PER_RUN,
        "loadedAt": now(),
    }
    for item in selected:
        try:
            source_config = json.loads(str(item.get("config_json") or "{}"))
        except json.JSONDecodeError:
            source_config = {}
        if isinstance(source_config, dict):
            request_policy = source_config.get("requestPolicy")
            if isinstance(request_policy, dict):
                source_config["requestPolicy"] = {
                    **request_policy,
                    "userAgent": str(
                        request_policy.get("userAgent")
                        or (
                            "ApertureGEOResearchBot/2.0 "
                            "(Aperture GEO; +{contactUrl})"
                        )
                    ).replace("{contactUrl}", CRAWLER_CONTACT_URL),
                }
        source = {
            "sourceId": item["id"],
            "publisher": item["publisher"],
            "title": item["name"],
            "url": item["url"],
            "sourceType": item["source_type"],
            "ingestionMethod": item["ingestion_method"],
            "maxItems": int(item["max_items"]),
            "respectRobots": bool(item["respect_robots"]),
            "trustTier": int(item["trust_tier"]),
            "config": source_config if isinstance(source_config, dict) else {},
        }
        if item["access_model"] == "x402" or item["ingestion_method"] == "x402":
            profile["paidSources"].append(source)
        else:
            profile["sources"].append(source)
    if not profile["sources"] and not profile["paidSources"]:
        raise ValueError(f"No usable registered sources assigned to {slug}")
    return profile


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


def _json_bytes(value: Any) -> int:
    return len(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    )


def _compact_json_value(
    value: Any,
    *,
    depth: int = 0,
    string_limit: int = 2_000,
    list_head: int = 2,
    list_tail: int = 20,
    dict_limit: int = 80,
) -> Any:
    if depth >= 7:
        return str(value)[:string_limit]
    if isinstance(value, dict):
        compacted: dict[str, Any] = {}
        for key, item in list(value.items())[:dict_limit]:
            compacted[str(key)[:200]] = _compact_json_value(
                item,
                depth=depth + 1,
                string_limit=string_limit,
                list_head=list_head,
                list_tail=list_tail,
                dict_limit=dict_limit,
            )
        if len(value) > dict_limit:
            compacted["_omittedKeyCount"] = len(value) - dict_limit
        return compacted
    if isinstance(value, (list, tuple)):
        items = list(value)
        if len(items) > list_head + list_tail:
            items = items[:list_head] + items[-list_tail:]
        return [
            _compact_json_value(
                item,
                depth=depth + 1,
                string_limit=string_limit,
                list_head=list_head,
                list_tail=list_tail,
                dict_limit=dict_limit,
            )
            for item in items
        ]
    if isinstance(value, str):
        return value[:string_limit]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:string_limit]


def compact_evidence_data(data: dict[str, Any]) -> dict[str, Any]:
    original_bytes = _json_bytes(data)
    if original_bytes <= EVIDENCE_DATA_MAX_BYTES:
        return data

    compacted = _compact_json_value(data)
    if not isinstance(compacted, dict):
        compacted = {"value": compacted}
    observations = data.get("observations")
    if isinstance(observations, list):
        compacted["observationCount"] = data.get(
            "observationCount", len(observations)
        )
        compacted["observations"] = _compact_json_value(
            observations,
            list_head=2,
            list_tail=20,
            string_limit=1_000,
            dict_limit=40,
        )
        compacted["_observationSample"] = {
            "total": len(observations),
            "kept": len(compacted["observations"]),
        }
    compacted["_compacted"] = True
    compacted["_originalBytes"] = original_bytes
    if _json_bytes(compacted) <= EVIDENCE_DATA_MAX_BYTES:
        return compacted

    compacted = _compact_json_value(
        data,
        string_limit=500,
        list_head=1,
        list_tail=8,
        dict_limit=40,
    )
    if not isinstance(compacted, dict):
        compacted = {"value": compacted}
    compacted["_compacted"] = True
    compacted["_originalBytes"] = original_bytes
    if isinstance(observations, list):
        compacted["observationCount"] = data.get(
            "observationCount", len(observations)
        )
    if _json_bytes(compacted) <= EVIDENCE_DATA_MAX_BYTES:
        return compacted

    return {
        "_compacted": True,
        "_originalBytes": original_bytes,
        "_preview": json.dumps(
            data,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )[:4_000],
    }


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
                "data": compact_evidence_data(
                    item.get("data")
                    if isinstance(item.get("data"), dict)
                    else {}
                ),
            }
        )
    return normalized


def collect_evidence(
    crawler: dict[str, Any],
    payload: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str], dict[str, Any]]:
    slug = str(crawler["slug"])
    profile = load_source_profile(crawler, mark_selected=True)
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
    trace["sourceRegistry"] = profile["sourceRegistry"]
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


def select_writing_style(
    profile: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> dict[str, str]:
    style_names = profile.get("writingStyles") or ["mechanism"]
    signal = "|".join(
        [
            str(profile.get("topic") or ""),
            *[
                f"{item.get('title', '')}:{item.get('publishedAt', '')}:{item.get('url', '')}"
                for item in evidence
            ],
        ]
    )
    index = int(hashlib.sha256(signal.encode("utf-8")).hexdigest()[:8], 16)
    style_name = style_names[index % len(style_names)]
    return {"id": style_name, **WRITING_STYLES[style_name]}


def evidence_prompt_block(
    item: dict[str, Any],
    index: int,
    *,
    excerpt_limit: int,
) -> str:
    block = (
        f"[S{index}] {item['publisher']} | {item['title']} | "
        f"{item['publishedAt']} | {item['url']}\n"
        f"{str(item.get('excerpt') or '')[:excerpt_limit]}"
    )
    data = item.get("data")
    if isinstance(data, dict) and data:
        structured = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        block += f"\n结构化数据：{structured[:5_000]}"
    return block


def canonical_source_url(value: str) -> str:
    parsed = urlsplit(str(value or "").strip())
    ignored_query_prefixes = ("utm_",)
    ignored_query_keys = {"ref", "source", "campaign"}
    query = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in ignored_query_keys
        and not key.lower().startswith(ignored_query_prefixes)
    ]
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            path,
            urlencode(sorted(query)),
            "",
        )
    )


def article_update_candidate(
    crawler_id: int,
    evidence: list[dict[str, Any]],
) -> dict[str, Any] | None:
    latest = rows(
        execute_sql(
            """
            SELECT r.id run_id, r.output_article_id, a.slug article_slug,
                   a.status article_status, a.published_at
            FROM research_runs r
            JOIN articles a ON a.id = r.output_article_id
            WHERE r.agent_id = :agent_id
              AND r.status = 'completed'
              AND r.output_article_id IS NOT NULL
            ORDER BY r.completed_at DESC, r.id DESC
            LIMIT 1
            """,
            {"agent_id": crawler_id},
        )
    )
    if not latest:
        return None
    candidate = latest[0]
    published_at = datetime.fromisoformat(
        str(candidate["published_at"]).replace("Z", "+00:00")
    )
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    age_hours = (
        datetime.now(timezone.utc) - published_at.astimezone(timezone.utc)
    ).total_seconds() / 3600
    if age_hours > ARTICLE_UPDATE_WINDOW_HOURS:
        return None

    previous_urls = {
        canonical_source_url(item["url"])
        for item in rows(
            execute_sql(
                """
                SELECT url FROM research_evidence
                WHERE run_id = :run_id
                """,
                {"run_id": candidate["run_id"]},
            )
        )
        if item.get("url")
    }
    current_urls = {
        canonical_source_url(str(item.get("url") or ""))
        for item in evidence
        if item.get("url")
    }
    union = previous_urls | current_urls
    overlap = len(previous_urls & current_urls) / len(union) if union else 0.0
    if overlap < ARTICLE_SOURCE_OVERLAP_THRESHOLD:
        return None
    return {
        **candidate,
        "sourceOverlap": round(overlap, 4),
        "ageHours": round(age_hours, 2),
    }


def fallback_research(
    text: str,
    profile: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    style = select_writing_style(profile, evidence)
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
        "title": HUMAN_TITLE_FALLBACKS.get(
            str(profile.get("category") or ""),
            f"{profile['topic']}：最新进展与关键影响",
        ),
        "dek": (
            "从最新行业变化出发，解释背后的约束、受影响的参与者，"
            "以及接下来最值得跟踪的信号。"
        ),
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
                "heading": "先把问题说清楚",
                "paragraphs": paragraphs[:2],
            },
            {
                "type": "matrix",
                "heading": "哪些事实最关键",
                "headers": ["证据", "观察", "研究含义"],
                "rows": evidence_rows,
            },
            {
                "type": "analysis",
                "heading": f"{style['name']}：为什么会这样",
                "number": "01",
                "paragraphs": paragraphs[2:5],
            },
            {
                "type": "counterargument",
                "heading": "还有哪些解释不能忽略",
                "number": "02",
                "paragraphs": paragraphs[4:7],
                "quote": paragraphs[0][:180],
            },
            {
                "type": "outlook",
                "heading": "接下来用什么检验判断",
                "bullets": [
                    paragraphs[-3][:260],
                    paragraphs[-2][:260],
                    paragraphs[-1][:260],
                ],
            },
        ],
    }


def human_facing_title(title: str, profile: dict[str, Any]) -> str:
    cleaned = re.sub(r"\s+", " ", str(title or "")).strip()
    cleaned = re.sub(r"^\s*(?:【[^】]+】|事实|分析判断|研究结论)[：:，,\s]*", "", cleaned)
    cleaned = re.sub(r"\s*\[S\d+(?:[-–][S]?\d+)?\]\s*", "", cleaned)

    if any(
        cleaned.startswith(prefix)
        for prefix in (
            "给定材料",
            "给定证据",
            "给定摘录",
            "现有材料",
            "现有证据",
            "从给定材料看",
            "从给定证据看",
        )
    ) and ("：" in cleaned or ":" in cleaned):
        cleaned = re.split(r"[：:]", cleaned, maxsplit=1)[1].strip()

    cleaned = re.split(
        (
            r"[；;，,]\s*(?:但|而|不过|同时)?\s*"
            r"(?:给定|现有)(?:材料|证据|摘录|公开信息)"
        ),
        cleaned,
        maxsplit=1,
    )[0].strip(" ：:；;，,")

    if (
        not cleaned
        or len(cleaned) < 8
        or any(term in cleaned for term in TITLE_AUDIT_LANGUAGE)
    ):
        cleaned = HUMAN_TITLE_FALLBACKS.get(
            str(profile.get("category") or ""),
            f"{profile.get('topic') or '行业变化'}：最新进展与关键影响",
        )
    if len(cleaned) > 54:
        shorter = re.split(r"[；;。]", cleaned, maxsplit=1)[0].strip()
        cleaned = shorter if 12 <= len(shorter) <= 54 else cleaned[:54].rstrip()
    return cleaned


def humanize_research_prose(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value
    replacements = (
        ("【事实】", ""),
        ("【来源陈述】", ""),
        ("【分析判断】", ""),
        ("【条件性判断】", ""),
        ("【证据边界】", "需要注意的是，"),
        ("【专业观点】", ""),
        ("给定材料", "现有公开信息"),
        ("给定证据", "现有公开信息"),
        ("给定摘录", "现有公开摘要"),
        ("本研究", "本文"),
    )
    for original, replacement in replacements:
        text = text.replace(original, replacement)
    text = re.sub(
        (
            r"^(?:事实|事实层面|分析判断|条件性判断|专业观点|"
            r"来源陈述|证据边界|替代解释)[：:，,\s]+"
        ),
        "",
        text.strip(),
    )
    text = re.sub(r"([。！？])\s*事实[：:]", r"\1", text)
    text = re.sub(r"([。！？])\s*分析判断[：:]", r"\1", text)
    return re.sub(r"，{2,}", "，", text).strip()


def humanize_research_sections(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for section in sections:
        for field in ("heading", "quote"):
            if isinstance(section.get(field), str):
                section[field] = humanize_research_prose(section[field])
        for field in ("paragraphs", "bullets"):
            values = section.get(field)
            if isinstance(values, list):
                section[field] = [
                    humanize_research_prose(item) for item in values
                ]
        rows = section.get("rows")
        if isinstance(rows, list):
            section["rows"] = [
                [
                    humanize_research_prose(cell)
                    for cell in row
                ]
                if isinstance(row, list)
                else row
                for row in rows
            ]
    return sections


def needs_human_editorial_revision(output: dict[str, Any]) -> bool:
    generic_headings = {
        "核心观点",
        "数据与证据",
        "分析过程",
        "分析过程：从事实到判断",
        "专业观点与产业影响",
        "专业观点与潜在影响",
        "结论与未来观察",
    }
    sections = output.get("sections")
    headings = [
        str(section.get("heading") or "").strip()
        for section in sections
        if isinstance(section, dict)
    ] if isinstance(sections, list) else []
    if sum(heading in generic_headings for heading in headings) >= 2:
        return True
    public_text = " ".join(
        [
            str(output.get("title") or ""),
            str(output.get("dek") or ""),
            str(output.get("summary") or ""),
            *[
                str(paragraph)
                for section in sections or []
                if isinstance(section, dict)
                for paragraph in section.get("paragraphs") or []
            ],
        ]
    )
    audit_markers = re.findall(
        (
            r"(?:^|[。！？])\s*(?:事实|分析判断|条件性判断|"
            r"证据边界|来源陈述)[：:]"
        ),
        public_text,
    )
    return len(audit_markers) >= 2


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
    normalized["title"] = human_facing_title(
        str(normalized["title"]), profile
    )
    normalized["dek"] = humanize_research_prose(normalized["dek"])
    normalized["summary"] = humanize_research_prose(normalized["summary"])

    analysis_process = normalized.get("analysisProcess")
    if not isinstance(analysis_process, list) or not analysis_process:
        normalized["analysisProcess"] = fallback["analysisProcess"]

    sections = normalized.get("sections")
    if not isinstance(sections, list):
        sections = []
    sections = [section for section in sections if isinstance(section, dict)]

    if not any(section.get("paragraphs") for section in sections):
        sections.insert(0, fallback["sections"][0])
    if not any(section.get("rows") for section in sections):
        sections.append(fallback["sections"][1])
    analytical_sections = [
        section
        for section in sections
        if section.get("type") in {
            "analysis",
            "counterargument",
            "scenario",
            "case-study",
            "mechanism",
        }
        or (
            section.get("paragraphs")
            and section.get("type") not in {"lead", "outlook"}
        )
    ]
    if not analytical_sections:
        sections.extend(fallback["sections"][2:4])

    has_outlook = any(
        section.get("type") in {"outlook", "scenario"}
        or section.get("bullets")
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
                "heading": "接下来用什么检验判断",
                "bullets": bullets,
            }
        )

    normalized["sections"] = humanize_research_sections(sections)
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
    style = select_writing_style(profile, evidence)
    evidence_text = "\n\n".join(
        evidence_prompt_block(item, index, excerpt_limit=12_000)
        for index, item in enumerate(evidence, start=1)
    )
    prompt = f"""
你是 Aperture Intelligence 的研究作者。请基于下列一手证据撰写深度中文行业分析。
你要有学者的问题意识和论证纪律，但不要写成论文腔，也不要写成公司新闻稿。
先把复杂问题想透，再用普通读者能一次读懂的中文说清楚。

研究主题：{profile['topic']}
数据截止：{now()}
本篇研究方法：{style['name']}
观察视角：{style['lens']}
文章组织：{style['shape']}
语言要求：{style['voice']}

硬性规则：
1. 只能使用给定证据中的事实和数字，不得虚构来源、日期、产品或市场数据。
2. 每个重要事实后用 [S1] 形式标出证据编号。
3. 明确区分“事实”“分析判断”“风险/不确定性”。
4. 不要按固定模板写。根据本篇研究方法，自行决定 4 到 7 个段落模块和具体标题。
5. 至少要有一个证据表、两个有实质论证的分析模块，以及一个可被未来数据检验的结尾。
6. 必须讨论至少一种替代解释或反例，不能把相关性直接写成因果关系。
7. 删除“赋能、引领、重塑、深刻变革、值得关注”等没有信息量的套话。
8. 句子尽量简洁。术语第一次出现时顺手解释，不要用术语显示专业。
9. 金融内容必须声明“不构成投资建议”。
10. 标题必须直接说清主体、变化或影响，像专业媒体文章标题。标题不得出现
“给定材料、给定证据、现有证据、证据不足、未展示、未提供、无法确认、仍待核验、
本研究”等审计措辞，也不要把资料缺口当作标题主语。
11. 正文首先服务人类读者。不要在段首反复使用“事实：、分析判断：、条件性判断：、
证据边界：、来源陈述：”等标签；用自然的转折和限定语把事实、解释与不确定性连接起来。
12. 不要使用“核心观点、数据与证据、分析过程、专业观点与产业影响、结论与未来观察”
这组通用章节名。每个章节标题都要针对本篇具体问题。
13. [S#] 引用和 JSON 结构负责 Agent 可读性；不要为了机器读取牺牲人类文章的叙事、
节奏和可读性。
14. 输出严格 JSON，不要 Markdown 代码围栏。

JSON 数据结构（字段固定，文章结构和标题不固定）：
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
    {{"type":"lead|evidence|analysis|mechanism|case-study|counterargument|scenario|outlook","heading":"针对本篇问题拟定的自然标题","paragraphs":["完整论证段落"],"headers":["可选"],"rows":[["可选证据表"]],"bullets":["可选"],"quote":"可选"}}
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
        return normalize_research_output(parse_research_json(text), profile, evidence), usage
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
    evidence_text = "\n\n".join(
        evidence_prompt_block(item, index, excerpt_limit=1_200)
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
        evidence_prompt_block(item, index, excerpt_limit=1_400)
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
5. 保留原稿的研究方法和自然文章结构，不要改回统一模板。
6. 用普通读者能看懂的中文重写有问题的句子，避免论文腔、新闻稿和空泛趋势词。
7. 审计意见只用于内部修订，不得把 unsupportedClaims、citationIssues 的措辞直接写入
标题、导语或正文，不要让文章看起来像审计报告。
8. 标题直接说明主体、变化或影响，不得出现“给定材料、给定证据、现有证据、证据不足、
未展示、未提供、无法确认、仍待核验、本研究”等措辞。
9. 不要在段首使用“事实：、分析判断：、条件性判断：、证据边界：、来源陈述：”标签。
用“与此同时、这意味着、需要注意的是、如果……那么……”等自然语言表达边界。
10. [S#] 和结构化字段负责 Agent 可读性，公开正文首先要像写给专业人类读者的文章。
11. 输出严格 JSON，不要 Markdown。

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
                       source_type, content_excerpt,
                       {evidence_data_sql} AS data_json
                FROM research_evidence
                WHERE run_id = :run_id
                ORDER BY id
                """.format(evidence_data_sql=EVIDENCE_DATA_SQL),
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
        profile = {
            "category": candidate["category_slug"],
            "topic": candidate["topic"],
        }
        output = normalize_research_output({
            "title": candidate["title"],
            "dek": candidate["dek"],
            "summary": candidate["summary"],
            "authorityScore": candidate["authority_score"],
            "keywords": json.loads(candidate["keywords"] or "[]"),
            "analysisProcess": [],
            "sections": json.loads(candidate["body_json"] or "[]"),
        }, profile, evidence)
        verification = verify_research_output(output, evidence)
        initial_verification = verification
        revision_usages: list[dict[str, Any]] = []
        for _ in range(2):
            if (
                verification["status"] == "verified"
                and not needs_human_editorial_revision(output)
            ):
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
    update_candidate = article_update_candidate(int(crawler["id"]), evidence)
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
        if (
            verification["status"] == "verified"
            and not needs_human_editorial_revision(output)
        ):
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
    writing_style = select_writing_style(profile, evidence)
    verification["writingStyle"] = {
        "id": writing_style["id"],
        "name": writing_style["name"],
        "lens": writing_style["lens"],
    }
    sections = output.get("sections") or []
    analysis_process = output.get("analysisProcess") or []
    article_status = (
        "published"
        if AUTO_PUBLISH_RESEARCH and verification["status"] == "verified"
        else "review"
    )
    should_update_article = bool(
        update_candidate
        and (
            article_status == "published"
            or update_candidate["article_status"] != "published"
        )
    )
    article_values = {
        "category_id": category["id"],
        "title": str(output.get("title") or profile["topic"])[:240],
        "dek": str(output.get("dek") or profile["topic"])[:500],
        "summary": str(output.get("summary") or "")[:3000],
        "author": "Aperture 研究编辑部",
        "author_role": f"{writing_style['byline']} · {crawler['name']}",
        "read_minutes": max(
            8,
            min(
                20,
                len(json.dumps(sections, ensure_ascii=False)) // 900,
            ),
        ),
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
        "keywords": json.dumps(
            output.get("keywords") or [], ensure_ascii=False
        ),
        "body_json": json.dumps(sections, ensure_ascii=False),
    }
    if should_update_article:
        article_values["article_id"] = update_candidate["output_article_id"]
        article = rows(
            execute_sql(
                """
                UPDATE articles
                SET category_id = :category_id, title = :title, dek = :dek,
                    summary = :summary, author = :author,
                    author_role = :author_role, read_minutes = :read_minutes,
                    updated_at = :updated_at, status = :status,
                    hero_style = :hero_style,
                    authority_score = :authority_score,
                    citation_count = :citation_count, keywords = :keywords,
                    body_json = :body_json
                WHERE id = :article_id
                RETURNING id, slug
                """,
                article_values,
            )
        )[0]
        execute_sql(
            "DELETE FROM sources WHERE article_id = :article_id",
            {"article_id": article["id"]},
        )
        article_action = "更新"
        verification["deduplication"] = {
            "action": "updated_existing_article",
            "sourceOverlap": update_candidate["sourceOverlap"],
            "articleAgeHours": update_candidate["ageHours"],
        }
    else:
        article_values.update(
            {
                "slug": article_slug,
                "published_at": published_at,
            }
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
                    :category_id, :slug, :title, :dek, :summary, :author,
                    :author_role, :read_minutes, :published_at, :updated_at,
                    :status, FALSE, :hero_style, :authority_score,
                    :citation_count, 'open', 0, :keywords, :body_json
                ) RETURNING id, slug
                """,
                article_values,
            )
        )[0]
        article_action = "生成"
        verification["deduplication"] = {
            "action": "created_new_article",
            "sourceOverlap": (
                update_candidate["sourceOverlap"]
                if update_candidate
                else None
            ),
        }
    article_id = article["id"]
    article_slug = article["slug"]
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
        f"已{article_action}并"
        f"{'自动发布' if article_status == 'published' else '提交审核'}"
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
            try:
                result["crossVerification"] = reverify_recent_research(
                    int(result["researchRunId"])
                )
            except Exception as error:
                cross_verification_error = (
                    f"{type(error).__name__}: {str(error)[:1200]}"
                )
                result["crossVerification"] = {
                    "status": "failed",
                    "error": cross_verification_error,
                }
                execute_sql(
                    """
                    UPDATE crawler_jobs
                    SET message = left(
                        message || :cross_verification_message,
                        2000
                    )
                    WHERE id = :job_id
                    """,
                    {
                        "cross_verification_message": (
                            "；历史交叉复核失败：" + cross_verification_error
                        ),
                        "job_id": job_id,
                    },
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
    if action == "source_registry":
        crawler_slug = str(payload.get("crawlerSlug") or "").strip()
        if not crawler_slug:
            raise ValueError("crawlerSlug is required")
        crawler_matches = rows(
            execute_sql(
                """
                SELECT id, name, slug, kind, industries, status
                FROM crawler_agents WHERE slug = :slug
                """,
                {"slug": crawler_slug},
            )
        )
        if not crawler_matches:
            raise ValueError(f"Crawler not found: {crawler_slug}")
        profile = load_source_profile(crawler_matches[0])
        return {
            "status": "ok",
            "crawler": crawler_slug,
            "registry": profile["sourceRegistry"],
            "sources": profile["sources"],
            "paidSources": profile["paidSources"],
            "timestamp": now(),
        }
    if action == "x402_fetch":
        crawler_slug = str(payload.get("crawlerSlug") or "commerce-feed-miner")
        crawler_matches = rows(
            execute_sql(
                """
                SELECT id, name, slug, kind, industries, status
                FROM crawler_agents WHERE slug = :slug
                """,
                {"slug": crawler_slug},
            )
        )
        if not crawler_matches:
            raise ValueError(f"Crawler not found: {crawler_slug}")
        profile = load_source_profile(crawler_matches[0])
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


@runtime_app.async_task
async def run_background_crawl(payload: dict[str, Any]) -> dict[str, Any]:
    """Run a crawl after the invocation has returned its acceptance response."""
    result = await asyncio.to_thread(run_scheduled_crawler, payload)
    result["dispatchId"] = payload.get("dispatchId")
    return result


def background_task_finished(task: asyncio.Task[Any]) -> None:
    background_tasks.discard(task)
    try:
        result = task.result()
    except asyncio.CancelledError:
        print("[runtime] asynchronous crawl cancelled", flush=True)
    except Exception as error:
        print(
            f"[runtime] asynchronous crawl failed: task={task.get_name()} "
            f"{type(error).__name__}: {error}",
            flush=True,
        )
    else:
        print(
            "[runtime] asynchronous crawl completed: "
            f"crawler={result.get('crawler')} status={result.get('status')} "
            f"jobId={result.get('jobId')} dispatchId={result.get('dispatchId')}",
            flush=True,
        )


@runtime_app.entrypoint
async def runtime_entrypoint(payload: dict[str, Any]) -> dict[str, Any]:
    action = str(payload.get("action", "health"))
    if action != "scheduled_crawl":
        return await asyncio.to_thread(invoke, payload)

    dispatch_id = str(payload.get("requestId") or uuid.uuid4())
    task_payload = {**payload, "dispatchId": dispatch_id}
    task = asyncio.create_task(
        run_background_crawl(task_payload),
        name=f"crawl-{task_payload.get('crawlerSlug', 'unknown')}-{dispatch_id}",
    )
    background_tasks.add(task)
    task.add_done_callback(background_task_finished)
    return {
        "status": "accepted",
        "executionMode": "async",
        "dispatchId": dispatch_id,
        "crawler": task_payload.get("crawlerSlug"),
        "message": "AgentCore Runtime 已接收后台任务",
        "acceptedAt": now(),
    }


if __name__ == "__main__":
    print("Starting asynchronous GEO AgentCore runtime on 0.0.0.0:8080", flush=True)
    runtime_app.run(port=8080, host="0.0.0.0")
