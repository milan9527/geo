from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row

from .data_api import DataApiConnection


DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://geo:geo_dev_password@127.0.0.1:55432/geo",
)
USE_AURORA_DATA_API = os.environ.get("AWS_DATA_API", "").lower() in {"1", "true", "yes"}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@contextmanager
def connection() -> Iterator[Any]:
    conn = (
        DataApiConnection()
        if USE_AURORA_DATA_API
        else psycopg.connect(DATABASE_URL, row_factory=dict_row)
    )
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS categories (
    id BIGSERIAL PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    eyebrow TEXT NOT NULL,
    description TEXT NOT NULL,
    accent TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS articles (
    id BIGSERIAL PRIMARY KEY,
    category_id BIGINT NOT NULL REFERENCES categories(id),
    slug TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    dek TEXT NOT NULL,
    summary TEXT NOT NULL,
    author TEXT NOT NULL,
    author_role TEXT NOT NULL,
    read_minutes INTEGER NOT NULL,
    published_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('draft', 'review', 'published')),
    featured BOOLEAN NOT NULL DEFAULT FALSE,
    hero_style TEXT NOT NULL,
    authority_score INTEGER NOT NULL,
    citation_count INTEGER NOT NULL DEFAULT 0,
    access_model TEXT NOT NULL DEFAULT 'open',
    agent_price DOUBLE PRECISION NOT NULL DEFAULT 0,
    keywords TEXT NOT NULL,
    body_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
    id BIGSERIAL PRIMARY KEY,
    article_id BIGINT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    publisher TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    published_at TEXT NOT NULL,
    source_type TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS crawler_agents (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    industries TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('running', 'paused', 'idle', 'error')),
    schedule TEXT NOT NULL,
    last_run TEXT,
    pages_today INTEGER NOT NULL DEFAULT 0,
    success_rate DOUBLE PRECISION NOT NULL DEFAULT 0,
    cost_per_doc DOUBLE PRECISION NOT NULL DEFAULT 0,
    config_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS crawler_jobs (
    id BIGSERIAL PRIMARY KEY,
    agent_id BIGINT NOT NULL REFERENCES crawler_agents(id),
    status TEXT NOT NULL CHECK(status IN ('queued', 'running', 'completed', 'failed')),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    documents INTEGER NOT NULL DEFAULT 0,
    message TEXT NOT NULL DEFAULT '',
    research_run_id BIGINT,
    article_id BIGINT REFERENCES articles(id),
    tool_trace_json TEXT NOT NULL DEFAULT '{}'
);

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
);

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
);

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
);

ALTER TABLE crawler_jobs ADD COLUMN IF NOT EXISTS research_run_id BIGINT;
ALTER TABLE crawler_jobs ADD COLUMN IF NOT EXISTS article_id BIGINT REFERENCES articles(id);
ALTER TABLE crawler_jobs ADD COLUMN IF NOT EXISTS tool_trace_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE research_runs ADD COLUMN IF NOT EXISTS tool_trace_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE research_runs ADD COLUMN IF NOT EXISTS verification_status TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE research_runs ADD COLUMN IF NOT EXISTS verification_json TEXT NOT NULL DEFAULT '{}';

CREATE TABLE IF NOT EXISTS analytics_daily (
    day TEXT PRIMARY KEY,
    human_views INTEGER NOT NULL,
    agent_views INTEGER NOT NULL,
    citations INTEGER NOT NULL,
    clicks INTEGER NOT NULL,
    payments INTEGER NOT NULL,
    revenue DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS traffic_events (
    id BIGSERIAL PRIMARY KEY,
    event_type TEXT NOT NULL,
    visitor_type TEXT NOT NULL,
    agent_name TEXT,
    article_id BIGINT REFERENCES articles(id),
    occurred_at TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    value_type TEXT NOT NULL DEFAULT 'boolean',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS admin_users (
    id BIGSERIAL PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'administrator',
    password_hash TEXT NOT NULL,
    password_salt TEXT NOT NULL,
    password_iterations INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active', 'disabled')),
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until TEXT,
    last_login TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS admin_sessions (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES admin_users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    user_agent TEXT NOT NULL DEFAULT '',
    ip_address TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_articles_category ON articles(category_id);
CREATE INDEX IF NOT EXISTS idx_articles_status ON articles(status);
CREATE INDEX IF NOT EXISTS idx_sources_article ON sources(article_id);
CREATE INDEX IF NOT EXISTS idx_events_occurred ON traffic_events(occurred_at);
CREATE INDEX IF NOT EXISTS idx_research_runs_started ON research_runs(started_at);
CREATE INDEX IF NOT EXISTS idx_research_evidence_run ON research_evidence(run_id);
CREATE INDEX IF NOT EXISTS idx_admin_sessions_user ON admin_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_admin_sessions_expires ON admin_sessions(expires_at);
"""


CATEGORIES = [
    {
        "slug": "ai",
        "name": "AI 行业动向",
        "eyebrow": "ARTIFICIAL INTELLIGENCE",
        "description": "追踪基础模型、推理系统和产业格局的关键变化。",
        "accent": "#5D7CFF",
        "sort_order": 1,
    },
    {
        "slug": "agent",
        "name": "Agent 技术",
        "eyebrow": "AGENT SYSTEMS",
        "description": "深入运行时、工具、身份、记忆和机器支付基础设施。",
        "accent": "#13A691",
        "sort_order": 2,
    },
    {
        "slug": "cloud",
        "name": "云计算",
        "eyebrow": "CLOUD INFRASTRUCTURE",
        "description": "解读云厂商新服务、平台战略与基础设施经济性。",
        "accent": "#3C8BCE",
        "sort_order": 3,
    },
    {
        "slug": "commerce",
        "name": "电商与媒体",
        "eyebrow": "COMMERCE + MEDIA",
        "description": "观察 AI 如何改变内容分发、购买决策和商业模式。",
        "accent": "#C66A52",
        "sort_order": 4,
    },
    {
        "slug": "finance",
        "name": "金融市场",
        "eyebrow": "MARKETS + CAPITAL",
        "description": "提供数据驱动的科技产业与证券市场研究框架。",
        "accent": "#A6782A",
        "sort_order": 5,
    },
]


def article(
    slug: str,
    category: str,
    title: str,
    dek: str,
    summary: str,
    author: str,
    role: str,
    read_minutes: int,
    published_at: str,
    hero_style: str,
    authority: int,
    citations: int,
    keywords: list[str],
    sections: list[dict],
    sources: list[tuple[str, str, str, str, str]],
    *,
    featured: bool = False,
    access_model: str = "open",
    price: float = 0,
    status: str = "published",
) -> dict:
    return {
        "slug": slug,
        "category": category,
        "title": title,
        "dek": dek,
        "summary": summary,
        "author": author,
        "author_role": role,
        "read_minutes": read_minutes,
        "published_at": published_at,
        "updated_at": published_at,
        "status": status,
        "featured": featured,
        "hero_style": hero_style,
        "authority_score": authority,
        "citation_count": citations,
        "access_model": access_model,
        "agent_price": price,
        "keywords": json.dumps(keywords, ensure_ascii=False),
        "body_json": json.dumps(sections, ensure_ascii=False),
        "sources": sources,
    }


ARTICLES = [
    article(
        "agent-runtime-control-plane",
        "agent",
        "Agent Runtime 正在成为 AI 应用的新控制平面",
        "模型能力趋同之后，身份、工具执行、状态管理与可观测性开始决定企业 Agent 的生产上限。",
        "企业正在把 Agent 从对话功能升级为能够持续执行任务的数字劳动力。新的架构重心并不是更长的 prompt，而是一个可治理、可审计、可伸缩的运行时。",
        "林致远",
        "首席 Agent 架构研究员",
        12,
        "2026-09-03T00:30:00+00:00",
        "network",
        96,
        2842,
        ["Agent Runtime", "工具调用", "身份治理", "可观测性"],
        [
            {
                "type": "lead",
                "heading": "核心判断",
                "paragraphs": [
                    "Agent 应用的竞争正从“模型能否回答”转向“系统能否完成”。当任务包含浏览器操作、代码执行、支付和跨系统写入时，运行时承担了传统操作系统与云控制平面的双重角色。",
                    "这意味着企业评估 Agent 平台时，需要把隔离、身份、工具权限、状态恢复和成本归因放在模型榜单之前。",
                ],
            },
            {
                "type": "thesis",
                "heading": "为什么运行时成为关键基础设施",
                "number": "01",
                "paragraphs": [
                    "单轮推理是短暂的，但真实任务具有持续状态。研究 Agent 可能跨越数十个网页和多个小时，客户服务 Agent 需要继承历史上下文，交易 Agent 还必须保持身份与支付授权的一致性。",
                    "运行时把模型请求组织成可恢复的任务单元，并对每次工具调用建立确定的安全边界。它同时负责会话、文件、网络、凭据和执行资源的生命周期。",
                ],
                "stat": {"value": "4×", "label": "采用受控运行时后，可定位失败步骤的速度提升"},
            },
            {
                "type": "matrix",
                "heading": "企业级 Agent Runtime 的五层能力",
                "headers": ["能力层", "核心问题", "生产要求"],
                "rows": [
                    ["身份与授权", "谁在代表谁执行？", "短期凭据、最小权限、完整审计"],
                    ["工具执行", "代码和浏览器在哪里运行？", "沙箱隔离、域名策略、资源上限"],
                    ["状态与记忆", "任务失败后如何恢复？", "检查点、版本化上下文、幂等"],
                    ["可观测性", "模型为什么做出这一步？", "端到端追踪、成本与质量归因"],
                    ["经济层", "任务成本与价值如何闭环？", "预算、支付、结算与收入事件"],
                ],
            },
            {
                "type": "analysis",
                "heading": "平台选择不应只看模型目录",
                "number": "02",
                "paragraphs": [
                    "模型可替换性正在提高，而执行上下文、身份策略和数据管道往往与平台深度耦合。架构上应把推理层与运行层解耦，通过稳定的任务、工具和事件接口连接。",
                    "对于大多数企业，最有价值的不是完全自主，而是可预测的半自主：高频低风险步骤自动完成，高影响决策保留策略检查和人工确认。",
                ],
                "quote": "Agent 的可靠性不是模型属性，而是系统属性。",
            },
            {
                "type": "outlook",
                "heading": "接下来值得关注的三件事",
                "bullets": [
                    "浏览器、代码解释器和企业 API 将收敛为统一的受策略约束工具层。",
                    "机器身份与机器支付会进入同一条授权链，Agent 可以在预算内自主购买数据或服务。",
                    "评估体系将从单轮答案质量转向任务完成率、人工接管率和单位成功任务成本。",
                ],
            },
        ],
        [
            ("AWS", "Amazon Bedrock AgentCore product documentation", "https://aws.amazon.com/bedrock/agentcore/", "2026-08-18", "官方文档"),
            ("OpenAI", "Codex SDK documentation", "https://developers.openai.com/codex/sdk", "2026-08-25", "官方文档"),
            ("NIST", "AI Risk Management Framework", "https://www.nist.gov/itl/ai-risk-management-framework", "2026-08-20", "治理框架"),
        ],
        featured=True,
        access_model="hybrid",
        price=0.05,
    ),
    article(
        "reasoning-model-system-efficiency",
        "ai",
        "推理模型进入“系统效率”竞争",
        "能力、延迟与成本形成新的不可能三角，企业需要重写模型评估方法。",
        "推理模型的绝对能力仍在增长，但生产环境的差异越来越多地来自系统设计：缓存、路由、工具编排、验证和预算控制。",
        "许知行",
        "AI 产业分析师",
        9,
        "2026-09-02T08:00:00+00:00",
        "orb",
        93,
        2106,
        ["推理模型", "模型评估", "推理成本", "系统效率"],
        [
            {
                "type": "lead",
                "heading": "从模型分数转向任务经济性",
                "paragraphs": [
                    "企业过去用通用基准判断模型能力，现在更需要回答三个问题：任务是否完成、需要多久、总成本是多少。模型只是这条链路中的一个变量。",
                    "在工具密集型任务中，一次错误调用可能抵消更强推理能力带来的收益，因此端到端评估比单轮准确率更接近真实生产力。",
                ],
            },
            {
                "type": "matrix",
                "heading": "新的评估记分卡",
                "headers": ["指标", "传统方法", "生产方法"],
                "rows": [
                    ["质量", "基准题准确率", "端到端任务完成率"],
                    ["速度", "首字延迟", "完成任务总时长"],
                    ["成本", "每百万 Token", "每个成功任务成本"],
                    ["可靠性", "平均得分", "长链路失败与恢复率"],
                ],
            },
            {
                "type": "analysis",
                "heading": "推理预算需要动态分配",
                "number": "01",
                "paragraphs": [
                    "不是每一步都值得使用最高推理强度。分类、检索和格式转换可由低成本路径承担，高不确定性决策再升级到深度推理。",
                    "成熟系统会根据任务风险、证据冲突和历史成功率动态路由，在质量约束下寻求最低综合成本。",
                ],
                "stat": {"value": "37%", "label": "分层路由在同等完成率下的示例成本降幅"},
            },
            {
                "type": "outlook",
                "heading": "对内容生产者的含义",
                "bullets": [
                    "清晰定义、结构化比较和可验证事实可减少模型检索与推理成本。",
                    "提供更新时间、作者身份和证据链，有助于模型在冲突信息中选择权威来源。",
                    "内容应同时服务快速摘要和深度研究两类推理预算。",
                ],
            },
        ],
        [
            ("MLCommons", "Inference benchmarks", "https://mlcommons.org/benchmarks/inference-datacenter/", "2026-08-20", "行业基准"),
            ("OpenAI", "Model and API documentation", "https://developers.openai.com/", "2026-08-28", "官方文档"),
            ("Stanford HAI", "AI Index resources", "https://hai.stanford.edu/ai-index", "2026-04-15", "行业报告"),
        ],
        featured=True,
    ),
    article(
        "cloud-agent-platform-control-plane",
        "cloud",
        "云厂商的 Agent 平台之战",
        "无服务器运行时、浏览器工具和机器身份，正在组成下一代云应用栈。",
        "云平台从托管模型快速扩展到 Agent 全生命周期。企业选择平台时，应重点比较执行边界、治理能力和任务级经济性。",
        "唐若云",
        "云计算研究总监",
        10,
        "2026-09-01T11:20:00+00:00",
        "blocks",
        94,
        1849,
        ["云计算", "Agent 平台", "无服务器", "开发者工具"],
        [
            {
                "type": "lead",
                "heading": "模型平台正在升级为执行平台",
                "paragraphs": [
                    "第一阶段的云 AI 竞争集中在模型可用性，第二阶段则围绕 Agent 如何安全地访问网络、运行代码和调用企业系统展开。",
                    "运行时、网关、身份和可观测性将决定平台能否承载从实验到大规模生产的跃迁。",
                ],
            },
            {
                "type": "thesis",
                "heading": "四个关键比较维度",
                "number": "01",
                "paragraphs": [
                    "企业需要比较运行时隔离、工具生态、身份继承和开放框架兼容性。平台功能数量不是重点，边界是否清晰、审计是否完整更重要。",
                    "对于跨云部署，任务定义与事件数据应保持可移植，模型和工具适配器则作为可替换组件。",
                ],
            },
            {
                "type": "matrix",
                "heading": "平台能力清单",
                "headers": ["层级", "基础能力", "高阶能力"],
                "rows": [
                    ["模型", "统一调用与路由", "基于任务的动态选择"],
                    ["执行", "沙箱与伸缩", "长任务恢复与检查点"],
                    ["工具", "API 与代码执行", "浏览器、支付和企业连接器"],
                    ["治理", "日志与权限", "意图、策略和成本审计"],
                ],
            },
            {
                "type": "outlook",
                "heading": "采购建议",
                "bullets": [
                    "用三个真实业务任务做端到端压力测试，而不是只比较功能清单。",
                    "明确运行时数据、记忆和追踪数据的导出路径。",
                    "单独测量模型成本、工具成本和失败重试成本。",
                ],
            },
        ],
        [
            ("AWS", "Bedrock AgentCore", "https://aws.amazon.com/bedrock/agentcore/", "2026-08-18", "官方文档"),
            ("Google Cloud", "Vertex AI platform", "https://cloud.google.com/vertex-ai", "2026-08-20", "官方文档"),
            ("Microsoft Azure", "Azure AI platform", "https://azure.microsoft.com/products/ai-services", "2026-08-20", "官方文档"),
        ],
    ),
    article(
        "agent-commerce-delegated-buying",
        "commerce",
        "从搜索框到委托购买：Agent Commerce 重写流量规则",
        "当消费者把比较与购买交给 Agent，品牌需要为机器决策重新设计商品事实与信任信号。",
        "Agent 不会像人一样浏览广告页面。它更依赖结构化商品事实、政策透明度、实时库存和可验证评价，品牌的内容架构因此成为新的转化基础设施。",
        "周意",
        "消费与媒体研究负责人",
        8,
        "2026-08-31T06:40:00+00:00",
        "commerce",
        91,
        1630,
        ["Agent Commerce", "电商", "机器支付", "商品数据"],
        [
            {
                "type": "lead",
                "heading": "新的购买入口不是页面，而是委托",
                "paragraphs": [
                    "用户给 Agent 的不是关键词，而是约束：预算、用途、交付时间和偏好。Agent 会跨平台收集信息、比较证据并完成交易。",
                    "品牌若不能提供稳定、机器可读且可验证的信息，即使拥有优秀的人类页面，也可能在候选生成阶段被排除。",
                ],
            },
            {
                "type": "thesis",
                "heading": "商品详情页需要双重表达",
                "number": "01",
                "paragraphs": [
                    "人类需要叙事、视觉和品牌感受；Agent 需要规格、兼容性、库存、价格历史和政策。两者不是互斥版本，而是同一事实层的不同表达。",
                    "企业应建立商品声明到证据的映射，确保营销内容、结构化数据和 API 返回保持一致。",
                ],
                "quote": "在 Agent 渠道里，可验证性就是新的品牌力。",
            },
            {
                "type": "matrix",
                "heading": "Agent 购买漏斗",
                "headers": ["阶段", "传统信号", "Agent 信号"],
                "rows": [
                    ["发现", "广告与排名", "实体匹配与需求约束"],
                    ["比较", "页面浏览", "结构化规格与独立证据"],
                    ["信任", "品牌与评价", "来源、政策与历史一致性"],
                    ["交易", "表单结账", "身份、预算和机器支付"],
                ],
            },
            {
                "type": "outlook",
                "heading": "品牌现在可以做什么",
                "bullets": [
                    "建立可公开抓取的产品知识层，并提供稳定实体标识。",
                    "监控主要 Agent 是否正确理解价格、库存和退换政策。",
                    "为高价值实时数据测试按调用或按结果收费。",
                ],
            },
        ],
        [
            ("Stripe", "Agentic commerce resources", "https://stripe.com/use-cases/agentic-commerce", "2026-08-19", "行业资料"),
            ("W3C", "Payment Request API", "https://www.w3.org/TR/payment-request/", "2026-08-20", "技术标准"),
            ("Schema.org", "Product structured data vocabulary", "https://schema.org/Product", "2026-07-10", "标准"),
        ],
        access_model="hybrid",
        price=0.03,
    ),
    article(
        "ai-capex-cashflow-validation",
        "finance",
        "AI CapEx 周期下半场：从算力扩张转向现金流验证",
        "市场关注点正在从投入规模切换到收入兑现、资产周转和自由现金流。",
        "AI 基础设施投资仍是科技资产的重要变量，但估值驱动逻辑正在发生变化。研究框架需要同时跟踪资本开支、AI 收入和现金流的传导效率。",
        "顾闻川",
        "科技与资本市场策略师",
        11,
        "2026-08-30T13:15:00+00:00",
        "market",
        95,
        2321,
        ["AI CapEx", "现金流", "科技股", "证券研究"],
        [
            {
                "type": "lead",
                "heading": "从投入叙事进入回报验证",
                "paragraphs": [
                    "资本开支高增长在周期早期代表需求信心，在周期中后期则必须与收入增速、资产利用率和自由现金流共同判断。",
                    "市场将逐步区分基础设施确定性与应用层兑现能力，产业链内部的估值相关性可能下降。",
                ],
            },
            {
                "type": "matrix",
                "heading": "AI 投资周期观察框架",
                "headers": ["变量", "积极信号", "风险信号"],
                "rows": [
                    ["资本开支", "订单与收入同步增长", "投入增速持续高于变现"],
                    ["利用效率", "单位推理成本下降", "闲置与折旧压力上升"],
                    ["应用收入", "续费与 ARPU 改善", "试点多、规模化少"],
                    ["现金流", "经营现金流覆盖投资", "融资依赖持续提高"],
                ],
            },
            {
                "type": "analysis",
                "heading": "上游与应用层需要不同估值锚",
                "number": "01",
                "paragraphs": [
                    "上游资产更受产能、订单能见度和供需边际影响；应用层则取决于客户留存、单位经济性与服务成本下降。",
                    "把所有 AI 资产放在同一增长框架中会掩盖现金流质量差异，组合管理需要按价值链位置拆分。",
                ],
                "stat": {"value": "3 个", "label": "核心领先指标：订单、利用率、单位收入成本"},
            },
            {
                "type": "outlook",
                "heading": "风险提示",
                "bullets": [
                    "宏观利率变化会显著影响长久期科技资产估值。",
                    "供应扩张可能快于终端需求释放，造成阶段性价格压力。",
                    "本文为研究框架，不构成任何证券或投资建议。",
                ],
            },
        ],
        [
            ("U.S. BEA", "Investment in Fixed Assets", "https://www.bea.gov/data/investment-fixed-assets", "2026-08-20", "官方经济数据"),
            ("U.S. SEC", "Company filings database", "https://www.sec.gov/edgar", "2026-08-29", "监管数据"),
            ("Federal Reserve", "Economic data", "https://fred.stlouisfed.org/", "2026-08-28", "宏观数据"),
        ],
        access_model="hybrid",
        price=0.12,
    ),
    article(
        "geo-evidence-architecture",
        "ai",
        "GEO 的真正壁垒不是关键词，而是证据架构",
        "生成式引擎优先选择可验证、可拆分、可追溯的内容单元。",
        "GEO 不等于把 SEO 文案改得更像答案。持续获得 AI 引用，需要内容团队建立事实声明、实体、来源和更新时间之间的结构化关系。",
        "许知行",
        "AI 产业分析师",
        7,
        "2026-08-29T09:00:00+00:00",
        "evidence",
        94,
        1988,
        ["GEO", "证据架构", "AI 引用", "结构化内容"],
        [
            {
                "type": "lead",
                "heading": "从页面优化走向知识优化",
                "paragraphs": [
                    "搜索引擎通常把页面作为排序单元，生成式引擎更可能抽取页面中的定义、数字、比较和结论。内容的最小竞争单元因此变成可独立引用的事实块。",
                    "事实块必须保留上下文、时间和来源，否则模型即使抽取到内容，也难以判断其可信度。",
                ],
            },
            {
                "type": "thesis",
                "heading": "四层证据架构",
                "number": "01",
                "paragraphs": [
                    "第一层是实体：明确公司、产品、人物和概念。第二层是声明：每句话具体声称什么。第三层是证据：数据来自哪里。第四层是时效：结论在什么时间范围内成立。",
                    "这套结构不仅提高机器理解，也能帮助编辑团队发现过期事实和证据冲突。",
                ],
            },
            {
                "type": "matrix",
                "heading": "GEO 内容质量信号",
                "headers": ["信号", "弱内容", "强内容"],
                "rows": [
                    ["作者", "无身份或泛化团队", "明确专家与研究职责"],
                    ["数据", "孤立数字", "口径、时间和来源完整"],
                    ["结构", "长篇连续叙述", "定义、结论、比较可独立引用"],
                    ["更新", "仅显示发布日期", "保留修订记录与变化原因"],
                ],
            },
            {
                "type": "outlook",
                "heading": "内容团队的实施顺序",
                "bullets": [
                    "先为高价值主题建立实体和来源规范。",
                    "再改造文章模板，强制记录声明与证据映射。",
                    "最后用 Agent 引用日志反向判断哪些内容单元最有价值。",
                ],
            },
        ],
        [
            ("Google Search Central", "Article structured data", "https://developers.google.com/search/docs/appearance/structured-data/article", "2026-08-20", "官方文档"),
            ("Schema.org", "Article vocabulary", "https://schema.org/Article", "2026-07-10", "标准"),
            ("W3C", "Web provenance resources", "https://www.w3.org/TR/prov-overview/", "2026-06-01", "标准"),
        ],
    ),
    article(
        "browser-agents-web-access",
        "agent",
        "Browser Agent 进入生产环境前，必须解决的七个边界",
        "网页自动化的难点不在点击，而在身份、授权、注入攻击和结果验证。",
        "浏览器工具让 Agent 可以操作没有 API 的系统，但也扩大了网页内容影响模型决策的攻击面。生产部署需要明确网络、凭据和操作风险边界。",
        "林致远",
        "首席 Agent 架构研究员",
        10,
        "2026-08-28T10:30:00+00:00",
        "browser",
        92,
        1451,
        ["Browser Agent", "网页自动化", "安全", "Web bot auth"],
        [
            {
                "type": "lead",
                "heading": "浏览器把开放世界带进了 Agent 上下文",
                "paragraphs": [
                    "API 工具通常拥有稳定结构和明确权限，网页则包含不可信文本、动态交互和模糊边界。Agent 在页面上看到的指令不应自动获得与用户指令相同的权重。",
                    "安全设计必须假设页面可能包含恶意内容，并对高影响操作设置独立确认机制。",
                ],
            },
            {
                "type": "matrix",
                "heading": "七个生产边界",
                "headers": ["边界", "控制措施", "观测指标"],
                "rows": [
                    ["域名", "允许列表与重定向检查", "越界请求数"],
                    ["身份", "短期令牌与任务隔离", "凭据使用轨迹"],
                    ["内容", "网页指令与用户意图区分", "注入拦截率"],
                    ["操作", "写入与交易二次确认", "人工接管率"],
                    ["资源", "时间、下载和步骤上限", "单位任务成本"],
                    ["证据", "截图与网络日志留存", "结果可复核率"],
                    ["隐私", "敏感字段识别与脱敏", "敏感数据暴露数"],
                ],
            },
            {
                "type": "analysis",
                "heading": "Web bot auth 比共享账号更适合机器访问",
                "number": "01",
                "paragraphs": [
                    "机器身份应与人类账号分离，具备清晰用途、权限和预算。这样站点能够识别合法 Agent，也能对不同数据和操作建立差异化策略。",
                    "认证并不等于完全信任。Agent 的每次高影响操作仍需通过意图和策略检查。",
                ],
            },
            {
                "type": "outlook",
                "heading": "上线检查清单",
                "bullets": [
                    "为浏览器任务定义可接受的终止状态与最大步骤数。",
                    "保存关键页面截图、DOM 证据和网络请求摘要。",
                    "模拟 prompt injection、恶意下载和授权过期场景。",
                ],
            },
        ],
        [
            ("OWASP", "LLM application security resources", "https://owasp.org/www-project-top-10-for-large-language-model-applications/", "2026-08-12", "安全标准"),
            ("W3C", "WebDriver specification", "https://www.w3.org/TR/webdriver2/", "2026-08-20", "技术标准"),
            ("AWS", "AgentCore Browser documentation", "https://docs.aws.amazon.com/bedrock-agentcore/", "2026-08-18", "官方文档"),
        ],
    ),
    article(
        "media-agent-licensing",
        "commerce",
        "媒体内容授权进入 Agent 计量时代",
        "按订阅、按调用与按结果三种模式，将共同塑造机器内容市场。",
        "当 Agent 直接消费研究、新闻和数据库，媒体需要把版权政策转化为可执行的机器访问规则，并建立从身份到结算的完整事件链。",
        "周意",
        "消费与媒体研究负责人",
        8,
        "2026-08-27T04:20:00+00:00",
        "media",
        90,
        1124,
        ["媒体", "内容授权", "x402", "Agent 流量"],
        [
            {
                "type": "lead",
                "heading": "机器访问需要新的许可产品",
                "paragraphs": [
                    "传统订阅面向人类阅读时长，Agent 更关注可否检索、引用、总结和用于后续任务。权限需要细化到内容类型与使用目的。",
                    "开放摘要可以扩大引用范围，深度数据和实时分析则适合按调用收费。",
                ],
            },
            {
                "type": "matrix",
                "heading": "三种商业模式",
                "headers": ["模式", "适用内容", "核心指标"],
                "rows": [
                    ["订阅", "持续更新的专业知识库", "活跃 Agent 与留存"],
                    ["按调用", "单篇、数据集和实时信号", "每千次请求收入"],
                    ["按结果", "线索、交易与研究任务", "成功结果与分成"],
                ],
            },
            {
                "type": "analysis",
                "heading": "计量能力比支付按钮更重要",
                "number": "01",
                "paragraphs": [
                    "媒体需要识别 Agent 身份、内容用途、引用行为和下游价值。只有把访问、授权、支付与内容交付连接起来，才能正确评估价格。",
                    "机器访问日志也会成为编辑信号，帮助内容团队识别高价值事实和未满足的问题。",
                ],
            },
            {
                "type": "outlook",
                "heading": "实施原则",
                "bullets": [
                    "保留足够开放内容，支持发现与引用。",
                    "对实时性、独占性和结构化程度更高的内容收费。",
                    "让机器清楚理解许可范围、价格和可接受用途。",
                ],
            },
        ],
        [
            ("W3C", "ODRL Information Model", "https://www.w3.org/TR/odrl-model/", "2026-08-20", "许可标准"),
            ("Stripe", "Machine payments resources", "https://stripe.com/", "2026-08-19", "行业资料"),
            ("W3C", "HTTP status code specifications", "https://www.rfc-editor.org/rfc/rfc9110", "2026-05-01", "标准"),
        ],
        access_model="hybrid",
        price=0.05,
        status="review",
    ),
]


AGENTS = [
    ("Research Coder", "research-coder", "Code Interpreter", ["AI", "云计算"], "running", "*/20 * * * *"),
    ("Render Scout", "render-scout", "Browser Tool", ["电商", "媒体"], "running", "*/15 * * * *"),
    ("Market Signal", "market-signal", "Code Interpreter", ["金融", "证券"], "running", "0 */1 * * *"),
    ("Evidence Verifier", "evidence-verifier", "Codex SDK", ["全部行业"], "running", "*/10 * * * *"),
    ("Cloud Release Watch", "cloud-release-watch", "Browser Tool", ["云计算"], "idle", "0 */2 * * *"),
    ("Commerce Feed Miner", "commerce-feed-miner", "Code Interpreter", ["电商"], "paused", "0 */3 * * *"),
]


def init_db() -> None:
    with connection() as conn:
        for statement in SCHEMA.split(";"):
            if statement.strip():
                conn.execute(statement)
        category_count = conn.execute(
            "SELECT COUNT(*) AS count FROM categories"
        ).fetchone()["count"]
        if category_count == 0:
            _seed_categories(conn)
            _seed_articles(conn)
            _seed_agents(conn)
        settings_count = conn.execute(
            "SELECT COUNT(*) AS count FROM app_settings"
        ).fetchone()["count"]
        if settings_count == 0:
            _seed_settings(conn)


def _seed_categories(conn: Any) -> None:
    conn.cursor().executemany(
        """
        INSERT INTO categories(slug, name, eyebrow, description, accent, sort_order)
        VALUES(%(slug)s, %(name)s, %(eyebrow)s, %(description)s, %(accent)s, %(sort_order)s)
        """,
        CATEGORIES,
    )


def _seed_articles(conn: Any) -> None:
    category_ids = {row["slug"]: row["id"] for row in conn.execute("SELECT id, slug FROM categories")}
    for item in ARTICLES:
        cursor = conn.execute(
            """
            INSERT INTO articles(
                category_id, slug, title, dek, summary, author, author_role,
                read_minutes, published_at, updated_at, status, featured,
                hero_style, authority_score, citation_count, access_model,
                agent_price, keywords, body_json
            ) VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                category_ids[item["category"]],
                item["slug"],
                item["title"],
                item["dek"],
                item["summary"],
                item["author"],
                item["author_role"],
                item["read_minutes"],
                item["published_at"],
                item["updated_at"],
                item["status"],
                item["featured"],
                item["hero_style"],
                item["authority_score"],
                len(item["sources"]),
                item["access_model"],
                item["agent_price"],
                item["keywords"],
                item["body_json"],
            ),
        )
        article_id = cursor.fetchone()["id"]
        conn.cursor().executemany(
            """
            INSERT INTO sources(article_id, publisher, title, url, published_at, source_type)
            VALUES(%s, %s, %s, %s, %s, %s)
            """,
            [(article_id, *source) for source in item["sources"]],
        )


def _seed_agents(conn: Any) -> None:
    for item in AGENTS:
        conn.execute(
            """
            INSERT INTO crawler_agents(
                name, slug, kind, industries, status, schedule, last_run,
                pages_today, success_rate, cost_per_doc, config_json
            ) VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                item[0],
                item[1],
                item[2],
                json.dumps(item[3], ensure_ascii=False),
                item[4],
                item[5],
                None,
                0,
                0,
                0,
                json.dumps({"maxPages": 12000, "respectRobots": True}),
            ),
        )


def _seed_settings(conn: Any) -> None:
    now = utc_now()
    settings = [
        ("agent_user_agent_detection", "true", "boolean", now),
        ("machine_content_endpoint", "true", "boolean", now),
        ("automatic_json_ld", "true", "boolean", now),
        ("x402_payments", "true", "boolean", now),
        ("payment_failure_alerts", "true", "boolean", now),
        ("analytics_retention_days", "120", "integer", now),
    ]
    conn.cursor().executemany(
        """
        INSERT INTO app_settings(key, value, value_type, updated_at)
        VALUES(%s, %s, %s, %s)
        """,
        settings,
    )
