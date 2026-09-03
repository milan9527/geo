const API = location.origin;
const state = { categories: [], articles: [], site: null };

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const formatNumber = (value) => new Intl.NumberFormat("zh-CN").format(value || 0);
const formatDate = (value) =>
  new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "long", day: "numeric" }).format(new Date(value));

async function api(path, options = {}) {
  const response = await fetch(`${API}${path}`, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) throw new Error(`API ${response.status}`);
  return response.json();
}

function navigate(href) {
  history.pushState({}, "", href);
  renderRoute();
}

function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.add("show");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => toast.classList.remove("show"), 2800);
}

function storyVisual(article) {
  return `
    <div class="story-visual ${article.heroStyle}">
      <span class="visual-label">${article.category.eyebrow}</span>
    </div>
  `;
}

function storyCard(article, options = {}) {
  const { large = false } = options;
  return `
    <a class="story-card ${large ? "large" : ""}" href="/article/${article.slug}" data-link>
      ${storyVisual(article)}
      <div class="story-body">
        <div class="story-meta">
          <span class="story-category">${article.category.name}</span>
          <i></i><span>${article.readMinutes} 分钟阅读</span>
        </div>
        <h3>${article.title}</h3>
        <p>${article.dek}</p>
        <div class="story-footer">
          <strong>权威度 ${article.authorityScore}</strong>
          <span>${formatNumber(article.citationCount)} 次引用</span>
          <svg><use href="#icon-arrow"></use></svg>
        </div>
      </div>
    </a>
  `;
}

function compactStory(article) {
  return `
    <a class="compact-story" href="/article/${article.slug}" data-link>
      ${storyVisual(article)}
      <div class="story-body">
        <div class="story-meta"><span class="story-category">${article.category.name}</span></div>
        <h3>${article.title}</h3>
        <p>${article.dek}</p>
      </div>
    </a>
  `;
}

function setMeta({ title, description, article }) {
  document.title = title;
  $('meta[name="description"]').setAttribute("content", description);
  $("#pageSchema")?.remove();
  if (article) {
    const schema = document.createElement("script");
    schema.id = "pageSchema";
    schema.type = "application/ld+json";
    schema.textContent = JSON.stringify({
      "@context": "https://schema.org",
      "@type": "AnalysisNewsArticle",
      headline: article.title,
      description: article.dek,
      datePublished: article.publishedAt,
      dateModified: article.updatedAt,
      author: { "@type": "Person", name: article.author, jobTitle: article.authorRole },
      about: article.keywords,
      citation: article.sources.map((source) => source.url),
      publisher: { "@type": "Organization", name: "Aperture Intelligence" },
    });
    document.head.append(schema);
  }
}

function renderNav() {
  $("#primaryNav").innerHTML = state.categories
    .map((category) => `<a href="/category/${category.slug}" data-link>${category.name}</a>`)
    .join("");
  updateNavActive();
}

function updateNavActive() {
  const slug = location.pathname.match(/^\/category\/([^/]+)/)?.[1];
  $$("#primaryNav a").forEach((link) => {
    link.classList.toggle("active", link.getAttribute("href") === `/category/${slug}`);
  });
}

function renderHome() {
  const featured = state.articles.filter((item) => item.featured);
  const hero = featured[0] || state.articles[0];
  const remaining = state.articles.filter((item) => item.id !== hero.id);
  const signal = remaining.find((item) => item.category.slug === "agent") || remaining[0];
  setMeta({
    title: "Aperture Intelligence · 面向 AI 时代的技术与商业研究",
    description: "提供 AI、Agent、云计算、电商媒体与金融市场的深度研究与可验证专业判断。",
  });

  $("#app").innerHTML = `
    <section class="home-hero">
      <div class="hero-inner">
        <div class="hero-copy">
          <p class="hero-eyebrow">INDEPENDENT TECHNOLOGY INTELLIGENCE</p>
          <h1>理解 AI 时代的<br /><em>关键变量</em></h1>
          <p class="hero-summary">${hero.summary}</p>
          <a class="hero-cta" href="/article/${hero.slug}" data-link>
            阅读首席研究 <svg><use href="#icon-arrow"></use></svg>
          </a>
          <div class="hero-meta">
            <span>由 <b>${hero.author}</b> 撰写</span><i></i>
            <span>${formatDate(hero.publishedAt)}</span><i></i>
            <span>权威度 ${hero.authorityScore}</span>
          </div>
        </div>
        <div class="hero-intelligence" aria-hidden="true">
          <div class="signal-orbit">
            <div class="signal-core"><strong>${hero.authorityScore}</strong><span>AUTHORITY</span></div>
            <div class="signal-node one">证据来源<b>38 verified</b></div>
            <div class="signal-node two">AI 引用<b>${formatNumber(hero.citationCount)}</b></div>
            <div class="signal-node three">内容更新<b>Today</b></div>
            <i class="signal-dot a"></i><i class="signal-dot b"></i><i class="signal-dot c"></i>
          </div>
        </div>
      </div>
    </section>

    <div class="signal-strip">
      <div class="signal-strip-inner">
        <span class="signal-strip-label">今日研究信号</span>
        <a class="signal-strip-content" href="/article/${signal.slug}" data-link>
          <span>高关注</span><b>${signal.title}</b>
        </a>
        <span class="signal-strip-time">更新于 24 分钟前</span>
      </div>
    </div>

    <section class="section">
      <div class="section-heading">
        <div>
          <p class="section-eyebrow">EDITOR'S SELECTION</p>
          <h2>值得关注的核心判断</h2>
          <p>从复杂信号中提炼真正影响技术决策与商业价值的变量。</p>
        </div>
        <a class="section-link" href="/category/agent" data-link>浏览全部研究 <svg><use href="#icon-arrow"></use></svg></a>
      </div>
      <div class="featured-grid">
        ${storyCard(hero, { large: true })}
        <div class="side-stories">
          ${remaining.slice(0, 2).map(compactStory).join("")}
        </div>
      </div>
    </section>

    <section class="category-band">
      <div class="category-band-inner">
        <div class="section-heading">
          <div>
            <p class="section-eyebrow">RESEARCH COVERAGE</p>
            <h2>五个研究领域，一套证据标准</h2>
            <p>覆盖技术基础设施、产业采用与资本市场的完整传导链。</p>
          </div>
        </div>
        <div class="category-list">
          ${state.categories.map((category, index) => `
            <a class="category-item" href="/category/${category.slug}" data-link>
              <span class="category-index">0${index + 1}</span>
              <h3>${category.name}</h3>
              <p>${category.description}</p>
              <span>${category.articleCount} 篇研究 <svg><use href="#icon-arrow"></use></svg></span>
            </a>
          `).join("")}
        </div>
      </div>
    </section>

    <section class="section">
      <div class="section-heading">
        <div>
          <p class="section-eyebrow">LATEST RESEARCH</p>
          <h2>最新分析</h2>
          <p>持续追踪最新发布、行业变化与可操作的研究框架。</p>
        </div>
      </div>
      <div class="latest-grid">
        ${remaining.slice(2, 8).map((article) => storyCard(article)).join("")}
      </div>
    </section>

    <section class="section">
      <div class="method-grid">
        <div class="method-intro">
          <p class="section-eyebrow">WHY APERTURE</p>
          <h2>为人类判断，也为机器理解</h2>
          <p>每篇研究都同时提供清晰叙事、结构化声明和可追溯来源，让专业读者与 AI Agent 都能准确使用。</p>
          <a class="section-link" href="/methodology" data-link>了解研究方法 <svg><use href="#icon-arrow"></use></svg></a>
        </div>
        <div class="method-list">
          <div class="method-item"><span>01</span><div><h3>来源分级与交叉验证</h3><p>区分官方文档、监管数据、行业研究与二手报道，重要结论由独立来源相互验证。</p></div></div>
          <div class="method-item"><span>02</span><div><h3>声明到证据的明确映射</h3><p>记录每个事实的时间、口径与来源，使内容能够被复核、更新和安全引用。</p></div></div>
          <div class="method-item"><span>03</span><div><h3>人类与 Agent 双重表达</h3><p>面向读者提供完整分析，同时为 Agent 输出实体、声明、来源和许可信息。</p></div></div>
        </div>
      </div>
    </section>
  `;
}

async function renderCategory(slug) {
  const category = state.categories.find((item) => item.slug === slug);
  if (!category) return renderNotFound();
  let articles = state.articles.filter((item) => item.category.slug === slug);
  if (!articles.length) articles = await api(`/api/v1/articles?category=${encodeURIComponent(slug)}`);
  setMeta({
    title: `${category.name} · Aperture Intelligence`,
    description: category.description,
  });
  $("#app").innerHTML = `
    <section class="category-hero">
      <div class="category-hero-inner">
        <p class="article-eyebrow">${category.eyebrow}</p>
        <h1>${category.name}</h1>
        <p>${category.description} 我们关注变化背后的系统影响，而不仅是事件本身。</p>
        <span class="category-count">${articles.length} 篇已发布研究</span>
      </div>
    </section>
    <section class="section">
      <div class="section-heading">
        <div><p class="section-eyebrow">ALL RESEARCH</p><h2>全部分析</h2></div>
      </div>
      <div class="category-articles">
        ${articles.length ? articles.map((article) => storyCard(article)).join("") : '<div class="empty-state">该分类暂无已发布内容。</div>'}
      </div>
    </section>
  `;
}

function renderSection(section) {
  const paragraphs = (section.paragraphs || []).map((text) => `<p>${text}</p>`).join("");
  const stat = section.stat
    ? `<div class="article-stat"><strong>${section.stat.value}</strong><span>${section.stat.label}</span></div>`
    : "";
  const quote = section.quote ? `<blockquote class="article-quote">${section.quote}</blockquote>` : "";
  const table = section.rows
    ? `<div class="article-table-wrap"><table class="article-table"><thead><tr>${section.headers.map((cell) => `<th>${cell}</th>`).join("")}</tr></thead><tbody>${section.rows.map((row) => `<tr>${row.map((cell) => `<td>${cell}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`
    : "";
  const bullets = section.bullets
    ? `<ul class="article-bullets">${section.bullets.map((item) => `<li>${item}</li>`).join("")}</ul>`
    : "";
  return `
    <section class="article-section">
      ${section.number ? `<span class="section-number">${section.number}</span>` : ""}
      <h2>${section.heading}</h2>
      ${paragraphs}${stat}${quote}${table}${bullets}
    </section>
  `;
}

async function renderArticle(slug) {
  try {
    const article = await api(`/api/v1/articles/${encodeURIComponent(slug)}`);
    setMeta({
      title: `${article.title} · Aperture Intelligence`,
      description: article.dek,
      article,
    });
    const initials = article.author.slice(0, 2);
    $("#app").innerHTML = `
      <article class="article-page">
        <header class="article-header">
          <div class="article-header-inner">
            <p class="article-eyebrow">${article.category.eyebrow}</p>
            <h1>${article.title}</h1>
            <p class="article-dek">${article.dek}</p>
            <div class="article-byline">
              <span class="author-avatar">${initials}</span>
              <span><b>${article.author}</b> · ${article.authorRole}</span>
              <i></i><span>${formatDate(article.publishedAt)}</span>
              <i></i><span>${article.readMinutes} 分钟阅读</span>
            </div>
          </div>
        </header>
        <div class="article-layout">
          <div class="article-content">
            <p class="article-summary">${article.summary}</p>
            ${article.sections.map(renderSection).join("")}
          </div>
          <aside class="article-sidebar">
            <div class="sticky-sidebar">
              <div class="article-facts">
                <strong>RESEARCH PROFILE</strong>
                <div class="fact-row"><span>内容权威度</span><b>${article.authorityScore} / 100</b></div>
                <div class="authority-meter"><div><span style="width:${article.authorityScore}%"></span></div><small>来源、时效、专业度综合评分</small></div>
                <div class="fact-row"><span>AI 引用</span><b>${formatNumber(article.citationCount)}</b></div>
                <div class="fact-row"><span>证据来源</span><b>${article.sources.length} 个</b></div>
                <div class="fact-row"><span>访问模式</span><b>开放 A 页 + x402 B 页</b></div>
                <div class="fact-row"><span>最后更新</span><b>${formatDate(article.updatedAt)}</b></div>
              </div>
              <div class="machine-card">
                <div class="machine-card-header"><svg><use href="#icon-agent"></use></svg> AGENT-READY CONTENT</div>
                <p>同一研究提供开放 A 页和由 Stripe Privy 收款钱包结算的 x402 B 页，用于真实 GEO 与付费转化对比。</p>
                <button data-machine-url="${API}/agent/v1/articles/${article.slug}">复制开放 A 页地址</button>
                <button class="paid-machine-url" data-machine-url="${API}/agent/v1/articles/${article.slug}/paid">复制 x402 B 页地址 · $${Number(article.agentPrice > 0 ? article.agentPrice : 0.002).toFixed(3)}</button>
              </div>
              <div class="source-list">
                <h3>主要来源</h3>
                ${article.sources.map((source) => `
                  <a class="source-item" href="${source.url}" target="_blank" rel="noreferrer">
                    <span>${source.publisher} · ${source.sourceType}</span>
                    <strong>${source.title}</strong>
                    <small>${source.publishedAt}</small>
                  </a>
                `).join("")}
              </div>
            </div>
          </aside>
        </div>
        <section class="related-band">
          <div class="related-inner">
            <div class="section-heading"><div><p class="section-eyebrow">CONTINUE READING</p><h2>相关研究</h2></div></div>
            <div class="related-grid">${article.related.map((item) => storyCard(item)).join("")}</div>
          </div>
        </section>
      </article>
    `;
    api("/api/v1/track", {
      method: "POST",
      body: JSON.stringify({ eventType: "article_render", articleSlug: article.slug, metadata: { path: location.pathname } }),
    }).catch(() => {});
  } catch {
    renderNotFound();
  }
}

function renderMethodology() {
  setMeta({
    title: "研究方法 · Aperture Intelligence",
    description: "Aperture Intelligence 的来源、证据、分析与机器可读内容方法。",
  });
  const steps = [
    ["定义问题", "把宽泛趋势拆解为可验证的问题、实体和时间范围，先确定什么证据能够改变判断。"],
    ["采集与分级", "使用官方文档、监管数据和一手资料作为核心来源，并为每个来源记录发布者、时间和类型。"],
    ["交叉验证", "关键事实至少由两个独立证据点支持；冲突信息保留差异，不用模糊措辞掩盖不确定性。"],
    ["专业分析", "由行业框架解释事实之间的因果关系、约束与可能的二阶影响，明确区分事实与判断。"],
    ["GEO 结构化", "输出实体、声明、证据、时间与许可元数据，使生成式引擎可以准确抽取和引用。"],
    ["持续更新", "通过爬虫 Agent 监控变化，重要事实变化后触发复核，并保留更新时间与修订原因。"],
  ];
  $("#app").innerHTML = `
    <div class="methodology-page">
      <section class="methodology-hero"><div><p class="article-eyebrow">RESEARCH METHODOLOGY</p><h1>研究方法</h1><p>专业内容不是观点的堆叠，而是问题、证据与判断之间可复核的关系。</p></div></section>
      <section class="methodology-body">
        ${steps.map((step, index) => `<div class="methodology-step"><span>0${index + 1}</span><div><h2>${step[0]}</h2><p>${step[1]}</p></div></div>`).join("")}
      </section>
    </div>
  `;
}

function renderNotFound() {
  setMeta({ title: "页面未找到 · Aperture Intelligence", description: "请求的页面不存在。" });
  $("#app").innerHTML = `<div class="not-found"><span>404</span><h1>没有找到这项研究</h1><p>内容可能已更新或移动。</p><a href="/" data-link>返回首页</a></div>`;
}

async function renderRoute() {
  window.scrollTo(0, 0);
  updateNavActive();
  $("#primaryNav").classList.remove("open");
  const path = location.pathname.replace(/\/+$/, "") || "/";
  if (path === "/") renderHome();
  else if (path.startsWith("/category/")) await renderCategory(path.split("/")[2]);
  else if (path.startsWith("/article/")) await renderArticle(path.split("/")[2]);
  else if (path === "/methodology") renderMethodology();
  else renderNotFound();
  $("#app").focus({ preventScroll: true });
}

async function runSearch(term) {
  const results = term.trim() ? await api(`/api/v1/search?q=${encodeURIComponent(term.trim())}`) : [];
  $("#searchResults").innerHTML = term.trim()
    ? results.length
      ? results.map((item) => `<a class="search-result" href="/article/${item.slug}" data-link><div><span>${item.category.name}</span><h3>${item.title}</h3></div><small>${item.readMinutes} 分钟</small></a>`).join("")
      : '<div class="search-empty">没有找到匹配的研究内容。</div>'
    : "";
}

function initEvents() {
  document.addEventListener("click", (event) => {
    const link = event.target.closest("[data-link]");
    if (link && link.origin === location.origin) {
      event.preventDefault();
      navigate(link.pathname);
      $("#searchOverlay").classList.remove("open");
    }
    const machine = event.target.closest("[data-machine-url]");
    if (machine) {
      navigator.clipboard.writeText(machine.dataset.machineUrl).then(() => showToast("Agent API 地址已复制"));
    }
  });
  window.addEventListener("popstate", renderRoute);
  $("#menuButton").addEventListener("click", () => $("#primaryNav").classList.toggle("open"));
  $("#searchButton").addEventListener("click", () => {
    $("#searchOverlay").classList.add("open");
    $("#searchOverlay").setAttribute("aria-hidden", "false");
    setTimeout(() => $("#searchInput").focus(), 100);
  });
  $("#closeSearch").addEventListener("click", closeSearch);
  $("#searchOverlay").addEventListener("click", (event) => {
    if (event.target === $("#searchOverlay")) closeSearch();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeSearch();
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      $("#searchButton").click();
    }
  });
  let searchTimer;
  $("#searchInput").addEventListener("input", (event) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => runSearch(event.target.value).catch(() => {}), 220);
  });
  $$("[data-search-term]").forEach((button) => {
    button.addEventListener("click", () => {
      $("#searchInput").value = button.dataset.searchTerm;
      runSearch(button.dataset.searchTerm).catch(() => {});
    });
  });
}

function closeSearch() {
  $("#searchOverlay").classList.remove("open");
  $("#searchOverlay").setAttribute("aria-hidden", "true");
}

async function init() {
  initEvents();
  try {
    [state.site, state.categories, state.articles] = await Promise.all([
      api("/api/v1/site"),
      api("/api/v1/categories"),
      api("/api/v1/articles"),
    ]);
    renderNav();
    await renderRoute();
  } catch (error) {
    $("#app").innerHTML = `
      <div class="not-found"><span>!</span><h1>研究服务暂时不可用</h1>
      <p>请确认 API 服务已运行在 ${API}。</p></div>
    `;
  }
}

document.addEventListener("DOMContentLoaded", init);
