# GEO Agent Runtime Architecture

## 已部署架构与目标扩展

```text
EventBridge Scheduler（已部署）
        |
        v
Lambda Scheduler Bridge（已部署）
        |
        v
AgentCore Runtime — GEO Orchestrator（已部署）
        |
        +-- Codex SDK worker（已部署）
        |     生成与修复站点专用爬虫程序
        |     输出 CrawlPlan + executable artifact
        |
        +-- AgentCore Code Interpreter（已部署）
        |     执行 Python/JS 爬虫、解析文档和结构化数据
        |
        +-- AgentCore Browser Tool（已部署，Web Bot Auth 已开启）
        |     Web bot auth、JS 渲染、交互式页面与登录态抓取
        |
        +-- Evidence Verifier
        |     去重、来源分级、时间校验、交叉验证
        |
        v
Raw store / normalized documents / entity graph
        |
        v
Bedrock GPT 5.6 Sol — analysis pipeline（已部署并验证）
        |
        +-- AI 行业动向
        +-- Agent 技术进展
        +-- 云厂商新服务
        +-- 电商 / 媒体 + AI
        +-- 金融证券市场研判
        |
        v
GEO Publisher
        |
        +-- Variant A: 开放页面 + JSON-LD + 引用块
        +-- Variant B: HTTP 402 + x402 payment requirements
                      Stripe / Privy 独立买方与出版方钱包（已验证）
        |
        v
Event stream -> traffic attribution -> A/B metrics -> dashboard
```

云数据库使用 Aurora PostgreSQL 17.7 Serverless v2（0.5–2 ACU，不自动暂停）与 Data API。Runtime 健康调用、
Aurora 查询、GPT-5.6 Sol 分析调用，以及 Stripe Privy 钱包在 Base Sepolia 上购买 x402
内容均已通过。Codex SDK 自动生成爬虫、Code Interpreter 执行、Browser Tool 自动化、
证据复核、文章入库与 Variant B 商户收款已经接入生产 Demo。主网钱包、退款、财务对账
与监管流程尚未启用。

EventBridge Scheduler 使用 UTC Cron，通过 ARM64 Python 3.13 Lambda 调用 AgentCore
Runtime。每次运行先在 Aurora 创建任务，再启动对应 Browser、Code Interpreter 或
Bedrock 校验线程，最后写回完成/失败状态。失败事件最多重试两次并进入 SQS DLQ。

## 服务边界

### Codex SDK worker

只负责生成、审查和修复站点适配代码。输入是域名、robots 策略、页面样例和所需字段；
输出是版本化 `CrawlPlan`、爬虫程序、测试样例与风险说明。生产环境应把生成代码放入隔离
工作目录，并在进入 Code Interpreter 前运行静态检查和资源策略检查。

当前生产 Runtime 使用 Codex SDK，并通过 Amazon Bedrock provider 调用
`openai.gpt-5.6-sol`。Codex 生成的站点爬虫经过 AST 安全检查后交给 AgentCore Code
Interpreter 执行；源码、thread、token 用量与工具 session 都写入 Aurora。

### Code Interpreter worker

适合：

- API、RSS、站点地图、PDF、CSV 和可直接请求的 HTML
- 批量清洗、正文抽取、表格处理、时间序列与金融指标计算
- 复现性强、能在受控沙箱中完成的任务

每个任务必须声明 CPU、内存、执行时限、最大下载量和允许访问的域名。

### Browser Tool worker

适合：

- 前端渲染、无限滚动、需要点击或分页的页面
- Web bot auth 授权页面
- 需要浏览器网络日志来发现真实数据接口的站点

先探测普通 HTTP，再按需升级到 Browser Tool，以控制成本。凭据通过 Agent 身份和密钥
服务注入，不写入 prompt、日志或生成代码。

### Evidence Verifier

每条结论至少保存：

- 来源 URL、发布者、抓取时间、发布时间和内容哈希
- 原文证据片段位置，不只保存模型摘要
- 来源等级、时效分和是否被独立来源交叉验证
- 分析模型、prompt 版本和生成时间

金融内容额外记录市场数据截止时间、币种、时区与“非投资建议”标记。

## 内容与 GEO 输出规范

每篇内容同时产出人类页面和机器可读表示：

- 明确的标题、摘要、发布日期、作者/机构和更新记录
- 结论先行的短段落、定义、比较表、FAQ 和可独立引用的数据块
- `Article`、`Dataset`、`FAQPage` 等适用的 JSON-LD
- 实体 ID、来源列表、事实声明到证据的映射
- 开放摘要与付费深度内容的清晰边界

## x402 页面状态机

```text
GET premium resource
  -> 402 + payment requirements
  -> Agent wallet signs payment
  -> retry with payment-signature
  -> AgentCore Payments verifies / settles
  -> 200 + machine-readable content
  -> payment + content delivery events
```

商户实现位于 `backend/x402_payment.py`，买方实现位于
`aws_runtime/crawler_tools.py`。链路只接受 Base Sepolia（`eip155:84532`）USDC 报价，
检查报价上限，并使用短时 Payment Session 约束总支出。2026-09-03 已完成三笔
`0.002 USDC` 端到端结算，出版方钱包收到 `0.006 USDC`，支付后均获得 HTTP 200 内容。

建议把 `request_id`、`agent_identity`、`variant`、`content_id`、`payment_id` 和
`delivery_status` 写入同一条追踪链。不要仅以支付成功计算转化，还应确认内容已完整交付。

## A/B 核心指标

开放页重点看：

- AI 引用率、引用排名、Agent 回访率
- 内容进入回答的段落和来源引擎
- 引用访问带来的人类下游点击

x402 页重点看：

- 402 到签名、签名到结算、结算到交付的分步转化率
- 每千次 Agent 请求收入、每个 Agent/内容品类收入
- 价格弹性、钱包失败率、重复购买与退款

最终使用“总内容价值”评估实验：

```text
总内容价值 = AI 引用带来的归因价值 + x402 净收入 - 抓取/推理/支付成本
```
