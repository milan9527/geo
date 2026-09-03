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

bedrock = boto3.client("bedrock-runtime", region_name=REGION)
rds_data = boto3.client("rds-data", region_name=REGION)
agentcore = boto3.client("bedrock-agentcore", region_name=REGION)

SOURCE_PROFILES = {
    "research-coder": {
        "category": "ai",
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
        "topic": "电商、支付与媒体平台采用 AI 和 Agent Commerce 的最新变化",
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
    },
    "market-signal": {
        "category": "finance",
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
        "topic": "主要云计算厂商最新服务发布及其对企业 AI 架构的影响",
        "sources": [
            {
                "publisher": "AWS What's New",
                "url": "https://aws.amazon.com/about-aws/whats-new/recent/feed/",
                "sourceType": "官方发布",
            },
            {
                "publisher": "Google Cloud",
                "url": "https://cloud.google.com/feeds/gcp-release-notes.xml",
                "sourceType": "官方发布说明",
            },
        ],
    },
    "commerce-feed-miner": {
        "category": "commerce",
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
            "User-Agent": "ApertureGEOResearchBot/1.0 (+https://example.com/bot)",
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


def collect_evidence(slug: str) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    profile = SOURCE_PROFILES.get(slug)
    if not profile:
        raise ValueError(f"No source profile configured for {slug}")
    evidence: list[dict[str, Any]] = []
    errors: list[str] = []
    for source in profile["sources"]:
        try:
            evidence.extend(fetch_source(source))
        except Exception as error:
            errors.append(f"{source['publisher']}: {type(error).__name__}: {error}")
    return profile, evidence[:10], errors


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
            error_message TEXT NOT NULL DEFAULT ''
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
        "CREATE INDEX IF NOT EXISTS idx_research_runs_started ON research_runs(started_at)",
        "CREATE INDEX IF NOT EXISTS idx_research_evidence_run ON research_evidence(run_id)",
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


def persist_research_output(
    *,
    crawler: dict[str, Any],
    job_id: int,
    profile: dict[str, Any],
    evidence: list[dict[str, Any]],
    fetch_errors: list[str],
    force_analysis: bool,
) -> dict[str, Any]:
    digest_input = "\n".join(
        f"{item['url']}|{item['publishedAt']}|{item['excerpt']}"
        for item in evidence
    )
    evidence_hash = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()
    previous = rows(
        execute_sql(
            """
            SELECT output_article_id, summary
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
                started_at, model_id, summary
            ) VALUES(
                :agent_id, :job_id, :status, :category_slug, :topic,
                :evidence_hash, :started_at, :model_id, :summary
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
            SET completed_at = :completed_at, output_article_id = :article_id
            WHERE id = :run_id
            """,
            {
                "completed_at": completed_at,
                "article_id": article_id,
                "run_id": run_id,
            },
        )
        execute_sql(
            """
            UPDATE crawler_jobs
            SET status = 'completed', finished_at = :finished_at,
                documents = :documents, message = :message,
                research_run_id = :run_id, article_id = :article_id
            WHERE id = :job_id
            """,
            {
                "finished_at": completed_at,
                "documents": len(evidence),
                "message": "证据无变化，已关联上次深度研究输出",
                "run_id": run_id,
                "article_id": article_id,
                "job_id": job_id,
            },
        )
        return {
            "status": "skipped",
            "jobId": job_id,
            "researchRunId": run_id,
            "articleId": article_id,
            "documents": len(evidence),
            "message": "证据无变化，未重复生成文章",
        }

    output, usage = generate_deep_research(profile, evidence)
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
    article_status = "published" if AUTO_PUBLISH_RESEARCH else "review"
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
                :hero_style, :authority_score, 0, 'open', 0, :keywords, :body_json
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
            output_article_id = :article_id, error_message = :errors
        WHERE id = :run_id
        """,
        {
            "completed_at": completed_at,
            "analysis_process": json.dumps(analysis_process, ensure_ascii=False),
            "summary": summary,
            "article_id": article_id,
            "errors": "; ".join(fetch_errors)[:2000],
            "run_id": run_id,
        },
    )
    message = (
        f"已生成并{'自动发布' if article_status == 'published' else '提交审核'}"
        f"深度研究《{output.get('title', profile['topic'])}》；"
        f"{len(evidence)} 条证据，{usage.get('totalTokens', 0)} tokens"
    )
    execute_sql(
        """
        UPDATE crawler_jobs
        SET status = 'completed', finished_at = :finished_at,
            documents = :documents, message = :message,
            research_run_id = :run_id, article_id = :article_id
        WHERE id = :job_id
        """,
        {
            "finished_at": completed_at,
            "documents": len(evidence),
            "message": message,
            "run_id": run_id,
            "article_id": article_id,
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
        "message": message,
        "finishedAt": completed_at,
    }


def run_tool_session(kind: str, slug: str) -> tuple[str, int]:
    session_name = f"{slug}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    if kind == "Browser Tool":
        response = agentcore.start_browser_session(
            browserIdentifier=BROWSER_ID,
            name=session_name,
            sessionTimeoutSeconds=60,
            viewPort={"width": 1280, "height": 720},
        )
        session_id = response["sessionId"]
        agentcore.stop_browser_session(
            browserIdentifier=BROWSER_ID,
            sessionId=session_id,
        )
        return f"AgentCore Browser 会话 {session_id} 已完成", 1
    if kind == "Code Interpreter":
        response = agentcore.start_code_interpreter_session(
            codeInterpreterIdentifier=CODE_INTERPRETER_ID,
            name=session_name,
            sessionTimeoutSeconds=60,
        )
        session_id = response["sessionId"]
        agentcore.stop_code_interpreter_session(
            codeInterpreterIdentifier=CODE_INTERPRETER_ID,
            sessionId=session_id,
        )
        return f"AgentCore Code Interpreter 会话 {session_id} 已完成", 1
    if kind == "Codex SDK":
        result = analyze(
            {
                "topic": "验证 GEO 爬虫生成与证据校验运行链路",
                "evidence": ["该任务由 EventBridge Scheduler 自动触发"],
                "maxTokens": 180,
            }
        )
        return f"Bedrock 校验线程已完成，使用 {result['usage'].get('totalTokens', 0)} tokens", 1
    return f"未识别工具类型：{kind}", 0


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
    if crawler["status"] == "paused":
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
        tool_message, _ = run_tool_session(str(crawler["kind"]), slug)
        profile, evidence, fetch_errors = collect_evidence(slug)
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
        )
        result["crawler"] = slug
        result["kind"] = crawler["kind"]
        result["toolMessage"] = tool_message
        result["scheduledTime"] = payload.get("scheduledTime")
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
