const I18N = window.apertureI18n;
const API = location.origin;
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const text = (en, zh) => I18N.lang === "zh" ? zh : en;
const escapeHtml = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
let navigationId = 0;
let searchId = 0;
let navigationController;

async function api(path, options = {}) {
  const response = await fetch(API + I18N.api(path), {
    ...options, headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (!response.ok) {
    const error = new Error(`API ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.add("show");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => toast.classList.remove("show"), 2800);
}

function updateNavActive() {
  const route = I18N.strip(location.pathname).replace(/\/+$/, "") || "/";
  $$("#primaryNav a").forEach(link => {
    const active = I18N.strip(new URL(link.href).pathname).replace(/\/+$/, "") === route;
    link.classList.toggle("active", active);
    if (active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
}

function syncHead(page) {
  document.title = page.title;
  const selector = 'meta[name="description"],meta[name="robots"],meta[property^="og:"],link[rel="canonical"],link[rel="alternate"],script[data-page-schema]';
  $$(selector).forEach(node => node.remove());
  $("#pageSchema")?.remove();
  $$(selector, page).forEach(node => document.head.append(node.cloneNode(true)));
}

function renderLoadError() {
  $("#app").innerHTML = `<section class="not-found" role="alert"><h1>${text("This page is temporarily unavailable", "页面暂时无法加载")}</h1><p>${text("Please try again. Your current address has been preserved.", "请重试，当前网址已保留。")}</p><button type="button" data-retry-page>${text("Retry", "重试")}</button></section>`;
}

async function renderRoute() {
  const requestId = ++navigationId;
  navigationController?.abort();
  navigationController = new AbortController();
  window.apertureGrowth?.stopPage();
  $("#primaryNav").classList.remove("open");
  $("#app").setAttribute("aria-busy", "true");
  try {
    // Use the complete, crawlable document for clicks as well as direct visits.
    // This keeps category counts, translations, redirects and metadata identical.
    const response = await fetch(location.pathname + location.search, {
      headers: { Accept: "text/html" }, signal: navigationController.signal,
    });
    if (!response.ok && response.status !== 404) throw new Error(`Page ${response.status}`);
    const page = new DOMParser().parseFromString(await response.text(), "text/html");
    if (requestId !== navigationId) return;
    const main = $("#app", page);
    if (!main) {
      if (response.status === 404) { location.reload(); return; }
      throw new Error("Missing page content");
    }
    const destination = new URL(response.url);
    if (response.redirected && destination.origin === location.origin) {
      history.replaceState({}, "", destination.pathname + destination.search + location.hash);
    }
    syncHead(page);
    $("#app").innerHTML = main.innerHTML;
    const nav = $("#primaryNav", page);
    if (nav) $("#primaryNav").innerHTML = nav.innerHTML;
    I18N.apply();
    updateNavActive();
    window.scrollTo(0, 0);
    if (location.hash) {
      let fragment = location.hash.slice(1);
      try { fragment = decodeURIComponent(fragment); } catch { /* Keep malformed fragments literal. */ }
      document.getElementById(fragment)?.scrollIntoView();
    }
    $("#app").focus({ preventScroll: true });
    window.apertureGrowth?.startPage();
  } catch (error) {
    if (requestId === navigationId && error.name !== "AbortError") renderLoadError();
  } finally {
    if (requestId === navigationId) $("#app").removeAttribute("aria-busy");
  }
}

function navigate(href) {
  history.pushState({}, "", I18N.path(href));
  return renderRoute();
}

async function runSearch(term) {
  const requestId = ++searchId;
  const query = term.trim();
  if (!query) { $("#searchResults").replaceChildren(); $("#searchResults").removeAttribute("aria-busy"); return; }
  $("#searchResults").setAttribute("aria-busy", "true");
  try {
    const results = await api(`/api/v1/search?q=${encodeURIComponent(query)}`);
    if (requestId !== searchId) return;
    $("#searchResults").innerHTML = results.length
      ? results.map(item => `<a class="search-result" href="${I18N.path("/article/" + encodeURIComponent(item.slug))}" data-link><div><span>${escapeHtml(item.category.name)}</span><h3>${escapeHtml(item.title)}</h3></div><small>${Number(item.readMinutes)} ${text("min", "分钟")}</small></a>`).join("")
      : `<div class="search-empty">${text("No matching research found.", "没有找到匹配的研究内容。")}</div>`;
  } catch {
    if (requestId === searchId) $("#searchResults").innerHTML = `<div class="search-empty" role="alert">${text("Search is temporarily unavailable. Please try again.", "搜索暂时不可用，请重试。")}</div>`;
  } finally {
    if (requestId === searchId) $("#searchResults").removeAttribute("aria-busy");
  }
}

function closeSearch() {
  $("#searchOverlay").classList.remove("open");
  $("#searchOverlay").setAttribute("aria-hidden", "true");
}

function track(eventType, articleSlug, metadata) {
  if (window.apertureGrowth?.disabled()) return;
  api("/api/v1/track", {method:"POST", keepalive:true,
    body:JSON.stringify({eventType, articleSlug, metadata})}).catch(() => {});
}

function initEvents() {
  document.addEventListener("click", async event => {
    if (event.target.closest("[data-retry-page]")) { renderRoute(); return; }
    const link = event.target.closest("a[data-link]");
    if (link && link.origin === location.origin && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey && event.button === 0 && (!link.target || link.target === "_self")) {
      event.preventDefault();
      const articleSlug = I18N.strip(link.pathname).match(/^\/article\/([^/]+)/)?.[1];
      track("human_click", articleSlug, {sourcePath:location.pathname, destinationPath:link.pathname});
      closeSearch();
      navigate(link.pathname + link.search + link.hash);
    }
    const machine = event.target.closest("[data-machine-url]");
    if (machine) {
      try {
        await navigator.clipboard.writeText(machine.dataset.machineUrl);
        showToast(text("Agent API link copied", "Agent API 地址已复制"));
        track("machine_link_copy", I18N.strip(location.pathname).match(/^\/article\/([^/]+)/)?.[1], {target:machine.dataset.machineUrl});
      } catch { showToast(text("Could not copy the link. Please try again.", "无法复制链接，请重试。")); }
    }
  });
  window.addEventListener("popstate", renderRoute);
  $("#menuButton").addEventListener("click", () => $("#primaryNav").classList.toggle("open"));
  $("#searchButton").addEventListener("click", () => {
    $("#searchOverlay").classList.add("open");
    $("#searchOverlay").setAttribute("aria-hidden", "false");
    $("#searchInput").focus();
  });
  $("#closeSearch").addEventListener("click", closeSearch);
  $("#searchOverlay").addEventListener("click", event => { if (event.target === $("#searchOverlay")) closeSearch(); });
  document.addEventListener("keydown", event => {
    if (event.key === "Escape") closeSearch();
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") { event.preventDefault(); $("#searchButton").click(); }
  });
  let timer;
  $("#searchInput").addEventListener("input", event => {
    clearTimeout(timer);
    // Invalidate an older response immediately, before this debounce expires.
    searchId++;
    timer = setTimeout(() => runSearch(event.target.value), 220);
  });
  $$("[data-search-term]").forEach(button => button.addEventListener("click", () => {
    clearTimeout(timer);
    const term = I18N.lang === "en" ? I18N.text(button.dataset.searchTerm) : button.dataset.searchTerm;
    $("#searchInput").value = term;
    runSearch(term);
  }));
}

function init() {
  initEvents();
  if (document.body.dataset.ssr === "true") {
    I18N.apply();
    updateNavActive();
    window.apertureGrowth?.startPage();
  } else renderRoute();
}

document.addEventListener("DOMContentLoaded", init);
