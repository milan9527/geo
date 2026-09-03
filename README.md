# Aperture GEO Intelligence

专业 GEO 内容平台 Demo，采用前台、管理后台、API 与数据库分离架构。

## 架构

```text
frontend/public   公开研究网站       http://127.0.0.1:4173
frontend/admin    独立管理后台       http://127.0.0.1:4174
backend           JSON API          http://127.0.0.1:8000
PostgreSQL 16     本地开发数据库     127.0.0.1:55432
Aurora PostgreSQL AWS 持久化数据库   Data API
aws_runtime       AgentCore Runtime 生产容器
agent_runtime     Codex SDK 爬虫生成器
```

公开网站不包含后台入口或运营数据。GEO 统计、内容管理、爬虫 Agent 和任务记录只在独立管理后台展示。
公开站和管理站通过同源 `/api`、`/agent` 路径反向代理到独立 API 服务，以兼容 IDE
端口预览、HTTPS 代理和远程开发环境。

## 本地 PostgreSQL 启动

本地需要 Python 3.10+（推荐 3.13）、Node.js 24+ 和 Docker。启动脚本会优先选择
Python 3.13；若检测到旧版 `.venv`，会先归档旧环境再重建。

使用一键脚本创建 Python 虚拟环境、安装 `psycopg`、启动 PostgreSQL 并运行三个应用服务：

```bash
./scripts/start.sh
```

启动后访问：

- 用户内容站：`http://127.0.0.1:4173`
- 管理后台：`http://127.0.0.1:4174`
- API 健康检查：`http://127.0.0.1:8000/api/health`

首次启动会创建 PostgreSQL Docker 持久化卷、自动建表，并写入带官方来源的起始内容。
流量、点击、Agent 访问、引用和支付统计只从真实 `traffic_events` 聚合，不生成模拟统计。
数据库连接可通过 `DATABASE_URL` 覆盖，默认值为：

```text
postgresql://geo:geo_dev_password@127.0.0.1:55432/geo
```

首次使用管理后台时，在另一个终端创建本地管理员：

```bash
PYTHONPATH=. .venv/bin/python scripts/create_admin_user.py \
  --username admin \
  --display-name "Aperture Administrator" \
  --generate
```

停止应用服务使用 `Ctrl+C`。停止 PostgreSQL 容器：

```bash
./scripts/stop.sh
```

执行完整端到端验证：

```bash
PYTHONPATH=. .venv/bin/python scripts/smoke_test.py
```

测试会真实验证 PostgreSQL 健康状态、内容创建和发布、搜索、Agent 内容端点、访问事件、
GEO 聚合统计、设置持久化、爬虫启停与单个/批量任务调度，并在结束后清理临时测试数据。

## AWS 模式启动

项目已在 `us-east-1` 创建独立 AWS 资源。应用使用标准 AWS credential chain，
不在代码或 `.env.aws` 中保存数据库密码；Aurora 主密码由 Secrets Manager 托管。

```bash
./scripts/start_aws.sh
```

该命令加载 `.env.aws`，API 通过 Aurora Data API 访问云数据库，不启动本地 PostgreSQL。
验证云数据库的全部前后台功能：

```bash
set -a
source .env.aws
set +a
export GEO_ADMIN_USERNAME=admin
export GEO_ADMIN_PASSWORD='创建管理员时生成的密码'
PYTHONPATH=. .venv/bin/python scripts/smoke_test.py
```

### AWS Web 部署

生产 Web 入口使用两套独立的私有 S3 + CloudFront OAC 分发，分别承载公开站和管理后台。
CloudFront 的 `/api/*` 与 `/agent/*` 行为转发到 ALB 后的 ECS Fargate API，因此浏览器
请求、管理员 Cookie 和 Agent 机器接口保持同源。

```text
Public CloudFront ----> private public S3 (OAC)
                   \
                    +--> ALB --> ECS Fargate API --> Aurora Data API
                   /
Admin CloudFront -----> private admin S3 (OAC)
```

执行部署：

```bash
chmod +x scripts/deploy_web_ecs.sh
./scripts/deploy_web_ecs.sh
```

脚本只使用 AWS CLI 和各服务 API，不调用 CloudFormation、CDK 或 SAM。它会构建 ARM64
后端镜像，等待 ECR 扫描确认无 Critical/High 漏洞后注册新的 ECS task definition，
滚动更新 Fargate 服务、同步两个 S3 前端并创建 CloudFront invalidation。

现有 S3、OAC、CloudFront、ALB、IAM 与 ECS 基础资源按稳定名称和 ID 发现；部署脚本不会
重建或替换分发，因此线上 URL 保持不变。ALB 仅接受 AWS CloudFront origin-facing
前缀列表流量，并要求分发注入 origin verification header；ECS 任务只接受 ALB
安全组访问。

当前线上入口：

- 公开站：`https://d1tsbnft7iv51.cloudfront.net`
- 管理后台：`https://deu7vkdd3jf5.cloudfront.net`
- API 健康检查：`https://d1tsbnft7iv51.cloudfront.net/api/health`

部署资源输出保存在本机忽略提交的 `.env.deploy.aws`。后端基于固定 digest 的
Python 3.13 Alpine ARM64 镜像，以非 root UID `10001` 运行；当前 ECR 扫描无发现。

### 已创建的 AWS 资源

| 服务 | 资源 | 状态/配置 |
| --- | --- | --- |
| Aurora | `geo-intelligence-demo` | PostgreSQL 17.7、Serverless v2 0.5–2 ACU、不自动暂停、Data API |
| Aurora | `geo-intelligence-demo-writer` | `db.serverless` 写节点 |
| Secrets Manager | Aurora 托管主凭证 | 应用只保存 Secret ARN |
| Bedrock | `geo-intelligence-sol` | GPT-5.6 Sol 应用推理配置 |
| AgentCore Runtime | `geo_intelligence_agent` | HTTP、PUBLIC、ARM64、非 root、不可变 digest、READY |
| AgentCore Browser | `geo_intelligence_browser` | PUBLIC、Web Bot Auth 签名已开启 |
| AgentCore Code Interpreter | `geo_intelligence_code` | PUBLIC、READY |
| AgentCore Payments | `DemoPaymentManager` | Stripe Privy 钱包 ACTIVE、Base Sepolia x402 已验证 |
| S3 + CloudFront | 公开站 | 私有 bucket、OAC、HTTP/2/3、Deployed |
| S3 + CloudFront | 管理后台 | 私有 bucket、OAC、HTTP/2/3、Deployed |
| ECS Fargate | `geo-intelligence-api` | ARM64、1/1 healthy、Container Insights |
| ALB | `geo-intelligence-alb` | 仅允许 CloudFront origin-facing 网络 |
| ECR | `geo-intelligence-api` | Alpine 镜像扫描 0 findings |
| ECR | `geo-intelligence-agent` | 固定 digest 的 Alpine 镜像，扫描 0 findings |
| IAM | `geo-intelligence-agentcore-role` | 项目资源范围内的 Bedrock、Data API、工具和日志权限 |
| EventBridge Scheduler | `geo-intelligence-crawlers` | 6 条 UTC 计划，5 条启用、1 条暂停 |
| Lambda | `geo-intelligence-scheduler-bridge` | ARM64 Python 3.13，Scheduler 到 AgentCore 桥接 |
| SQS | `geo-intelligence-scheduler-dlq` | 调度失败死信队列，保留 14 天 |
| IAM | `geo-intelligence-scheduler-role` | Scheduler 调用 Lambda 与写入 DLQ |
| IAM | `geo-intelligence-scheduler-bridge-role` | Lambda 调用 AgentCore 与写日志 |

资源 ARN 与工具 ID 保存在本机忽略提交的 `.env.aws`。IAM 信任与执行策略位于
[`infrastructure/aws`](./infrastructure/aws)。

AgentCore Runtime 支持：

- `{"action":"health"}`：检查 Aurora 内容数、模型与工具配置
- `{"action":"analyze", ...}`：通过 Bedrock GPT-5.6 Sol 生成专业研究简报
- `{"action":"tool_config"}`：返回 Browser、Code Interpreter 与行业范围
- `{"action":"x402_fetch","url":"..."}`：通过 AgentCore Payments 购买 x402 内容
- `{"action":"scheduled_crawl","crawlerSlug":"..."}`：执行真实抓取、研究、证据审计和入库

容器实现位于 [`aws_runtime`](./aws_runtime)，镜像固定为 ECR digest，避免 `latest`
发生非预期漂移。

### EventBridge 自动调度

```text
EventBridge Scheduler
    -> Lambda scheduler bridge
    -> AgentCore Runtime scheduled_crawl
    -> Browser / Code Interpreter / Bedrock
    -> Aurora crawler_jobs
```

| Agent | 调度频率 | Scheduler 状态 |
| --- | --- | --- |
| Evidence Verifier | 每 10 分钟 | ENABLED |
| Render Scout | 每 15 分钟 | ENABLED |
| Research Coder | 每 20 分钟 | ENABLED |
| Market Signal | 每小时 | ENABLED |
| Cloud Release Watch | 每 2 小时 | ENABLED |
| Commerce Feed Miner | 每 3 小时 | DISABLED，与后台暂停状态一致 |

所有计划使用 UTC、关闭 Flexible Time Window、最多重试 2 次、事件最长保留 300 秒。
重试失败后事件进入 SQS DLQ。管理后台暂停或恢复爬虫时，会同步更新对应 Scheduler
计划状态。Lambda bridge 超时为 900 秒，异步自动重试为 0；Scheduler 事件不会因为
长时间的模型分析而重复执行、重复付费或重复生成文章。

各 Agent 的生产职责与执行工具：

| Agent | 主要职责 | 真实执行链路 |
| --- | --- | --- |
| Research Coder | AI、基础模型、Agent 与云端 AI 基础设施 | Codex SDK 生成 Python 爬虫 → Code Interpreter 执行官方 RSS 抓取 |
| Render Scout | 电商、支付、媒体动态页面 | AgentCore Browser + Web Bot Auth → Playwright/CDP 渲染和正文提取 |
| Market Signal | 科技股、利率与 AI 资本开支研判 | Codex SDK → Code Interpreter → FRED 官方时间序列与变化计算 |
| Evidence Verifier | Agent 技术与全局证据治理 | 官方来源抓取 → 每次滚动复核最久未审计的 2 篇 → 必要时最多两轮修订和复核 |
| Cloud Release Watch | 云厂商新服务和企业 AI 架构 | AgentCore Browser 抓取 AWS、Google Cloud 前端渲染页面 |
| Commerce Feed Miner | 电商 Feed 与机器付费数据 | Codex SDK → Code Interpreter，并可执行受预算约束的 x402 支付抓取 |

Codex 使用 Runtime 的 AWS 身份和 Amazon Bedrock provider 调用
`openai.gpt-5.6-sol`。生成源码在执行前经过 AST 安全检查；网络域名、子进程、原始
socket、动态代码执行、云元数据和宿主文件路径均受限制。源码、Codex thread、token
用量、Code Interpreter/Browser session 和支付交易都写入 Aurora 供后台审计。

重新创建或更新计划：

```bash
PYTHONPATH=. .venv/bin/python scripts/provision_eventbridge.py
```

创建一分钟后自动执行并删除的一次性链路测试：

```bash
PYTHONPATH=. .venv/bin/python scripts/provision_eventbridge.py --preflight
```

### AgentCore Payments / x402 验证

项目已绑定 AgentCore Payment Manager、Stripe Privy connector 和独立的买方/出版方
embedded crypto wallet。公开内容对每篇文章提供同内容 A/B 两个 Agent 接口：

```text
A: GET /agent/v1/articles/{slug}       -> HTTP 200，开放机器内容
B: GET /agent/v1/articles/{slug}/paid  -> HTTP 402，支付后返回同一深度内容
```

Variant B 使用 x402 v2 `exact` scheme、Base Sepolia USDC 和官方 facilitator。出版方
钱包地址为 `0x23a885262A89c32eB582f4439184769dA22c467c`，默认价格为
`0.002 USDC`。端到端链路已经验证：

```text
GET paid resource -> HTTP 402 -> AgentCore ProcessPayment
-> Stripe Privy wallet 签名 -> Base Sepolia 结算 -> HTTP 200 paid content
```

Commerce Feed Miner 已在正常研究任务中购买本站 Variant B 内容。已验证三笔
`0.002 USDC` 交易，出版方钱包余额增加到 `0.006 USDC`；支付 Session、金额、
HTTP 结果和交易哈希均写入 Aurora。代表性交易：
`0x8257af51cd0b8a1efc97dd308f68f930e0ac38a07492ee5288d5141dc612f289`。

为防止无人值守持续扣费，Commerce Feed Miner 的 Scheduler 默认暂停；只有后台手动
运行并明确传入 `allowPayment=true` 才会付款。其他五个 Agent 保持自动调度。

## 用户内容站

- 专业媒体式首页与五类研究频道
- AI、Agent、云计算、电商媒体、金融市场内容
- 完整文章正文、分析框架、对比表和结论
- 作者、更新时间、权威度和可追溯来源
- 站内搜索、相关推荐、响应式布局
- 动态 JSON-LD 和面向 Agent 的机器可读内容端点

机器内容接口示例：

```text
GET /agent/v1/articles/agent-runtime-control-plane
```

接口返回声明、章节、来源、许可模式和 x402 定价信息。对应的 `/paid` 地址返回标准
`PAYMENT-REQUIRED` 挑战头；完成支付后返回 Variant B 内容。

## 管理后台

- 7/30/90 天 GEO、人类流量和 Agent 流量统计
- AI 引用率、Agent 来源、x402 收入与实时事件
- A/B 页面访问、402 挑战、支付转化率和按时间范围统计
- 内容发布状态、权威度、引用次数和访问模式管理
- 爬虫 Agent 启停、立即运行与批量调度
- 研究输出：原始来源、结构化数据、分析过程、专业观点、结论和未来观察指标
- AgentCore 任务记录和运行配置

### 运行爬虫与查看输出

1. 打开管理后台 `http://127.0.0.1:4174`，进入“爬虫 Agent”。
2. 点击单个 Agent 的“立即运行”，或等待 EventBridge Scheduler 自动触发。
3. 在“研究输出”查看完整研究链路：
   - 每条一手来源的发布者、标题、原始链接、发布日期、抓取时间和结构化数据；
   - 问题定义、数据对照、因果约束识别、产业映射等分析过程；
   - 核心观点、证据矩阵、深度行业分析、结论、风险边界和未来观察指标。
4. 研究稿先接受独立证据审计。存在不支持的事实、引用或因果表述时，系统会按审计结果
   自动修订并再次审计；只有二次审计通过的内容才自动发布，并立即出现在用户内容站
   对应分类、文章详情页和面向 Agent 的机器可读内容接口中。未通过的稿件保留在后台
   审核区，后台“内容管理”可以继续修改、发布或撤回。

“任务记录”用于查看运行状态、工具会话、证据数量和错误信息，不是研究正文入口。
来源无变化时，任务会标记为 `skipped` 并关联上一版研究稿，避免重复消耗模型。

研究生成不是抓取摘要拼接。Runtime 会先保存可追溯证据，再调用 GPT-5.6 Sol 执行事实与
观点分离、跨来源对照、因果边界检查和产业影响推演。文章结构强制包含“核心观点”、
“数据与证据”、“分析过程”、“专业观点与产业影响”以及“结论与未来观察”；金融内容
额外强制显示“不构成投资建议”。`RESEARCH_AUTO_PUBLISH` 默认为 `true`；如需恢复人工
审核流程，可将其设置为 `false`。

### 管理员登录

管理后台使用 PostgreSQL 用户和服务端会话登录。密码采用 PBKDF2-SHA256 和独立随机盐
保存，浏览器只接收 `HttpOnly`、`SameSite=Strict` 会话 Cookie，默认有效期 12 小时。

创建管理员或重置现有管理员密码：

```bash
set -a
source .env.aws
set +a
PYTHONPATH=. .venv/bin/python scripts/create_admin_user.py \
  --username admin \
  --display-name "Aperture Administrator" \
  --generate
```

脚本只在终端显示一次随机密码。打开 `http://127.0.0.1:4174` 后使用该账户登录。
连续 5 次密码错误会锁定账户 15 分钟；退出登录会立即删除数据库会话。

`GEO_ALLOW_ADMIN_KEY` 默认为 `false`。仅在自动化测试或受控运维场景需要兼容旧 API Key
时才临时启用，并设置独立的 `GEO_ADMIN_KEY`。HTTPS 部署应同时设置
`GEO_ADMIN_COOKIE_SECURE=true`。

## 主要 API

| Method | Path | 说明 |
| --- | --- | --- |
| `GET` | `/api/v1/categories` | 公开分类 |
| `GET` | `/api/v1/articles` | 公开内容列表 |
| `GET` | `/api/v1/articles/{slug}` | 完整文章 |
| `GET` | `/api/v1/search?q=` | 内容搜索 |
| `GET` | `/agent/v1/articles/{slug}` | Agent 机器表示 |
| `GET` | `/agent/v1/articles/{slug}/paid` | x402 Variant B 机器表示 |
| `POST` | `/api/admin/auth/login` | 管理员登录并创建 Cookie 会话 |
| `GET` | `/api/admin/auth/me` | 获取当前登录用户 |
| `POST` | `/api/admin/auth/logout` | 注销并删除服务端会话 |
| `GET` | `/api/admin/metrics` | GEO 与流量统计 |
| `GET` | `/api/admin/articles` | 内容管理 |
| `GET` | `/api/admin/crawlers` | 爬虫 Agent |
| `POST` | `/api/admin/crawlers/{id}/run` | 运行 Agent |
| `GET` | `/api/admin/research` | 深度研究输出列表 |
| `GET` | `/api/admin/research/{id}` | 研究过程、正文与来源详情 |

## 生产接入

[`agent_runtime/ARCHITECTURE.md`](./agent_runtime/ARCHITECTURE.md) 描述 AgentCore Runtime、
Code Interpreter、Browser Tool、Bedrock、AgentCore Payments 与数据链路。

[`agent_runtime/codex_crawler.ts`](./agent_runtime/codex_crawler.ts) 提供 Codex SDK
站点专用爬虫生成与修复骨架。

当前已真实部署 Aurora、Bedrock、AgentCore Runtime、Browser、Code Interpreter、
EventBridge Scheduler、ECS、S3/CloudFront OAC 和管理后台登录。Variant B 商户收款与
AgentCore Payments 买方链路已完成 Base Sepolia 测试网真实结算。主网资金、退款、
财务对账和监管流程不在 Demo 范围内，启用主网前必须另行审计。
