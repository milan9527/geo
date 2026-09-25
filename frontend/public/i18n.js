/* Locale follows the public URL; the console remembers an explicit preference. */
(() => {
  const admin = document.documentElement.dataset.console === "true";
  const strip = path => path.replace(/^\/zh(?=\/|$)/, "") || "/";
  const lang = admin ? (localStorage.getItem("aperture-admin-language") === "zh" ? "zh" : "en") : (/^\/zh(?:\/|$)/.test(location.pathname) ? "zh" : "en");
  const dictionary = window.APERTURE_EN || {};
  const keys = Object.keys(dictionary).sort((a, b) => b.length - a.length);
  const pattern = new RegExp(keys.map(key => key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|"), "g");
  const text = value => {
    if (lang === "zh" || typeof value !== "string" || !/[\u3400-\u9fff]/.test(value)) return value;
    const key = value.trim();
    if (dictionary[key]) return value.replace(key, dictionary[key]);
    const translated = value.replace(pattern, match => dictionary[match]);
    return /[\u3400-\u9fff]/.test(translated) ? value : translated;
  };
  const path = (value, locale = lang) => (locale === "zh" ? "/zh" : "") + strip(value);
  const api = value => value + (value.includes("?") ? "&" : "?") + "lang=" + lang;
  const protectedNode = node => node.parentElement?.closest("script,style,code,pre,[data-language-switch],[data-language-picker],[data-original-language]");
  let observer;
  function apply(root = document.body) {
    if (!root) return;
    observer?.disconnect();
    if (admin) {
      const picker = document.querySelector("[data-language-picker]");
      const target = document.querySelector("#adminShell:not([hidden]) .top-actions") || document.body;
      if (picker && picker.parentElement !== target) target.prepend(picker);
    }
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      const node = walker.currentNode;
      if (!protectedNode(node)) {
        const translated = text(node.nodeValue);
        if (translated !== node.nodeValue) node.nodeValue = translated;
      }
    }
    for (const el of [root, ...root.querySelectorAll("[title],[placeholder],[aria-label],[alt],a[href]")]) {
      if (!el.closest || el.closest("[data-language-switch],[data-language-picker],[data-original-language]")) continue;
      for (const attr of ["title", "placeholder", "aria-label", "alt"]) {
        if (el.hasAttribute(attr)) el.setAttribute(attr, text(el.getAttribute(attr)));
      }
      if (!admin && el.matches("a[href]") && !el.hasAttribute("hreflang")) {
        const url = new URL(el.getAttribute("href"), location.href);
        const unlocalized = strip(url.pathname);
        if (url.origin === location.origin && (/^\/(article|category|authors)\//.test(unlocalized) || ["/", "/about", "/methodology", "/corrections", "/editorial-policy", "/feed.xml"].includes(unlocalized))) {
          el.setAttribute("href", path(unlocalized) + url.search + url.hash);
        }
      }
    }
    document.title = text(document.title);
    document.querySelectorAll("[data-language-switch]").forEach(link => {
      link.href = path(location.pathname, lang === "en" ? "zh" : "en") + location.search + location.hash;
      link.textContent = lang === "en" ? "中文" : "English";
    });
    observer?.observe(document.body, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ["title", "placeholder", "aria-label"] });
  }
  window.apertureI18n = { lang, locale: lang === "en" ? "en-US" : "zh-CN", text, path, strip, api, apply };
  document.documentElement.lang = lang === "en" ? "en" : "zh-CN";
  const nativeConfirm = window.confirm.bind(window);
  window.confirm = message => nativeConfirm(text(message));
  document.addEventListener("DOMContentLoaded", () => {
    if (admin) {
      const picker = document.createElement("select");
      picker.dataset.languagePicker = "true";
      picker.className = "language-picker";
      picker.setAttribute("aria-label", lang === "en" ? "Interface language" : "界面语言");
      picker.innerHTML = '<option value="en">English</option><option value="zh">中文</option>';
      picker.value = lang;
      picker.addEventListener("change", () => { localStorage.setItem("aperture-admin-language", picker.value); location.reload(); });
      document.body.append(picker);
    } else if (!document.querySelector("[data-language-switch]")) {
      const link = document.createElement("a");
      link.dataset.languageSwitch = "true";
      link.className = "language-switch";
      document.querySelector(".header-actions")?.prepend(link);
    }
    observer = new MutationObserver(() => apply());
    apply();
    document.documentElement.classList.add("locale-ready");
  });
})();
