/* Anonymous, short-lived session attribution. No IP, email or full referrer. */
(() => {
  const timeout = 30 * 60 * 1000;
  let memorySession;
  let currentPage;
  let timer;
  const disabled = () => {
    const test = new URLSearchParams(location.search).get("aperture_test") === "1";
    try {
      if (test) sessionStorage.setItem("aperture-test", "1");
      if (sessionStorage.getItem("aperture-test") === "1") return true;
    } catch {}
    return test || navigator.webdriver
      || /aperture|bot|spider|crawler|headless|lighthouse/i.test(navigator.userAgent);
  };
  function read(storage, key) {
    try { return JSON.parse(storage.getItem(key)); } catch { return null; }
  }
  function write(storage, key, value) {
    try { storage.setItem(key, JSON.stringify(value)); } catch {}
  }
  function session() {
    const now = Date.now();
    let stored;
    try { stored = read(sessionStorage, "aperture-growth"); } catch {}
    let item = stored || memorySession;
    if (!item || now - item.lastActive > timeout) {
      let previous;
      try { previous = read(localStorage, "aperture-last-visit"); } catch {}
      const params = new URLSearchParams(location.search);
      let referrerHost = "";
      try { referrerHost = new URL(document.referrer).hostname; } catch {}
      if (referrerHost === location.hostname) referrerHost = "";
      item = {
        sessionId: crypto.randomUUID(),
        source: params.get("utm_source") || referrerHost || "direct_or_unknown",
        medium: params.get("utm_medium") || (referrerHost ? "referral" : "none"),
        campaign: params.get("utm_campaign") || "",
        referrerHost,
        returning: Boolean(previous && now - previous > timeout),
      };
    }
    item.lastActive = now;
    memorySession = item;
    try { write(sessionStorage, "aperture-growth", item); write(localStorage, "aperture-last-visit", now); } catch {}
    return item;
  }
  function track(eventType, extra = {}) {
    if (disabled() || !currentPage) return;
    const payload = {
      eventType,
      articleSlug: currentPage.slug || undefined,
      metadata: { ...session(), eventId: crypto.randomUUID(), path: currentPage.path, ...extra },
    };
    fetch("/api/v1/track", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload), keepalive: true,
    }).catch(() => {});
  }
  function stopPage() {
    clearInterval(timer);
    currentPage = null;
  }
  function startPage() {
    stopPage();
    if (disabled() || document.querySelector(".not-found")) return;
    const path = location.pathname.replace(/\/+$/, "") || "/";
    currentPage = { path, slug: document.querySelector(".article-page")
      ? path.match(/^\/article\/([^/]+)$/)?.[1] : null };
    track("page_view");
    if (!currentPage.slug) return;
    let activeSeconds = 0;
    let lastTick = performance.now();
    timer = setInterval(() => {
      const now = performance.now();
      const elapsed = Math.min(2, (now - lastTick) / 1000);
      lastTick = now;
      if (document.visibilityState !== "visible") return;
      activeSeconds += elapsed;
      const content = document.querySelector(".article-content");
      if (!content) return;
      const rect = content.getBoundingClientRect();
      const progress = (innerHeight - rect.top) / Math.max(1, rect.height);
      if (activeSeconds >= 30 && progress >= 0.5) {
        track("engaged_read");
        clearInterval(timer);
      }
    }, 1000);
  }
  document.addEventListener("click", (event) => {
    const link = event.target.closest("a");
    if (link?.getAttribute("href") === "/feed.xml") track("rss_click");
    if (link?.closest(".related-band") && currentPage?.slug) track("related_click");
    if (event.target.closest("[data-share-article]")) {
      const canonical = document.querySelector('link[rel="canonical"]')?.href || location.origin + location.pathname;
      navigator.clipboard.writeText(canonical).then(() => {
        const toast = document.querySelector("#toast");
        if (toast) {
          toast.textContent = "文章链接已复制";
          toast.classList.add("show");
          setTimeout(() => toast.classList.remove("show"), 2800);
        }
      }).catch(() => {});
    }
  });
  window.apertureGrowth = { startPage, stopPage, disabled };
})();
