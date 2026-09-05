const API = location.origin;
const todayIso = () => new Date().toISOString().slice(0, 10);
const daysAgoIso = (days) => new Date(Date.now() - days * 86400000).toISOString().slice(0, 10);
const state = {
  view: "dashboard",
  range: "30d",
  customStart: daysAgoIso(29),
  customEnd: todayIso(),
  selectedArticles: new Set(),
  user: null,
  metrics: null,
  articles: [],
  research: [],
  categories: [],
  crawlers: [],
  dataSources: [],
  sourceTestBatch: {
    running: false,
    total: 0,
    completed: 0,
    success: 0,
    failed: 0,
  },
  sourceQuery: "",
  sourceCategory: "all",
  sourceStatus: "all",
  jobs: [],
  events: [],
  settings: {},
};
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const fmt = (value) => new Intl.NumberFormat("zh-CN").format(value || 0);
const money = (value) => `$${Number(value || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 6 })}`;
const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (character) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
}[character]));
const safeExternalUrl = (value) => {
  try {
    const url = new URL(String(value ?? ""));
    return ["http:", "https:"].includes(url.protocol) ? url.href : "";
  } catch {
    return "";
  }
};
const metricsPath = () => state.range === "custom"
  ? `/api/admin/metrics?start=${encodeURIComponent(state.customStart)}&end=${encodeURIComponent(state.customEnd)}`
  : `/api/admin/metrics?range=${state.range}`;
const relativeTime = (value) => {
  const minutes = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 60000));
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  if (minutes < 1440) return `${Math.floor(minutes / 60)} 小时前`;
  return `${Math.floor(minutes / 1440)} 天前`;
};
const compactIdentifier = (value, head = 8, tail = 6) => {
  const text = String(value || "");
  return text.length > head + tail + 3
    ? `${text.slice(0, head)}…${text.slice(-tail)}`
    : text || "—";
};
const x402EventLabels = {
  x402_challenge: "402 Challenge",
  x402_payment: "链上结算成功",
  x402_verification_failed: "支付凭证验证失败",
  x402_settlement_failed: "链上结算失败",
  x402_service_error: "支付服务错误",
};

function renderX402Event(event) {
  const transactionUrl = event.transactionHash
    ? safeExternalUrl(`https://sepolia.basescan.org/tx/${event.transactionHash}`)
    : "";
  const detail = event.error
    || (event.transactionHash ? compactIdentifier(event.transactionHash) : compactIdentifier(event.requestId));
  return `
    <div class="x402-event ${escapeHtml(event.status)}">
      <span class="x402-event-state">${escapeHtml(x402EventLabels[event.type] || event.type)}</span>
      <div>
        <strong>${escapeHtml(event.articleTitle || event.articleSlug || "付费机器内容")}</strong>
        <small>${escapeHtml(event.agentName)} · ${relativeTime(event.occurredAt)}</small>
      </div>
      <div class="x402-event-payment">
        <b>${event.type === "x402_payment" ? money(event.amountUsd) : "—"}</b>
        <small>${event.internal ? "内部 Agent" : event.payer ? "外部付款人" : "未提供付款"}</small>
      </div>
      ${transactionUrl
        ? `<a href="${escapeHtml(transactionUrl)}" target="_blank" rel="noreferrer">${escapeHtml(detail)}</a>`
        : `<span class="x402-event-detail">${escapeHtml(detail)}</span>`}
    </div>
  `;
}

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

const crawlerScheduleOptions = [
  ["*/10 * * * *", "每 10 分钟"],
  ["*/15 * * * *", "每 15 分钟"],
  ["*/20 * * * *", "每 20 分钟"],
  ["*/30 * * * *", "每 30 分钟"],
  ["0 * * * *", "每小时"],
  ["0 */2 * * *", "每 2 小时"],
  ["0 */3 * * *", "每 3 小时"],
  ["0 */6 * * *", "每 6 小时"],
  ["0 */12 * * *", "每 12 小时"],
];

function eventbridgeStateLabel(state) {
  return { ENABLED: "已启用", DISABLED: "已禁用", MISSING: "计划缺失", UNKNOWN: "状态未知" }[state] || state;
}

const sourceMethodLabels = {
  feed: "RSS / Atom",
  web: "普通网页",
  browser: "Browser 渲染",
  api: "API",
  timeseries: "时间序列",
  x402: "x402 付费",
};

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
      <div class="range-controls">
        <div class="range-tabs">${["7d","30d","90d"].map((range) => `<button class="${state.range === range ? "active" : ""}" data-range="${range}">${range.toUpperCase()}</button>`).join("")}</div>
        <div class="custom-range ${state.range === "custom" ? "active" : ""}">
          <input type="date" id="rangeStart" value="${state.customStart}" max="${todayIso()}" aria-label="统计开始日期" />
          <span>至</span>
          <input type="date" id="rangeEnd" value="${state.customEnd}" max="${todayIso()}" aria-label="统计结束日期" />
          <button data-apply-custom-range>应用</button>
        </div>
      </div>
    </section>
    <section class="metric-grid">
      ${metricCard("i-users", "teal", "Agent 独立访问", fmt(summary.agentViews), data.growth.agent, `占总流量 ${summary.agentShare}%`)}
      ${metricCard("i-citation", "purple", "AI 内容引用", fmt(summary.citations), data.growth.citations, `引用率 ${summary.citationRate}%`)}
      ${metricCard("i-trend", "blue", "人类独立访问", fmt(summary.humanViews), data.growth.human, `${fmt(summary.humanRequests || 0)} 次页面请求`)}
      ${metricCard("i-wallet", "amber", "x402 测试网结算", money(summary.revenue), data.growth.revenue, `${fmt(ab.internalPayments)} 笔内部 · ${fmt(ab.externalPayments)} 笔外部`)}
    </section>
    <section class="panel ab-panel">
      <div class="panel-header"><div><p>GEO + X402 EXPERIMENT</p><h2>Agent 内容 A/B 实测</h2></div><span>${data.startDate} 至 ${data.endDate}</span></div>
      <div class="ab-grid">
        <div><span>A · 开放机器页</span><strong>${fmt(ab.variantAViews)}</strong><small>无需支付的 Agent 请求</small></div>
        <div><span>B · x402 付费页</span><strong>${fmt(ab.variantBViews)}</strong><small>${fmt(ab.challenges)} 次支付挑战</small></div>
        <div><span>支付尝试成功率</span><strong>${ab.paymentSuccessRate}%</strong><small>${fmt(ab.payments)} / ${fmt(ab.paymentAttempts)} 次付款尝试</small></div>
        <div><span>测试网结算总额</span><strong>${money(ab.revenue)}</strong><small>外部收入 ${money(ab.externalRevenue)}</small></div>
      </div>
      <div class="x402-detail-grid">
        <div><span>未跟进 Challenge</span><strong>${fmt(ab.unpaidChallengesEstimate)}</strong><small>challenge − 成功结算，近似值</small></div>
        <div><span>凭证验证失败</span><strong>${fmt(ab.verificationFailures)}</strong><small>已提交支付头但验证未通过</small></div>
        <div><span>链上结算失败</span><strong>${fmt(ab.settlementFailures)}</strong><small>凭证有效但结算失败</small></div>
        <div><span>支付服务错误</span><strong>${fmt(ab.serviceErrors)}</strong><small>商户配置或 facilitator 异常</small></div>
        <div><span>内部 Agent 结算</span><strong>${fmt(ab.internalPayments)}</strong><small>${money(ab.internalRevenue)} · 测试流量</small></div>
        <div><span>外部付款结算</span><strong>${fmt(ab.externalPayments)}</strong><small>${money(ab.externalRevenue)} · 真实变现</small></div>
        <div><span>独立付款地址</span><strong>${fmt(ab.uniquePayers)}</strong><small>${fmt(ab.confirmedTransactions)} 个交易哈希</small></div>
        <div><span>Challenge 转结算</span><strong>${ab.conversionRate}%</strong><small>仅作漏斗观察，不等于付款意图</small></div>
      </div>
      <div class="x402-events">
        <div class="x402-events-header"><strong>最近 x402 事件</strong><span>Base Sepolia · 自动支付保持启用</span></div>
        ${(ab.recentEvents || []).length
          ? ab.recentEvents.map(renderX402Event).join("")
          : '<div class="empty-state">所选周期暂无 x402 事件。</div>'}
      </div>
    </section>
    <section class="dashboard-grid">
      <article class="panel">
        <div class="panel-header"><div><p>TRAFFIC INTELLIGENCE</p><h2>人类与 Agent 独立访问趋势</h2></div><span>HLL 估算 · 日志延迟数分钟</span></div>
        <div class="chart-summary">
          <div><span>所选周期</span><strong>${fmt(total)}</strong></div>
          <div class="chart-legend"><i></i>Agent 独立访问 <b>${fmt(summary.agentViews)}</b></div>
          <div class="chart-legend human"><i></i>人类独立访问 <b>${fmt(summary.humanViews)}</b></div>
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
        <div class="panel-header"><div><p>BUSINESS ACTIVITY</p><h2>最新业务事件</h2></div><span>实时</span></div>
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
        <div class="table-toolbar">
          <div class="content-filters"><input id="contentSearch" placeholder="搜索标题或作者…"/><select id="statusFilter"><option value="">全部状态</option><option value="published">已发布</option><option value="review">待审核</option><option value="draft">草稿</option></select></div>
          <div class="bulk-actions" id="bulkActions">
            <span>已选择 <b id="selectedCount">0</b> 篇</span>
            <button data-batch-action="publish" disabled>批量发布</button>
            <button data-batch-action="review" disabled>转为审核</button>
            <button class="danger" data-batch-action="delete" disabled>删除</button>
          </div>
        </div>
        <table class="data-table"><thead><tr><th class="select-column"><input type="checkbox" id="selectAllArticles" aria-label="选择当前列表全部内容" /></th><th>内容</th><th>分类</th><th>状态</th><th>GEO 权威度</th><th>AI 引用</th><th>Agent 访问</th><th>更新时间</th><th>操作</th></tr></thead><tbody id="contentRows"></tbody></table>
      </div>
    </section>
  `;
  renderContentRows();
  $("#contentSearch").addEventListener("input", renderContentRows);
  $("#statusFilter").addEventListener("change", renderContentRows);
}

function filteredContentRows() {
  const term = ($("#contentSearch")?.value || "").toLowerCase();
  const status = $("#statusFilter")?.value || "";
  return state.articles.filter((item) => (!term || `${item.title}${item.author}`.toLowerCase().includes(term)) && (!status || item.status === status));
}

function renderContentRows() {
  const rows = filteredContentRows();
  $("#contentRows").innerHTML = rows.map((item) => `
    <tr>
      <td class="select-column"><input type="checkbox" data-select-article="${item.id}" ${state.selectedArticles.has(item.id) ? "checked" : ""} aria-label="选择 ${escapeHtml(item.title)}" /></td>
      <td><button class="content-title content-link" data-open-article="${item.id}"><strong>${escapeHtml(item.title)}</strong><span>${escapeHtml(item.author)} · /${escapeHtml(item.slug)}</span></button></td>
      <td>${escapeHtml(item.category_name)}</td><td><span class="status-pill ${escapeHtml(item.status)}">${escapeHtml(statusLabel(item.status))}</span></td>
      <td><b>${item.authority_score}</b> / 100</td><td>${fmt(item.citation_count)}</td>
      <td><span class="access-pill">${item.access_model === "open" ? "开放" : `x402 · $${item.agent_price}`}</span></td>
      <td>${relativeTime(item.updated_at)}</td>
      <td><div class="action-group"><button class="table-action" data-open-article="${item.id}" title="查看内容"><svg><use href="#i-content"></use></svg></button><button class="table-action" data-toggle-publish="${item.id}" data-status="${item.status}" title="${item.status === "published" ? "转为审核" : "发布"}"><svg><use href="#${item.status === "published" ? "i-pause" : "i-play"}"></use></svg></button></div></td>
    </tr>
  `).join("");
  updateBulkActions();
}

function updateBulkActions() {
  const count = state.selectedArticles.size;
  if ($("#selectedCount")) $("#selectedCount").textContent = count;
  $$("[data-batch-action]").forEach((button) => { button.disabled = count === 0; });
  const visible = filteredContentRows().map((item) => item.id);
  if ($("#selectAllArticles")) {
    $("#selectAllArticles").checked = visible.length > 0 && visible.every((id) => state.selectedArticles.has(id));
    $("#selectAllArticles").indeterminate = visible.some((id) => state.selectedArticles.has(id)) && !$("#selectAllArticles").checked;
  }
}

function renderDetailSection(section) {
  const paragraphs = (Array.isArray(section.paragraphs) ? section.paragraphs : []).map((paragraph) => `<p>${escapeHtml(paragraph)}</p>`).join("");
  const bullets = Array.isArray(section.bullets) && section.bullets.length
    ? `<ul>${section.bullets.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`
    : "";
  const quote = section.quote ? `<blockquote>${escapeHtml(section.quote)}</blockquote>` : "";
  const rows = Array.isArray(section.rows) && section.rows.length
    ? `<div class="detail-table-wrap"><table><thead><tr>${(Array.isArray(section.headers) ? section.headers : []).map((cell) => `<th>${escapeHtml(cell)}</th>`).join("")}</tr></thead><tbody>${section.rows.map((row) => `<tr>${(Array.isArray(row) ? row : []).map((cell) => `<td>${escapeHtml(cell)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`
    : "";
  return `<section><h3>${escapeHtml(section.heading || "分析")}</h3>${paragraphs}${quote}${rows}${bullets}</section>`;
}

function renderDetailSource(source, index) {
  const url = safeExternalUrl(source.url);
  const content = `
    <span>[S${index + 1}] ${escapeHtml(source.publisher)} · ${escapeHtml(source.source_type)}</span>
    <strong>${escapeHtml(source.title)}</strong>
    <small>${escapeHtml(source.published_at)}</small>
  `;
  return url
    ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${content}</a>`
    : `<div class="detail-source-unlinked">${content}</div>`;
}

async function openArticleDetail(articleId) {
  const modal = $("#contentDetailModal");
  const body = $("#contentDetailBody");
  modal.classList.add("open");
  modal.setAttribute("aria-hidden", "false");
  body.innerHTML = '<div class="detail-loading">正在读取完整内容…</div>';
  try {
    const article = await api(`/api/admin/articles/${articleId}`);
    $("#contentDetailEyebrow").textContent = `${article.category_name} · ${statusLabel(article.status)}`;
    $("#contentDetailTitle").textContent = article.title;
    body.innerHTML = `
      <div class="detail-meta">
        <span>${escapeHtml(article.author)} · ${escapeHtml(article.author_role)}</span>
        <span>权威度 ${article.authority_score}/100</span>
        <span>${article.citation_count} 条引用</span>
        <span>${escapeHtml(article.updated_at.slice(0, 10))} 更新</span>
      </div>
      <p class="detail-dek">${escapeHtml(article.dek)}</p>
      <p class="detail-summary">${escapeHtml(article.summary)}</p>
      <div class="detail-sections">${(Array.isArray(article.sections) ? article.sections : []).map(renderDetailSection).join("")}</div>
      <div class="detail-sources">
        <h3>数据与来源</h3>
        ${Array.isArray(article.sources) && article.sources.length
          ? article.sources.map(renderDetailSource).join("")
          : "<p>暂无来源记录。</p>"}
      </div>
    `;
  } catch (error) {
    body.innerHTML = `<div class="empty-state">${escapeHtml(error.message || "内容读取失败")}</div>`;
  }
}

function closeArticleDetail() {
  $("#contentDetailModal").classList.remove("open");
  $("#contentDetailModal").setAttribute("aria-hidden", "true");
}

async function batchArticles(action) {
  const ids = [...state.selectedArticles];
  if (!ids.length) return;
  if (action === "delete" && !window.confirm(`确认永久删除所选 ${ids.length} 篇内容？相关来源会一并删除，研究任务记录将保留。`)) return;
  const result = await api("/api/admin/articles/batch", {
    method: "PATCH",
    body: JSON.stringify({ ids, action, confirm: action === "delete" }),
  });
  state.selectedArticles.clear();
  state.articles = await api("/api/admin/articles");
  $("#contentCount").textContent = state.articles.length;
  renderContent();
  const labels = { publish: "已批量发布", review: "已转为待审核", delete: "已删除" };
  showToast(labels[action], `${result.count} 篇内容已处理`);
}

function renderCrawlers() {
  $("#pageTitle").textContent = "爬虫 Agent 管理";
  $("#adminApp").innerHTML = `
    <section class="view-page">
      <div class="view-header"><div><h2>Agent 爬虫矩阵</h2><p>管理 Code Interpreter、Browser Tool 与证据核验 Agent。</p></div><div class="view-actions"><button class="primary-button" data-run-all><svg><use href="#i-play"></use></svg>全部运行</button></div></div>
      <div class="crawler-card-grid">${state.crawlers.map((crawler) => `
        <article class="crawler-card">
          <div class="crawler-card-top"><span class="crawler-logo">${crawler.kind.split(" ").map((v) => v[0]).join("").slice(0,2)}</span><div><strong>${escapeHtml(crawler.name)}</strong><small>${escapeHtml(crawler.kind)} · ${escapeHtml(crawler.scheduleLabel || crawler.schedule)} · UTC${crawler.eventbridge ? ` · ${eventbridgeStateLabel(crawler.eventbridge.state)}` : ""}</small></div><span class="status-pill ${crawler.status}">${statusLabel(crawler.status)}</span></div>
          <div class="crawler-tags">${crawler.industries.map((tag) => `<span>${tag}</span>`).join("")}</div>
          <div class="crawler-metrics"><div><strong>${fmt(crawler.sourceCount)}</strong><span>启用来源</span></div><div><strong>${fmt(crawler.pages_today)}</strong><span>今日文档</span></div><div><strong>${crawler.success_rate}%</strong><span>成功率</span></div></div>
          <div class="crawler-card-actions"><button class="ghost-button" data-run-crawler="${crawler.id}" data-paid="${crawler.slug === "commerce-feed-miner" ? "true" : "false"}"><svg><use href="#i-play"></use></svg>${crawler.slug === "commerce-feed-miner" ? "运行 x402" : "立即运行"}</button><button class="ghost-button" data-edit-crawler-schedule="${crawler.id}" data-crawler-name="${escapeHtml(crawler.name)}" data-crawler-schedule="${escapeHtml(crawler.schedule)}"><svg><use href="#i-jobs"></use></svg>定期时间</button><button class="ghost-button" data-toggle-crawler="${crawler.id}" data-status="${crawler.status}"><svg><use href="#${crawler.status === "paused" ? "i-play" : "i-pause"}"></use></svg>${crawler.status === "paused" ? "恢复" : "暂停"}</button></div>
        </article>
      `).join("")}</div>
    </section>
  `;
}

function filteredDataSources() {
  const query = state.sourceQuery.trim().toLowerCase();
  return state.dataSources.filter((source) => {
    const matchesQuery = !query || [
      source.publisher,
      source.name,
      source.url,
      source.source_type,
    ].some((value) => String(value || "").toLowerCase().includes(query));
    const matchesCategory = state.sourceCategory === "all" || source.category_slug === state.sourceCategory;
    const matchesStatus = state.sourceStatus === "all" || source.status === state.sourceStatus;
    return matchesQuery && matchesCategory && matchesStatus;
  });
}

function sourceStatusLabel(source) {
  if (source.status === "active") return "已启用";
  if (source.status === "paused") return "已暂停";
  return "异常";
}

function renderSources() {
  const rows = filteredDataSources();
  const activeCount = state.dataSources.filter((source) => source.status === "active").length;
  const testedCount = state.dataSources.filter((source) => source.last_test_status).length;
  const passedCount = state.dataSources.filter((source) => source.last_test_status === "success").length;
  const failedCount = state.dataSources.filter((source) => source.last_test_status === "failed").length;
  const batch = state.sourceTestBatch;
  const batchLabel = batch.running
    ? `测试中 ${batch.completed}/${batch.total}`
    : "测试全部来源";
  $("#pageTitle").textContent = "数据源注册中心";
  $("#adminApp").innerHTML = `
    <section class="view-page">
      <div class="view-header">
        <div><h2>数据源与 Agent 动态分配</h2><p>注册、验证并控制生产 Runtime 下一次任务实际读取的来源。</p></div>
        <div class="view-actions">
          <button class="ghost-button" data-test-all-sources ${batch.running ? "disabled" : ""}>${batchLabel}</button>
          <button class="primary-button" data-create-source>＋ 新增数据源</button>
        </div>
      </div>
      <section class="source-registry-summary">
        <div><span>注册来源</span><strong>${fmt(state.dataSources.length)}</strong></div>
        <div><span>启用来源</span><strong>${fmt(activeCount)}</strong></div>
        <div><span>已测试</span><strong>${fmt(testedCount)}</strong></div>
        <div><span>测试通过</span><strong>${fmt(passedCount)}</strong></div>
        <div><span>测试失败</span><strong>${fmt(failedCount)}</strong></div>
      </section>
      <article class="table-panel source-registry-panel">
        <div class="table-toolbar">
          <div class="content-filters">
            <input id="sourceSearch" value="${escapeHtml(state.sourceQuery)}" placeholder="搜索机构、来源或 URL" />
            <select id="sourceCategoryFilter">
              <option value="all">全部分类</option>
              ${state.categories.map((category) => `<option value="${category.slug}" ${state.sourceCategory === category.slug ? "selected" : ""}>${escapeHtml(category.name)}</option>`).join("")}
            </select>
            <select id="sourceStatusFilter">
              <option value="all">全部状态</option>
              <option value="active" ${state.sourceStatus === "active" ? "selected" : ""}>启用</option>
              <option value="paused" ${state.sourceStatus === "paused" ? "selected" : ""}>暂停</option>
              <option value="error" ${state.sourceStatus === "error" ? "selected" : ""}>异常</option>
            </select>
          </div>
          <span class="source-result-count">${rows.length} 条结果</span>
        </div>
        <div class="source-table-wrap">
          <table class="data-table source-table">
            <thead><tr><th>来源</th><th>分类 / 采集</th><th>可信度</th><th>分配 Agent</th><th>连通性</th><th>状态</th><th>操作</th></tr></thead>
            <tbody>
              ${rows.length ? rows.map((source) => {
                const externalUrl = safeExternalUrl(source.url);
                const testStatus = source.last_test_status
                  ? `<span class="status-pill ${source.last_test_status === "success" ? "running" : "error"}">${source.last_test_status === "success" ? "通过" : "失败"}</span><small>${escapeHtml(source.last_test_message || "")}</small>`
                  : "<span class=\"test-pending\">尚未测试</span>";
                return `<tr>
                  <td class="source-identity"><strong>${escapeHtml(source.name)}</strong><span>${escapeHtml(source.publisher)} · ${escapeHtml(source.source_type)}</span>${externalUrl ? `<a href="${escapeHtml(externalUrl)}" target="_blank" rel="noreferrer">${escapeHtml(source.url)}</a>` : ""}</td>
                  <td><span class="access-pill">${escapeHtml(source.category_name)}</span><small class="source-method">${escapeHtml(sourceMethodLabels[source.ingestion_method] || source.ingestion_method)} · ${escapeHtml(source.access_model)}${source.credentialsConfigured ? " · 已绑定凭据" : ""}</small></td>
                  <td><b class="trust-tier t${source.trust_tier}">T${source.trust_tier}</b><small class="source-method">最多 ${source.max_items} 条</small></td>
                  <td><div class="assigned-agent-list">${source.assignments.length ? source.assignments.map((assignment) => `<span>${escapeHtml(assignment.agent_name)}</span>`).join("") : "<em>未分配</em>"}</div></td>
                  <td class="source-test-state">${testStatus}</td>
                  <td><span class="status-pill ${source.status === "active" ? "running" : source.status}">${sourceStatusLabel(source)}</span></td>
                  <td><div class="action-group">
                    <button class="table-action" data-test-source="${source.id}" title="测试连通性"><svg><use href="#i-refresh"></use></svg></button>
                    <button class="table-action" data-edit-source="${source.id}" title="编辑"><svg><use href="#i-settings"></use></svg></button>
                    <button class="table-action" data-toggle-source="${source.id}" data-source-status="${source.status}" title="${source.status === "active" ? "暂停" : "启用"}"><svg><use href="#${source.status === "active" ? "i-pause" : "i-play"}"></use></svg></button>
                    <button class="table-action danger" data-delete-source="${source.id}" title="删除">×</button>
                  </div></td>
                </tr>`;
              }).join("") : '<tr><td colspan="7"><div class="empty-state">没有符合条件的数据源。</div></td></tr>'}
            </tbody>
          </table>
        </div>
      </article>
    </section>
  `;
}

function updateSourceAuthFields() {
  const form = $("#dataSourceForm");
  const authType = form.elements.authType.value;
  $("#sourceApiKeyOptions").hidden = authType !== "apiKeyHeader";
  $("#sourceTokenCredential").hidden = !["bearer", "apiKeyHeader", "cookie"].includes(authType);
  $("#sourceBasicCredential").hidden = authType !== "basic";
  const tokenInput = form.elements.credentialToken;
  tokenInput.placeholder = authType === "cookie"
    ? "Cookie 请求头内容；留空则保留现有 Secret"
    : authType === "apiKeyHeader"
      ? "新 API Key；留空则保留现有 Secret"
      : "新 Bearer Token；留空则保留现有 Secret";
}

function openDataSourceModal(sourceId = null) {
  const form = $("#dataSourceForm");
  form.reset();
  form.elements.sourceId.value = sourceId || "";
  form.elements.status.value = "active";
  form.elements.trustTier.value = "1";
  form.elements.maxItems.value = "4";
  form.elements.requestsPerSecond.value = "2";
  form.elements.cacheTtlSeconds.value = "0";
  form.elements.maxRetries.value = "1";
  form.elements.requestUserAgent.value = "";
  form.elements.accessModel.value = "open";
  form.elements.ingestionMethod.value = "feed";
  form.elements.respectRobots.checked = true;
  form.elements.authType.value = "none";
  form.elements.apiKeyHeader.value = "X-API-Key";
  form.elements.secretArn.value = "";
  form.elements.credentialToken.value = "";
  form.elements.credentialUsername.value = "";
  form.elements.credentialPassword.value = "";
  form.elements.removeCredential.checked = false;
  $("#dataSourceCategory").innerHTML = state.categories.map((category) => `<option value="${category.slug}">${escapeHtml(category.name)}</option>`).join("");
  const source = sourceId ? state.dataSources.find((item) => item.id === Number(sourceId)) : null;
  const assigned = new Set(source?.agentIds || []);
  $("#dataSourceAgents").innerHTML = state.crawlers.map((crawler) => `
    <label><input type="checkbox" name="agentIds" value="${crawler.id}" ${assigned.has(crawler.id) ? "checked" : ""} /><span><strong>${escapeHtml(crawler.name)}</strong><small>${escapeHtml(crawler.kind)}</small></span></label>
  `).join("");
  if (source) {
    form.elements.publisher.value = source.publisher;
    form.elements.name.value = source.name;
    form.elements.url.value = source.url;
    form.elements.categorySlug.value = source.category_slug;
    form.elements.sourceType.value = source.source_type;
    form.elements.ingestionMethod.value = source.ingestion_method;
    form.elements.accessModel.value = source.access_model;
    form.elements.status.value = source.status;
    form.elements.trustTier.value = String(source.trust_tier);
    form.elements.maxItems.value = String(source.max_items);
    form.elements.respectRobots.checked = Boolean(source.respect_robots);
    form.elements.notes.value = source.notes || "";
    const requestPolicy = source.config?.requestPolicy || {};
    form.elements.requestsPerSecond.value = String(requestPolicy.requestsPerSecond ?? 2);
    form.elements.cacheTtlSeconds.value = String(requestPolicy.cacheTtlSeconds ?? 0);
    form.elements.maxRetries.value = String(requestPolicy.maxRetries ?? 1);
    form.elements.requestUserAgent.value = requestPolicy.userAgent || "";
    const auth = source.config?.auth || {};
    form.elements.authType.value = auth.type || "none";
    form.elements.apiKeyHeader.value = auth.headerName || "X-API-Key";
    form.elements.secretArn.value = source.secret_arn || "";
  }
  updateSourceAuthFields();
  $("#dataSourceModalTitle").textContent = source ? "编辑数据源" : "新增数据源";
  $("#dataSourceModal").classList.add("open");
  $("#dataSourceModal").setAttribute("aria-hidden", "false");
  setTimeout(() => form.elements.publisher.focus(), 40);
}

function closeDataSourceModal() {
  $("#dataSourceModal").classList.remove("open");
  $("#dataSourceModal").setAttribute("aria-hidden", "true");
}

async function saveDataSource(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const sourceId = form.elements.sourceId.value;
  const existing = sourceId
    ? state.dataSources.find((item) => item.id === Number(sourceId))
    : null;
  const requestPolicy = {
    ...(existing?.config?.requestPolicy || {}),
    requestsPerSecond: Number(form.elements.requestsPerSecond.value),
    cacheTtlSeconds: Number(form.elements.cacheTtlSeconds.value),
    maxRetries: Number(form.elements.maxRetries.value),
    retryStatusCodes: existing?.config?.requestPolicy?.retryStatusCodes || [429, 503],
    maxRetryAfterSeconds: existing?.config?.requestPolicy?.maxRetryAfterSeconds || 60,
  };
  const requestUserAgent = form.elements.requestUserAgent.value.trim();
  if (requestUserAgent) requestPolicy.userAgent = requestUserAgent;
  else delete requestPolicy.userAgent;
  const authType = form.elements.authType.value;
  const auth = { type: authType };
  if (authType === "apiKeyHeader") {
    auth.headerName = form.elements.apiKeyHeader.value.trim() || "X-API-Key";
    auth.secretKey = "apiKey";
  } else if (authType === "bearer") {
    auth.tokenKey = "token";
  } else if (authType === "basic") {
    auth.usernameKey = "username";
    auth.passwordKey = "password";
  } else if (authType === "cookie") {
    auth.cookieKey = "cookie";
  }
  const payload = {
    publisher: form.elements.publisher.value,
    name: form.elements.name.value,
    url: form.elements.url.value,
    categorySlug: form.elements.categorySlug.value,
    sourceType: form.elements.sourceType.value,
    ingestionMethod: form.elements.ingestionMethod.value,
    accessModel: form.elements.accessModel.value,
    status: form.elements.status.value,
    trustTier: Number(form.elements.trustTier.value),
    maxItems: Number(form.elements.maxItems.value),
    respectRobots: form.elements.respectRobots.checked,
    notes: form.elements.notes.value,
    secretArn: form.elements.secretArn.value.trim(),
    removeCredential: form.elements.removeCredential.checked,
    config: {
      ...(existing?.config || {}),
      requestPolicy,
      auth,
    },
    agentIds: $$('input[name="agentIds"]:checked', form).map((input) => Number(input.value)),
  };
  const token = form.elements.credentialToken.value.trim();
  const username = form.elements.credentialUsername.value.trim();
  const password = form.elements.credentialPassword.value;
  if (authType === "bearer" && token) payload.credential = { token };
  if (authType === "apiKeyHeader" && token) payload.credential = { apiKey: token };
  if (authType === "cookie" && token) payload.credential = { cookie: token };
  if (authType === "basic" && (username || password)) {
    if (!username || !password) {
      showToast("认证信息不完整", "Basic Auth 必须同时提供用户名和密码");
      return;
    }
    payload.credential = { username, password };
  }
  await api(sourceId ? `/api/admin/data-sources/${sourceId}` : "/api/admin/data-sources", {
    method: sourceId ? "PATCH" : "POST",
    body: JSON.stringify(payload),
  });
  closeDataSourceModal();
  state.dataSources = await api("/api/admin/data-sources");
  state.crawlers = await api("/api/admin/crawlers");
  $("#sourceRegistryCount").textContent = state.dataSources.length;
  renderSources();
  showToast(sourceId ? "数据源已更新" : "数据源已注册", "下一次 Agent 任务将读取最新分配");
}

async function pollSourceTestBatch() {
  state.sourceTestBatch = await api("/api/admin/data-sources/test-batch");
  if (state.view === "sources") renderSources();
  if (state.sourceTestBatch.running) {
    clearTimeout(pollSourceTestBatch.timer);
    pollSourceTestBatch.timer = setTimeout(pollSourceTestBatch, 2500);
    return;
  }
  state.dataSources = await api("/api/admin/data-sources");
  if (state.view === "sources") renderSources();
  showToast(
    "批量连通性测试完成",
    `${state.sourceTestBatch.success} 个通过 · ${state.sourceTestBatch.failed} 个失败`,
  );
}

async function testAllDataSources() {
  state.sourceTestBatch = await api("/api/admin/data-sources/test-all", {
    method: "POST",
    body: "{}",
  });
  renderSources();
  showToast("批量测试已启动", `${state.sourceTestBatch.total} 个来源在后台测试`);
  clearTimeout(pollSourceTestBatch.timer);
  pollSourceTestBatch.timer = setTimeout(pollSourceTestBatch, 1200);
}

function updateCrawlerScheduleFields() {
  const daily = $("#crawlerSchedulePreset").value === "daily";
  $("#crawlerDailyTimeField").hidden = !daily;
  $("#crawlerDailyTime").required = daily;
}

function openCrawlerScheduleModal(button) {
  const schedule = button.dataset.crawlerSchedule === "0 */1 * * *"
    ? "0 * * * *"
    : button.dataset.crawlerSchedule;
  const presetValues = new Set(crawlerScheduleOptions.map(([value]) => value));
  $("#crawlerScheduleId").value = button.dataset.editCrawlerSchedule;
  $("#crawlerScheduleName").textContent = button.dataset.crawlerName;
  if (presetValues.has(schedule)) {
    $("#crawlerSchedulePreset").value = schedule;
  } else {
    const daily = schedule.match(/^([0-5]?\d) ([01]?\d|2[0-3]) \* \* \*$/);
    $("#crawlerSchedulePreset").value = "daily";
    $("#crawlerDailyTime").value = daily
      ? `${String(Number(daily[2])).padStart(2, "0")}:${String(Number(daily[1])).padStart(2, "0")}`
      : "00:00";
  }
  updateCrawlerScheduleFields();
  $("#crawlerScheduleModal").classList.add("open");
  $("#crawlerScheduleModal").setAttribute("aria-hidden", "false");
}

function closeCrawlerScheduleModal() {
  $("#crawlerScheduleModal").classList.remove("open");
  $("#crawlerScheduleModal").setAttribute("aria-hidden", "true");
}

async function saveCrawlerSchedule(event) {
  event.preventDefault();
  const crawlerId = $("#crawlerScheduleId").value;
  const preset = $("#crawlerSchedulePreset").value;
  let schedule = preset;
  if (preset === "daily") {
    const [hour, minute] = $("#crawlerDailyTime").value.split(":").map(Number);
    schedule = `${minute} ${hour} * * *`;
  }
  const result = await api(`/api/admin/crawlers/${crawlerId}`, {
    method: "PATCH",
    body: JSON.stringify({ schedule }),
  });
  closeCrawlerScheduleModal();
  state.crawlers = await api("/api/admin/crawlers");
  renderCrawlers();
  const syncState = result.eventbridge
    ? eventbridgeStateLabel(result.eventbridge.state)
    : "已保存到 PostgreSQL";
  showToast("定期时间已更新", `${result.scheduleLabel} · UTC · ${syncState}`);
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
            ${run.verification?.status ? `<div class="research-process"><h3>证据审计</h3><div><b>${run.verification.status === "verified" ? "已通过" : "需要人工复核"} · ${run.verification.score || 0}</b><span>${run.verification.writingStyle?.name ? `${run.verification.writingStyle.name} · ` : ""}${run.verification.notes || ""}</span><p>${(run.verification.unsupportedClaims || []).join("；") || "未发现无证据支持的关键表述"}</p></div></div>` : ""}
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
  if (view === "sources") renderSources();
  if (view === "jobs") renderJobs();
  if (view === "settings") renderSettings();
}

async function loadAll() {
  [state.metrics, state.articles, state.research, state.categories, state.crawlers, state.dataSources, state.sourceTestBatch, state.jobs, state.events, state.settings] = await Promise.all([
    api(metricsPath()),
    api("/api/admin/articles"),
    api("/api/admin/research"),
    api("/api/v1/categories"),
    api("/api/admin/crawlers"),
    api("/api/admin/data-sources"),
    api("/api/admin/data-sources/test-batch"),
    api("/api/admin/jobs"),
    api("/api/admin/events"),
    api("/api/admin/settings"),
  ]);
  $("#contentCount").textContent = state.articles.length;
  $("#researchCount").textContent = state.research.filter((item) => item.status === "completed").length;
  $("#sourceRegistryCount").textContent = state.dataSources.length;
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
      state.metrics = await api(metricsPath());
      renderDashboard();
    }
    if (event.target.closest("[data-apply-custom-range]")) {
      const start = $("#rangeStart").value;
      const end = $("#rangeEnd").value;
      if (!start || !end || start > end) {
        showToast("时间范围无效", "请选择正确的开始和结束日期");
        return;
      }
      state.customStart = start;
      state.customEnd = end;
      state.range = "custom";
      state.metrics = await api(metricsPath());
      renderDashboard();
    }
    const articleDetail = event.target.closest("[data-open-article]");
    if (articleDetail) await openArticleDetail(Number(articleDetail.dataset.openArticle));
    const batchAction = event.target.closest("[data-batch-action]");
    if (batchAction) await batchArticles(batchAction.dataset.batchAction);
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
    const editSchedule = event.target.closest("[data-edit-crawler-schedule]");
    if (editSchedule) openCrawlerScheduleModal(editSchedule);
    const toggle = event.target.closest("[data-toggle-crawler]");
    if (toggle) {
      const status = toggle.dataset.status === "paused" ? "running" : "paused";
      await api(`/api/admin/crawlers/${toggle.dataset.toggleCrawler}`, { method: "PATCH", body: JSON.stringify({ status }) });
      state.crawlers = await api("/api/admin/crawlers");
      renderCrawlers();
      showToast(status === "paused" ? "Agent 已暂停" : "Agent 已恢复");
    }
    if (event.target.closest("[data-create-source]")) openDataSourceModal();
    if (event.target.closest("[data-test-all-sources]")) await testAllDataSources();
    const editSource = event.target.closest("[data-edit-source]");
    if (editSource) openDataSourceModal(Number(editSource.dataset.editSource));
    const testSource = event.target.closest("[data-test-source]");
    if (testSource) {
      const source = state.dataSources.find((item) => item.id === Number(testSource.dataset.testSource));
      try {
        const result = await api(`/api/admin/data-sources/${testSource.dataset.testSource}/test`, {
          method: "POST",
          body: "{}",
        });
        showToast("连通性测试通过", `${source?.name || "数据源"} · ${result.message}`);
      } catch (error) {
        showToast("连通性测试失败", error.payload?.message || error.message);
      }
      state.dataSources = await api("/api/admin/data-sources");
      renderSources();
    }
    const toggleSource = event.target.closest("[data-toggle-source]");
    if (toggleSource) {
      const status = toggleSource.dataset.sourceStatus === "active" ? "paused" : "active";
      await api(`/api/admin/data-sources/${toggleSource.dataset.toggleSource}`, {
        method: "PATCH",
        body: JSON.stringify({ status }),
      });
      state.dataSources = await api("/api/admin/data-sources");
      state.crawlers = await api("/api/admin/crawlers");
      renderSources();
      showToast(status === "active" ? "数据源已启用" : "数据源已暂停", "Runtime 分配已立即更新");
    }
    const deleteSource = event.target.closest("[data-delete-source]");
    if (deleteSource) {
      const source = state.dataSources.find((item) => item.id === Number(deleteSource.dataset.deleteSource));
      if (window.confirm(`确认删除数据源“${source?.name || deleteSource.dataset.deleteSource}”？Agent 分配关系会一并删除。`)) {
        await api(`/api/admin/data-sources/${deleteSource.dataset.deleteSource}`, { method: "DELETE" });
        state.dataSources = await api("/api/admin/data-sources");
        state.crawlers = await api("/api/admin/crawlers");
        $("#sourceRegistryCount").textContent = state.dataSources.length;
        renderSources();
        showToast("数据源已删除", "历史文章引用和研究证据不受影响");
      }
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
  document.addEventListener("change", (event) => {
    const article = event.target.closest("[data-select-article]");
    if (article) {
      const articleId = Number(article.dataset.selectArticle);
      if (article.checked) state.selectedArticles.add(articleId);
      else state.selectedArticles.delete(articleId);
      updateBulkActions();
    }
    if (event.target.id === "selectAllArticles") {
      const visibleIds = filteredContentRows().map((item) => item.id);
      visibleIds.forEach((articleId) => {
        if (event.target.checked) state.selectedArticles.add(articleId);
        else state.selectedArticles.delete(articleId);
      });
      renderContentRows();
    }
    if (event.target.id === "sourceCategoryFilter") {
      state.sourceCategory = event.target.value;
      renderSources();
    }
    if (event.target.id === "sourceStatusFilter") {
      state.sourceStatus = event.target.value;
      renderSources();
    }
  });
  document.addEventListener("input", (event) => {
    if (event.target.id === "sourceSearch") {
      state.sourceQuery = event.target.value;
      renderSources();
      $("#sourceSearch")?.focus();
      $("#sourceSearch")?.setSelectionRange(state.sourceQuery.length, state.sourceQuery.length);
    }
  });
  $("#closeArticleModal").addEventListener("click", closeArticleModal);
  $("#cancelArticleModal").addEventListener("click", closeArticleModal);
  $("#articleModal").addEventListener("click", (event) => {
    if (event.target === $("#articleModal")) closeArticleModal();
  });
  $("#closeContentDetail").addEventListener("click", closeArticleDetail);
  $("#contentDetailModal").addEventListener("click", (event) => {
    if (event.target === $("#contentDetailModal")) closeArticleDetail();
  });
  $("#closeCrawlerScheduleModal").addEventListener("click", closeCrawlerScheduleModal);
  $("#cancelCrawlerScheduleModal").addEventListener("click", closeCrawlerScheduleModal);
  $("#crawlerScheduleModal").addEventListener("click", (event) => {
    if (event.target === $("#crawlerScheduleModal")) closeCrawlerScheduleModal();
  });
  $("#crawlerSchedulePreset").addEventListener("change", updateCrawlerScheduleFields);
  $("#crawlerScheduleForm").addEventListener("submit", saveCrawlerSchedule);
  $("#closeDataSourceModal").addEventListener("click", closeDataSourceModal);
  $("#cancelDataSourceModal").addEventListener("click", closeDataSourceModal);
  $("#dataSourceModal").addEventListener("click", (event) => {
    if (event.target === $("#dataSourceModal")) closeDataSourceModal();
  });
  $("#dataSourceForm").addEventListener("submit", saveDataSource);
  $("#sourceAuthType").addEventListener("change", () => {
    const form = $("#dataSourceForm");
    if (form.elements.authType.value !== "none") {
      form.elements.accessModel.value = "authenticated";
    }
    updateSourceAuthFields();
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
