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

function setMeta({ title, description, article, robots = "index, follow, max-snippet:-1, max-image-preview:large" }) {
  document.title = title;
  $('meta[name="description"]').setAttribute("content", description);
  $('meta[name="robots"]')?.setAttribute("content", robots);
  const canonical = $('link[rel="canonical"]');
  const publicOrigin = canonical ? new URL(canonical.href).origin : location.origin;
  const canonicalPath = location.pathname.replace(/\/+$/, "") || "/";
  canonical?.setAttribute("href", `${publicOrigin}${canonicalPath}`);
  for (const [property, content] of Object.entries({
    "og:title": title,
    "og:description": description,
    "og:url": `${publicOrigin}${canonicalPath}`,
    "og:type": article ? "article" : "website",
  })) {
    let tag = $(`meta[property="${property}"]`);
    if (!tag) {
      tag = document.createElement("meta");
      tag.setAttribute("property", property);
      document.head.append(tag);
    }
    tag.setAttribute("content", content);
  }
  $$("script[data-page-schema]").forEach((schema) => schema.remove());
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
      author: {
        "@type": "Organization",
        name: "Aperture 研究编辑部",
        url: `${publicOrigin}/authors/research-desk`,
      },
      about: article.keywords,
      citation: article.sources.map((source) => source.url),
      publisher: {
        "@type": "Organization",
        name: "Aperture Intelligence",
        url: `${publicOrigin}/`,
      },
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

async function renderHome() {
  // Reuse the same published content for direct visits and in-app navigation.
  const response = await fetch("/", { headers: { Accept: "text/html" } });
  if (!response.ok) throw new Error(`Home ${response.status}`);
  const page = new DOMParser().parseFromString(await response.text(), "text/html");
  if (location.pathname !== "/") return;
  setMeta({
    title: page.title,
    description: $('meta[name="description"]', page).content,
  });
  $("#app").innerHTML = $("#app", page).innerHTML;
  $$("script[data-page-schema]", page).forEach((schema) => document.head.append(schema.cloneNode(true)));
}

async function renderCategory(slug) {
  const category = state.categories.find((item) => item.slug === slug);
  if (!category) return renderNotFound();
  let articles = state.articles.filter((item) => item.category.slug === slug);
  if (!articles.length) articles = await api(`/api/v1/articles?category=${encodeURIComponent(slug)}`);
  setMeta({
    title: category.seoTitle || `${category.name} · Aperture Intelligence`,
    description: category.seoDescription || category.description,
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
  const code = section.code ? `<pre class="article-code"><code>${String(section.code).replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[character]))}</code></pre>` : "";
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
      ${paragraphs}${stat}${quote}${table}${bullets}${code}
    </section>
  `;
}

async function renderArticle(slug) {
  try {
    const article = await api(`/api/v1/articles/${encodeURIComponent(slug)}`);
    if (article.slug !== decodeURIComponent(slug)) {
      history.replaceState({}, "", `/article/${encodeURIComponent(article.slug)}${location.search}${location.hash}`);
    }
    setMeta({
      title: article.seoTitle || `${article.title} · Aperture Intelligence`,
      description: article.seoDescription || article.dek,
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
              <span><a href="/authors/research-desk"><b>${article.author}</b></a> · ${article.authorRole}</span>
              <i></i><span>${formatDate(article.publishedAt)}</span>
              <i></i><span>${article.readMinutes} 分钟阅读</span>
            </div>
          </div>
        </header>
        <div class="article-layout">
          <div class="article-content">
            <p class="article-summary">${article.summary}</p>
            ${article.sections.map(renderSection).join("")}
            <div class="reader-follow"><h2>继续关注工程实践</h2><p>用 RSS 阅读器订阅新文章，或把这篇文章分享给正在解决同类问题的人。</p><a href="/feed.xml" data-rss>订阅 RSS 更新</a><button type="button" data-share-article>复制文章链接</button></div>
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
  } catch {
    renderNotFound();
  }
}

function renderMethodology() {
  setMeta({
    title: "研究方法 · Aperture Intelligence",
    description: "Aperture Intelligence 的来源分级、证据验证、行业分析与机器可读内容方法。",
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
  setMeta({ title: "页面未找到 · Aperture Intelligence", description: "请求的页面不存在。", robots: "noindex, nofollow" });
  $("#app").innerHTML = `<div class="not-found"><span>404</span><h1>没有找到这项研究</h1><p>内容可能已更新或移动。</p><a href="/" data-link>返回首页</a></div>`;
}

async function renderRoute() {
  window.apertureGrowth?.stopPage();
  window.scrollTo(0, 0);
  updateNavActive();
  $("#primaryNav").classList.remove("open");
  const path = location.pathname.replace(/\/+$/, "") || "/";
  if (path === "/") await renderHome();
  else if (path.startsWith("/category/")) await renderCategory(path.split("/")[2]);
  else if (path.startsWith("/article/")) await renderArticle(path.split("/")[2]);
  else if (path === "/methodology") renderMethodology();
  else renderNotFound();
  $("#app").focus({ preventScroll: true });
  window.apertureGrowth?.startPage();
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
    if (link && link.origin === location.origin && !event.metaKey && !event.ctrlKey && !event.shiftKey && event.button === 0) {
      event.preventDefault();
      const destinationSlug = link.pathname.match(/^\/article\/([^/]+)/)?.[1];
      api("/api/v1/track", {
        method: "POST",
        keepalive: true,
        body: JSON.stringify({
          eventType: "human_click",
          articleSlug: destinationSlug,
          metadata: {
            sourcePath: location.pathname,
            destinationPath: link.pathname,
          },
        }),
      }).catch(() => {});
      navigate(`${link.pathname}${link.search}${link.hash}`);
      $("#searchOverlay").classList.remove("open");
    }
    const machine = event.target.closest("[data-machine-url]");
    if (machine) {
      navigator.clipboard.writeText(machine.dataset.machineUrl).then(() => showToast("Agent API 地址已复制"));
      api("/api/v1/track", {
        method: "POST",
        keepalive: true,
        body: JSON.stringify({
          eventType: "machine_link_copy",
          articleSlug: location.pathname.match(/^\/article\/([^/]+)/)?.[1],
          metadata: { target: machine.dataset.machineUrl },
        }),
      }).catch(() => {});
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
  const serverRendered = document.body.dataset.ssr === "true";
  initEvents();
  if (serverRendered) window.apertureGrowth?.startPage();
  try {
    [state.site, state.categories, state.articles] = await Promise.all([
      api("/api/v1/site"),
      api("/api/v1/categories"),
      api("/api/v1/articles"),
    ]);
    renderNav();
    if (serverRendered) {
      updateNavActive();
      return;
    }
    await renderRoute();
  } catch (error) {
    if (serverRendered) {
      $$("[data-link]").forEach((link) => link.removeAttribute("data-link"));
      return;
    }
    $("#app").innerHTML = `
      <div class="not-found"><span>!</span><h1>研究服务暂时不可用</h1>
      <p>请确认 API 服务已运行在 ${API}。</p></div>
    `;
  }
}

document.addEventListener("DOMContentLoaded", init);
