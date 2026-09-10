"""Render the public home from published article summaries, without JavaScript."""

from __future__ import annotations

import html
from urllib.parse import quote


HOME_TITLE = "Aperture Intelligence · 面向 AI 时代的技术与商业研究"
HOME_DESCRIPTION = "Aperture Intelligence 提供 AI、Agent、云计算、电商媒体与金融市场的深度研究。"


def text(value: object) -> str:
    return html.escape(str(value), quote=True)


def article_path(article: dict) -> str:
    return "/article/" + quote(str(article["slug"]), safe="")


def story_card(article: dict, *, style: str = "") -> str:
    card_class = "compact-story" if style == "compact" else f"story-card {style}"
    return f"""
<a class="{card_class}" href="{article_path(article)}" data-link>
  <div class="story-visual {text(article['hero_style'])}">
    <span class="visual-label">{text(article['category_eyebrow'])}</span>
  </div>
  <div class="story-body">
    <div class="story-meta"><span class="story-category">{text(article['category_name'])}</span>
      <i></i><span>{int(article['read_minutes'])} 分钟阅读</span></div>
    <h3>{text(article['title'])}</h3>
    <p>{text(article['dek'])}</p>
    <div class="story-footer"><span>{text(article['author'])}</span>
      <time datetime="{text(article['published_at'])}">{text(str(article['published_at'])[:10])}</time>
      <svg><use href="#icon-arrow"></use></svg></div>
  </div>
</a>"""


def render_home(articles: list[dict], categories: list[dict], base_url: str) -> tuple[str, list[dict]]:
    hero = articles[0] if articles else None
    remaining = articles[1:]
    summary = hero["summary"] if hero else "基于可追溯证据，持续研究 AI、Agent、云计算、电商媒体与金融市场。"
    hero_details = ""
    hero_visual = ""
    selected = ""
    if hero:
        hero_details = f"""
<a class="hero-cta" href="{article_path(hero)}" data-link>阅读首席研究 <svg><use href="#icon-arrow"></use></svg></a>
<div class="hero-meta"><span>由 <b>{text(hero['author'])}</b> 撰写</span><i></i>
  <time datetime="{text(hero['published_at'])}">{text(str(hero['published_at'])[:10])}</time></div>"""
        hero_visual = f"""
<div class="hero-intelligence" aria-hidden="true"><div class="signal-orbit">
  <div class="signal-core"><strong>{int(hero['source_count'])}</strong><span>SOURCES</span></div>
  <div class="signal-node one">研究领域<b>{text(hero['category_name'])}</b></div>
  <div class="signal-node two">阅读时长<b>{int(hero['read_minutes'])} 分钟</b></div>
  <div class="signal-node three">内容更新<b>{text(str(hero['updated_at'])[:10])}</b></div>
  <i class="signal-dot a"></i><i class="signal-dot b"></i><i class="signal-dot c"></i>
</div></div>"""
        side_stories = "".join(story_card(item, style="compact") for item in remaining[:2])
        selected = f"""
<section class="section"><div class="section-heading"><div>
  <p class="section-eyebrow">EDITOR'S SELECTION</p><h2>值得关注的核心判断</h2>
  <p>从复杂信号中提炼影响技术决策与商业价值的变量。</p>
</div></div><div class="featured-grid">{story_card(hero, style='large')}
  <div class="side-stories">{side_stories}</div></div></section>"""
    category_cards = "".join(
        f"""<a class="category-item" href="/category/{quote(str(category['slug']), safe='')}" data-link>
<span class="category-index">{index:02d}</span><h3>{text(category['name'])}</h3>
<p>{text(category['description'])}</p><span>{int(category['article_count'])} 篇研究
<svg><use href="#icon-arrow"></use></svg></span></a>"""
        for index, category in enumerate(categories, 1)
    )
    latest = "".join(story_card(item) for item in remaining[2:])
    latest_section = (
        f"""<section class="section"><div class="section-heading"><div>
<p class="section-eyebrow">LATEST RESEARCH</p><h2>最新分析</h2>
<p>持续追踪最新发布、行业变化与可操作的研究框架；更多文章可按研究领域浏览。</p>
</div></div><div class="latest-grid">{latest}</div></section>""" if latest else ""
    )
    empty = '<section class="section"><p class="empty-state">暂无已发布研究。</p></section>' if not articles else ""
    main_html = f"""
<section class="home-hero"><div class="hero-inner"><div class="hero-copy">
  <p class="hero-eyebrow">INDEPENDENT TECHNOLOGY INTELLIGENCE</p>
  <h1>理解 AI 时代的<br /><em>关键变量</em></h1>
  <p class="hero-summary">{text(summary)}</p>{hero_details}
</div>{hero_visual}</div></section>
{selected}{empty}
<section class="category-band"><div class="category-band-inner"><div class="section-heading"><div>
  <p class="section-eyebrow">RESEARCH COVERAGE</p><h2>五个研究领域，一套证据标准</h2>
  <p>覆盖技术基础设施、产业采用与资本市场的完整传导链。</p>
</div></div><div class="category-list">{category_cards}</div></div></section>
{latest_section}
<section class="section"><div class="method-grid"><div class="method-intro">
  <p class="section-eyebrow">WHY APERTURE</p><h2>为人类判断，也为机器理解</h2>
  <p>每篇研究都提供完整分析、结构化声明和可追溯来源，便于读者核实事实与判断。</p>
  <a class="section-link" href="/methodology" data-link>了解研究方法 <svg><use href="#icon-arrow"></use></svg></a>
</div><div class="method-list">
  <div class="method-item"><span>01</span><div><h3>来源分级与交叉验证</h3><p>区分官方文档、监管数据、行业研究与二手报道，核对关键结论的证据。</p></div></div>
  <div class="method-item"><span>02</span><div><h3>声明到证据的明确映射</h3><p>记录事实的时间、口径与来源，保留推断的适用范围与不确定性。</p></div></div>
  <div class="method-item"><span>03</span><div><h3>审核与持续更新</h3><p>新稿通过证据审核及全文去重后发布，发现错误时按纠错政策处理。</p></div></div>
</div></div></section>"""
    schemas = [{
        "@context": "https://schema.org", "@type": "Organization",
        "@id": base_url + "/#organization", "name": "Aperture Intelligence",
        "url": base_url + "/", "publishingPrinciples": base_url + "/editorial-policy",
        "correctionsPolicy": base_url + "/corrections",
    }, {
        "@context": "https://schema.org", "@type": "WebSite",
        "@id": base_url + "/#website", "name": "Aperture Intelligence", "url": base_url + "/",
        "inLanguage": "zh-CN", "publisher": {"@id": base_url + "/#organization"},
    }, {
        "@context": "https://schema.org", "@type": "CollectionPage",
        "@id": base_url + "/#homepage", "name": HOME_TITLE, "description": HOME_DESCRIPTION,
        "url": base_url + "/", "inLanguage": "zh-CN",
        "isPartOf": {"@id": base_url + "/#website"},
        "mainEntity": {"@type": "ItemList", "itemListElement": [
            {"@type": "ListItem", "position": index,
             "url": base_url + article_path(item), "name": item["title"]}
            for index, item in enumerate(articles, 1)
        ]},
    }]
    return main_html, schemas
