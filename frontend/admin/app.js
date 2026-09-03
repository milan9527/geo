const API = location.origin;
const state = { view: "dashboard", range: "30d", user: null, metrics: null, articles: [], research: [], categories: [], crawlers: [], jobs: [], events: [], settings: {} };
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const fmt = (value) => new Intl.NumberFormat("zh-CN").format(value || 0);
const money = (value) => `$${Number(value || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 6 })}`;
const relativeTime = (value) => {
  const minutes = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 60000));
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  if (minutes < 1440) return `${Math.floor(minutes / 60)} 小时前`;
  return `${Math.floor(minutes / 1440)} 天前`;
};

async function api(path, options = {}) {
  const response = await fetch(`${API}${path}`, {
    ...options,
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(payload.error || `API ${response.status}`);
    error.status = response.status;
    error.payload = payload;
    if (response.status === 401 && !path.startsWith("/api/admin/auth/")) {
      showLogin("登录会话已过期，请重新登录。");
    }
    throw error;
  }
  return payload;
}

function userInitials(user) {
  const source = user.displayName || user.username || "AD";
  return source.replace(/\s+/g, "").slice(0, 2).toUpperCase();
}

function showLogin(message = "") {
  state.user = null;
  $("#adminShell").hidden = true;
  $("#loginScreen").hidden = false;
  $("#loginError").textContent = message;
  const password = $('#loginForm [name="password"]');
  if (password) password.value = "";
  setTimeout(() => $('#loginForm [name="username"]')?.focus(), 30);
}

function showAdmin(user) {
  state.user = user;
  $("#loginScreen").hidden = true;
  $("#adminShell").hidden = false;
  $("#adminInitials").textContent = userInitials(user);
  $("#adminDisplayName").textContent = user.displayName;
  $("#adminRole").textContent = user.role;
}

async function login(event) {
  event.preventDefault();
  const button = $("#loginButton");
  const form = new FormData(event.currentTarget);
  $("#loginError").textContent = "";
  button.disabled = true;
  button.textContent = "正在验证…";
  try {
    const result = await api("/api/admin/auth/login", {
      method: "POST",
      body: JSON.stringify({
        username: form.get("username"),
        password: form.get("password"),
      }),
    });
    showAdmin(result.user);
    await loadAll();
    renderDashboard();
  } catch (error) {
    showLogin(error.message || "登录失败，请重试。");
  } finally {
    button.disabled = false;
    button.innerHTML = '登录控制台 <svg><use href="#i-arrow"></use></svg>';
  }
}

async function logout() {
  try {
    await api("/api/admin/auth/logout", { method: "POST", body: "{}" });
  } finally {
    showLogin("已安全退出管理控制台。");
  }
}

function showToast(title, copy = "") {
  $("#toastTitle").textContent = title;
  $("#toastCopy").textContent = copy;
  $("#toast").classList.add("show");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => $("#toast").classList.remove("show"), 2800);
}

function statusLabel(status) {
  return { published: "已发布", review: "待审核", draft: "草稿", running: "运行中", paused: "已暂停", idle: "空闲", error: "异常", completed: "已完成", queued: "排队中", failed: "失败" }[status] || status;
}

function metricCard(icon, color, label, value, growth, note) {
  return `
    <article class="metric-card">
      <div class="metric-card-header"><span class="metric-icon ${color}"><svg><use href="#${icon}"></use></svg></span><span>${label}</span></div>
      <strong>${value}</strong>
      <div class="metric-footer"><b>${growth >= 0 ? "↑" : "↓"} ${Math.abs(growth)}%</b><span>${note}</span></div>
      <div class="metric-art"><i></i><i></i><i></i><i></i><i></i><i></i></div>
    </article>
  `;
}

function chartPath(values, width = 720, height = 200) {
  const max = Math.max(...values, 1) * 1.12;
  const step = width / Math.max(1, values.length - 1);
  return values.map((value, index) => `${index ? "L" : "M"}${(index * step).toFixed(1)} ${(height - value / max * 172).toFixed(1)}`).join(" ");
}

function renderChart(daily) {
  const agent = daily.map((row) => row.agent_views);
  const human = daily.map((row) => row.human_views);
  const agentPath = chartPath(agent);
  const humanPath = chartPath(human);
  const labels = daily.length <= 7 ? daily : daily.filter((_, index) => index % Math.ceil(daily.length / 6) === 0);
  return `
    <div class="traffic-chart">
      <svg viewBox="0 0 720 215" preserveAspectRatio="none" aria-label="流量趋势图">
        <defs>
          <linearGradient id="agentFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#18aa96" stop-opacity=".28"/><stop offset="1" stop-color="#18aa96" stop-opacity="0"/></linearGradient>
          <linearGradient id="humanFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#7469e9" stop-opacity=".16"/><stop offset="1" stop-color="#7469e9" stop-opacity="0"/></linearGradient>
        </defs>
        <g class="chart-grid"><line x1="0" y1="20" x2="720" y2="20"/><line x1="0" y1="65" x2="720" y2="65"/><line x1="0" y1="110" x2="720" y2="110"/><line x1="0" y1="155" x2="720" y2="155"/><line x1="0" y1="200" x2="720" y2="200"/></g>
        <path class="human-area" d="${humanPath} L720 200 L0 200 Z"></path>
        <path class="agent-area" d="${agentPath} L720 200 L0 200 Z"></path>
        <path class="human-line" d="${humanPath}"></path>
        <path class="agent-line" d="${agentPath}"></path>
      </svg>
      <div class="chart-labels">${labels.map((row) => `<span>${row.day.slice(5)}</span>`).join("")}</div>
    </div>
  `;
}

function renderDashboard() {
  const data = state.metrics;
  const summary = data.summary;
  const ab = data.abTest;
  const total = summary.humanViews + summary.agentViews;
  $("#pageTitle").textContent = "GEO 运营总览";
  $("#adminApp").innerHTML = `
    <section class="dashboard-header">
      <p>监测内容可见度、Agent 访问和机器流量变现表现。</p>
      <div class="range-tabs">${["7d","30d","90d"].map((range) => `<button class="${state.range === range ? "active" : ""}" data-range="${range}">${range.toUpperCase()}</button>`).join("")}</div>
    </section>
    <section class="metric-grid">
      ${metricCard("i-users", "teal", "Agent 独立访问", fmt(summary.agentViews), data.growth.agent, `占总流量 ${summary.agentShare}%`)}
      ${metricCard("i-citation", "purple", "AI 内容引用", fmt(summary.citations), data.growth.citations, `引用率 ${summary.citationRate}%`)}
      ${metricCard("i-trend", "blue", "人类访问", fmt(summary.humanViews), data.growth.human, "含 AI 引荐访问")}
      ${metricCard("i-wallet", "amber", "x402 收入", money(summary.revenue), data.growth.revenue, `${fmt(summary.payments)} 笔支付`)}
    </section>
    <section class="panel ab-panel">
      <div class="panel-header"><div><p>GEO + X402 EXPERIMENT</p><h2>Agent 内容 A/B 实测</h2></div><span>${data.range} 天真实事件</span></div>
      <div class="ab-grid">
        <div><span>A · 开放机器页</span><strong>${fmt(ab.variantAViews)}</strong><small>无需支付的 Agent 请求</small></div>
        <div><span>B · x402 付费页</span><strong>${fmt(ab.variantBViews)}</strong><small>${fmt(ab.challenges)} 次支付挑战</small></div>
        <div><span>支付转化</span><strong>${ab.conversionRate}%</strong><small>${fmt(ab.payments)} 笔链上结算</small></div>
        <div><span>机器流量收入</span><strong>${money(ab.revenue)}</strong><small>由 PAYMENT-RESPONSE 结算事件确认</small></div>
      </div>
    </section>
    <section class="dashboard-grid">
      <article class="panel">
        <div class="panel-header"><div><p>TRAFFIC INTELLIGENCE</p><h2>人类与 Agent 流量趋势</h2></div><span>总访问 ${fmt(total)}</span></div>
        <div class="chart-summary">
          <div><span>所选周期</span><strong>${fmt(total)}</strong></div>
          <div class="chart-legend"><i></i>Agent 流量 <b>${fmt(summary.agentViews)}</b></div>
          <div class="chart-legend human"><i></i>人类访问 <b>${fmt(summary.humanViews)}</b></div>
        </div>
        ${renderChart(data.daily)}
      </article>
      <article class="panel">
        <div class="panel-header"><div><p>AGENT ATTRIBUTION</p><h2>Agent 来源分布</h2></div><span>${summary.agentShare}% share</span></div>
        <div class="sources-list">${data.agentSources.length ? data.agentSources.map((source, index) => `
          <div class="source-row"><div class="source-name"><span class="source-avatar ${["","a","p","g","o"][index]}">${source.name[0]}</span>${source.name}</div><div class="source-bar"><span style="width:${source.value}%"></span></div><b>${source.value}%</b></div>
        `).join("") : '<div class="empty-state">所选周期暂无 Agent 访问。</div>'}</div>
      </article>
    </section>
    <section class="secondary-grid">
      <article class="panel">
        <div class="panel-header"><div><p>CONTENT HEALTH</p><h2>内容与爬虫运行状况</h2></div><span>${data.crawlers.running}/${data.crawlers.total} agents</span></div>
        <div class="content-health">
          <div class="health-ring"><strong>${Math.round(summary.citationRate * 2.1)}</strong><span>GEO HEALTH</span></div>
          <div class="health-stats">
            <div class="health-row"><span>已发布内容</span><strong>${data.content.published || 0}</strong></div>
            <div class="health-row"><span>待审核 / 草稿</span><strong>${(data.content.review || 0) + (data.content.draft || 0)}</strong></div>
            <div class="health-row"><span>运行中 Agent</span><strong>${data.crawlers.running}</strong></div>
            <div class="health-row"><span>今日抓取文档</span><strong>${fmt(data.crawlers.pagesToday)}</strong></div>
          </div>
        </div>
      </article>
      <article class="panel">
        <div class="panel-header"><div><p>LIVE ACTIVITY</p><h2>最新访问事件</h2></div><span>实时</span></div>
        <div class="event-list">${state.events.slice(0,5).map((event) => `
          <div class="event-row"><span class="event-icon"><svg><use href="#${event.visitor_type === "agent" ? "i-agent" : "i-users"}"></use></svg></span><div><strong>${event.agent_name || (event.visitor_type === "agent" ? "未知 Agent" : "人类访客")} · ${event.event_type}</strong><span>${event.article_title || "站点页面"}</span></div><small>${relativeTime(event.occurred_at)}</small></div>
        `).join("")}</div>
      </article>
    </section>
  `;
}

function renderContent() {
  $("#pageTitle").textContent = "内容管理";
  $("#adminApp").innerHTML = `
    <section class="view-page">
      <div class="view-header"><div><h2>专业内容库</h2><p>管理发布状态、GEO 权威度、访问模式与 Agent 定价。</p></div><div class="view-actions"><button class="ghost-button" id="exportContent">导出内容</button><button class="primary-button" id="createContent">创建研究</button></div></div>
      <div class="table-panel">
        <div class="table-toolbar"><input id="contentSearch" placeholder="搜索标题或作者…"/><select id="statusFilter"><option value="">全部状态</option><option value="published">已发布</option><option value="review">待审核</option><option value="draft">草稿</option></select></div>
        <table class="data-table"><thead><tr><th>内容</th><th>分类</th><th>状态</th><th>GEO 权威度</th><th>AI 引用</th><th>Agent 访问</th><th>更新时间</th><th>操作</th></tr></thead><tbody id="contentRows"></tbody></table>
      </div>
    </section>
  `;
  renderContentRows();
  $("#contentSearch").addEventListener("input", renderContentRows);
  $("#statusFilter").addEventListener("change", renderContentRows);
}

function renderContentRows() {
  const term = ($("#contentSearch")?.value || "").toLowerCase();
  const status = $("#statusFilter")?.value || "";
  const rows = state.articles.filter((item) => (!term || `${item.title}${item.author}`.toLowerCase().includes(term)) && (!status || item.status === status));
  $("#contentRows").innerHTML = rows.map((item) => `
    <tr>
      <td><div class="content-title"><strong>${item.title}</strong><span>${item.author} · /${item.slug}</span></div></td>
      <td>${item.category_name}</td><td><span class="status-pill ${item.status}">${statusLabel(item.status)}</span></td>
      <td><b>${item.authority_score}</b> / 100</td><td>${fmt(item.citation_count)}</td>
      <td><span class="access-pill">${item.access_model === "open" ? "开放" : `x402 · $${item.agent_price}`}</span></td>
      <td>${relativeTime(item.updated_at)}</td>
      <td><div class="action-group"><button class="table-action" data-toggle-publish="${item.id}" data-status="${item.status}" title="${item.status === "published" ? "转为审核" : "发布"}"><svg><use href="#${item.status === "published" ? "i-pause" : "i-play"}"></use></svg></button></div></td>
    </tr>
  `).join("");
}

function renderCrawlers() {
  $("#pageTitle").textContent = "爬虫 Agent 管理";
  $("#adminApp").innerHTML = `
    <section class="view-page">
      <div class="view-header"><div><h2>Agent 爬虫矩阵</h2><p>管理 Code Interpreter、Browser Tool 与证据核验 Agent。</p></div><div class="view-actions"><button class="primary-button" data-run-all><svg><use href="#i-play"></use></svg>全部运行</button></div></div>
      <div class="crawler-card-grid">${state.crawlers.map((crawler) => `
        <article class="crawler-card">
          <div class="crawler-card-top"><span class="crawler-logo">${crawler.kind.split(" ").map((v) => v[0]).join("").slice(0,2)}</span><div><strong>${crawler.name}</strong><small>${crawler.kind} · ${crawler.schedule}${crawler.eventbridge ? ` · EventBridge ${crawler.eventbridge.state}` : ""}</small></div><span class="status-pill ${crawler.status}">${statusLabel(crawler.status)}</span></div>
          <div class="crawler-tags">${crawler.industries.map((tag) => `<span>${tag}</span>`).join("")}</div>
          <div class="crawler-metrics"><div><strong>${fmt(crawler.pages_today)}</strong><span>今日文档</span></div><div><strong>${crawler.success_rate}%</strong><span>成功率</span></div><div><strong>$${crawler.cost_per_doc}</strong><span>单文档成本</span></div></div>
          <div class="crawler-card-actions"><button class="ghost-button" data-run-crawler="${crawler.id}" data-paid="${crawler.slug === "commerce-feed-miner" ? "true" : "false"}"><svg><use href="#i-play"></use></svg>${crawler.slug === "commerce-feed-miner" ? "运行并测试 x402" : "立即运行"}</button><button class="ghost-button" data-toggle-crawler="${crawler.id}" data-status="${crawler.status}"><svg><use href="#${crawler.status === "paused" ? "i-play" : "i-pause"}"></use></svg>${crawler.status === "paused" ? "恢复" : "暂停"}</button></div>
        </article>
      `).join("")}</div>
    </section>
  `;
}

function renderJobs() {
  $("#pageTitle").textContent = "爬虫任务记录";
  $("#adminApp").innerHTML = `
    <section class="view-page">
      <div class="view-header"><div><h2>任务执行历史</h2><p>查看每个 Agent 的调度、吞吐与运行结果。</p></div><div class="view-actions"><button class="ghost-button" id="refreshJobs"><svg><use href="#i-refresh"></use></svg>刷新</button></div></div>
      <article class="panel"><div class="panel-header"><div><p>AURORA JOB QUEUE</p><h2>最近 20 次任务</h2></div></div>
        <div class="job-timeline">${state.jobs.map((job) => `<div class="job-row"><i class="job-dot"></i><div><strong>${job.agent_name}</strong><span>${job.agent_kind}${job.toolTrace?.sessionId ? ` · ${job.toolTrace.provider} · ${job.toolTrace.sessionId}` : ""}</span></div><span>${job.message}</span><b class="status-pill ${job.status}">${statusLabel(job.status)}</b><small>${relativeTime(job.started_at)}</small></div>`).join("")}</div>
      </article>
    </section>
  `;
}

function renderResearch() {
  $("#pageTitle").textContent = "深度研究输出";
  $("#adminApp").innerHTML = `
    <section class="view-page">
      <div class="view-header"><div><h2>证据驱动研究稿</h2><p>查看 Agent 抓取的原始来源、分析过程、专业观点、结论与发布状态。</p></div><div class="view-actions"><button class="ghost-button" id="refreshResearch"><svg><use href="#i-refresh"></use></svg>刷新</button></div></div>
      <div class="research-output-list">
        ${state.research.length ? state.research.map((run) => `
          <article class="research-output-card">
            <div class="research-output-header">
              <div><p>${run.agent_name} · ${run.agent_kind}</p><h2>${run.article_title || run.topic}</h2><span>${run.category_slug} · ${run.evidence_count} 条证据 · ${relativeTime(run.started_at)}</span></div>
              <span class="status-pill ${run.status === "completed" ? "running" : run.status}">${run.status === "completed" ? "已生成" : run.status === "skipped" ? "证据未变化" : run.status}</span>
            </div>
            <p class="research-summary">${run.summary || run.error_message || "研究任务正在执行。"}</p>
            ${run.toolTrace?.provider ? `<div class="research-process"><h3>真实工具执行</h3><div><b>${run.toolTrace.provider}</b><span>Session ${run.toolTrace.sessionId || "n/a"}</span><p>${run.toolTrace.documents || 0} 条文档${run.toolTrace.codexThreadId ? ` · Codex Thread ${run.toolTrace.codexThreadId}` : ""}${run.toolTrace.webBotAuth ? " · Web Bot Auth" : ""}</p></div></div>` : ""}
            ${run.verification?.status ? `<div class="research-process"><h3>证据审计</h3><div><b>${run.verification.status === "verified" ? "已通过" : "需要人工复核"} · ${run.verification.score || 0}</b><span>${run.verification.notes || ""}</span><p>${(run.verification.unsupportedClaims || []).join("；") || "未发现无证据支持的关键表述"}</p></div></div>` : ""}
            ${run.analysisProcess?.length ? `<div class="research-process"><h3>分析过程</h3>${run.analysisProcess.map((step, index) => `<div><b>0${index + 1} ${step.step}</b><span>${step.method}</span><p>${step.result}</p><small>${step.evidence}</small></div>`).join("")}</div>` : ""}
            ${run.sections?.length ? `<div class="research-sections"><h3>观点与结论</h3>${run.sections.map((section) => `<div><b>${section.heading}</b>${(section.paragraphs || []).slice(0, 2).map((text) => `<p>${text}</p>`).join("")}${(section.bullets || []).length ? `<ul>${section.bullets.map((item) => `<li>${item}</li>`).join("")}</ul>` : ""}</div>`).join("")}</div>` : ""}
            <div class="research-evidence"><h3>数据与来源</h3>${run.evidence.map((source, index) => `<a href="${source.url}" target="_blank" rel="noreferrer"><span>[S${index + 1}] ${source.publisher} · ${source.source_type}</span><b>${source.title}</b><small>${source.published_at}</small><p>${source.content_excerpt.slice(0, 260)}</p></a>`).join("")}</div>
            ${run.output_article_id ? `<div class="research-output-footer"><span>文章 #${run.output_article_id} · ${run.article_status === "review" ? "待编辑审核" : statusLabel(run.article_status)}</span><button class="ghost-button" data-open-content>前往内容管理</button></div>` : ""}
          </article>
        `).join("") : '<div class="empty-state">尚无深度研究输出。运行一个爬虫 Agent 后，证据和分析会显示在这里。</div>'}
      </div>
    </section>
  `;
}

function renderSettings() {
  const setting = (key, fallback = false) => state.settings[key]?.value ?? fallback;
  $("#pageTitle").textContent = "数据与设置";
  $("#adminApp").innerHTML = `
    <section class="view-page">
      <div class="view-header"><div><h2>平台配置</h2><p>管理数据采集、Agent 识别与机器访问策略。</p></div></div>
      <div class="settings-grid">
        <article class="setting-card"><h2>GEO 与访问识别</h2>
          <div class="setting-row"><div><strong>Agent User-Agent 识别</strong><span>记录常见 AI 爬虫与研究 Agent 来源</span></div><button class="toggle ${setting("agent_user_agent_detection", true) ? "on" : ""}" data-setting="agent_user_agent_detection"></button></div>
          <div class="setting-row"><div><strong>机器可读内容端点</strong><span>输出声明、来源与许可信息</span></div><button class="toggle ${setting("machine_content_endpoint", true) ? "on" : ""}" data-setting="machine_content_endpoint"></button></div>
          <div class="setting-row"><div><strong>自动 JSON-LD</strong><span>为已发布内容生成结构化元数据</span></div><button class="toggle ${setting("automatic_json_ld", true) ? "on" : ""}" data-setting="automatic_json_ld"></button></div>
        </article>
        <article class="setting-card"><h2>支付与授权</h2>
          <div class="setting-row"><div><strong>x402 Agent 支付</strong><span>高价值内容支持机器按次购买</span></div><button class="toggle ${setting("x402_payments", true) ? "on" : ""}" data-setting="x402_payments"></button></div>
          <div class="setting-row"><div><strong>Stripe · Privy 钱包</strong><span>需要提供外部服务凭据后启用</span></div><span class="status-pill paused">未配置</span></div>
          <div class="setting-row"><div><strong>支付失败告警</strong><span>结算成功率低于 95% 时触发</span></div><button class="toggle ${setting("payment_failure_alerts", true) ? "on" : ""}" data-setting="payment_failure_alerts"></button></div>
        </article>
        <article class="setting-card"><h2>数据库</h2>
          <div class="setting-row"><div><strong>Aurora PostgreSQL 17.7</strong><span>Serverless v2 · Data API</span></div><span class="status-pill running">已连接</span></div>
          <div class="setting-row"><div><strong>分析数据保留</strong><span>当前统计保留周期</span></div><b>${setting("analytics_retention_days", 120)} 天</b></div>
        </article>
        <article class="setting-card"><h2>推理与运行时</h2>
          <div class="setting-row"><div><strong>分析模型</strong><span>Amazon Bedrock 应用推理配置</span></div><b>GPT-5.6 Sol</b></div>
          <div class="setting-row"><div><strong>AgentCore Runtime</strong><span>Browser + Code Interpreter</span></div><span class="status-pill running">READY</span></div>
          <div class="setting-row"><div><strong>EventBridge Scheduler</strong><span>6 条计划 · Lambda 桥接 · SQS DLQ</span></div><span class="status-pill running">ACTIVE</span></div>
        </article>
      </div>
    </section>
  `;
}

async function switchView(view) {
  state.view = view;
  $$(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.view === view));
  if (view === "dashboard") renderDashboard();
  if (view === "content") renderContent();
  if (view === "research") renderResearch();
  if (view === "crawlers") renderCrawlers();
  if (view === "jobs") renderJobs();
  if (view === "settings") renderSettings();
}

async function loadAll() {
  [state.metrics, state.articles, state.research, state.categories, state.crawlers, state.jobs, state.events, state.settings] = await Promise.all([
    api(`/api/admin/metrics?range=${state.range}`),
    api("/api/admin/articles"),
    api("/api/admin/research"),
    api("/api/v1/categories"),
    api("/api/admin/crawlers"),
    api("/api/admin/jobs"),
    api("/api/admin/events"),
    api("/api/admin/settings"),
  ]);
  $("#contentCount").textContent = state.articles.length;
  $("#researchCount").textContent = state.research.filter((item) => item.status === "completed").length;
}

async function refresh() {
  const button = $("#refreshButton");
  button.classList.add("spinning");
  try {
    await loadAll();
    switchView(state.view);
    showToast("数据已刷新", "统计、内容和 Agent 状态已同步");
  } finally {
    button.classList.remove("spinning");
  }
}

async function runAll() {
  const result = await api("/api/admin/crawlers/run-all", { method: "POST", body: "{}" });
  showToast("批量任务已提交", `${result.jobs.length} 个 Agent 已进入运行队列`);
  state.crawlers = await api("/api/admin/crawlers");
  state.jobs = await api("/api/admin/jobs");
  if (state.view === "crawlers") renderCrawlers();
  if (state.view === "jobs") renderJobs();
}

function bindEvents() {
  $$(".nav-item").forEach((button) => button.addEventListener("click", () => switchView(button.dataset.view)));
  $("#refreshButton").addEventListener("click", refresh);
  $("#runAllButton").addEventListener("click", runAll);
  $("#logoutButton").addEventListener("click", logout);
  document.addEventListener("click", async (event) => {
    const range = event.target.closest("[data-range]");
    if (range) {
      state.range = range.dataset.range;
      state.metrics = await api(`/api/admin/metrics?range=${state.range}`);
      renderDashboard();
    }
    const publish = event.target.closest("[data-toggle-publish]");
    if (publish) {
      const status = publish.dataset.status === "published" ? "review" : "published";
      await api(`/api/admin/articles/${publish.dataset.togglePublish}`, { method: "PATCH", body: JSON.stringify({ status }) });
      state.articles = await api("/api/admin/articles");
      renderContent();
      showToast(status === "published" ? "内容已发布" : "内容已转入审核");
    }
    const run = event.target.closest("[data-run-crawler]");
    if (run) {
      const result = await api(`/api/admin/crawlers/${run.dataset.runCrawler}/run`, { method: "POST", body: JSON.stringify({ allowPayment: run.dataset.paid === "true" }) });
      showToast("任务已提交", result.agent);
      state.crawlers = await api("/api/admin/crawlers");
      state.jobs = await api("/api/admin/jobs");
      renderCrawlers();
    }
    const toggle = event.target.closest("[data-toggle-crawler]");
    if (toggle) {
      const status = toggle.dataset.status === "paused" ? "running" : "paused";
      await api(`/api/admin/crawlers/${toggle.dataset.toggleCrawler}`, { method: "PATCH", body: JSON.stringify({ status }) });
      state.crawlers = await api("/api/admin/crawlers");
      renderCrawlers();
      showToast(status === "paused" ? "Agent 已暂停" : "Agent 已恢复");
    }
    if (event.target.closest("[data-run-all]")) runAll();
    const settingToggle = event.target.closest("[data-setting]");
    if (settingToggle) {
      const key = settingToggle.dataset.setting;
      const value = !settingToggle.classList.contains("on");
      await api(`/api/admin/settings/${key}`, { method: "PATCH", body: JSON.stringify({ value }) });
      state.settings[key].value = value;
      renderSettings();
      showToast("设置已保存", "配置已持久化到 PostgreSQL");
    }
    if (event.target.closest("#createContent")) openArticleModal();
    if (event.target.closest("#exportContent")) exportContent();
    if (event.target.closest("#refreshJobs")) {
      state.jobs = await api("/api/admin/jobs");
      renderJobs();
      showToast("任务记录已刷新");
    }
    if (event.target.closest("#refreshResearch")) {
      state.research = await api("/api/admin/research");
      renderResearch();
      showToast("研究输出已刷新");
    }
    if (event.target.closest("[data-open-content]")) switchView("content");
  });
  $("#closeArticleModal").addEventListener("click", closeArticleModal);
  $("#cancelArticleModal").addEventListener("click", closeArticleModal);
  $("#articleModal").addEventListener("click", (event) => {
    if (event.target === $("#articleModal")) closeArticleModal();
  });
  $("#createArticleForm").addEventListener("submit", createArticle);
}

function openArticleModal() {
  $("#articleCategory").innerHTML = state.categories.map((category) => `<option value="${category.slug}">${category.name}</option>`).join("");
  $("#articleModal").classList.add("open");
  $("#articleModal").setAttribute("aria-hidden", "false");
  setTimeout(() => $('#createArticleForm [name="title"]').focus(), 50);
}

function closeArticleModal() {
  $("#articleModal").classList.remove("open");
  $("#articleModal").setAttribute("aria-hidden", "true");
}

async function createArticle(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const payload = Object.fromEntries(form.entries());
  const result = await api("/api/admin/articles", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  event.currentTarget.reset();
  closeArticleModal();
  state.articles = await api("/api/admin/articles");
  $("#contentCount").textContent = state.articles.length;
  renderContent();
  showToast("研究内容已创建", `${result.slug} · ${statusLabel(result.status)}`);
}

function exportContent() {
  const payload = JSON.stringify(
    {
      exportedAt: new Date().toISOString(),
      source: "Aperture Control",
      articles: state.articles,
    },
    null,
    2,
  );
  const blob = new Blob([payload], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `aperture-content-${new Date().toISOString().slice(0, 10)}.json`;
  link.click();
  URL.revokeObjectURL(url);
  showToast("内容已导出", `${state.articles.length} 篇内容元数据`);
}

async function init() {
  $("#loginForm").addEventListener("submit", login);
  bindEvents();
  try {
    const session = await api("/api/admin/auth/me");
    showAdmin(session.user);
    await loadAll();
    renderDashboard();
  } catch (error) {
    if (error.status === 401) {
      showLogin();
      return;
    }
    showLogin("无法连接管理 API，请确认后端服务运行正常。");
  }
}

document.addEventListener("DOMContentLoaded", init);
