# 旧文章网址如何保留：本站 301、canonical 与 sitemap 修复实录

去重后旧文章变成404怎么办？本站13个旧网址的处理案例，说明如何审核内容对应关系、持久保存301，并验证canonical、sitemap和浏览器导航。

## 文章去重之后，旧链接仍然有人访问

清理重复内容时，稿件数量可能减少，但搜索引擎、收藏夹和外站文章中的旧链接不会跟着消失。Aperture在2026年9月14日处理了一批已经被发现的旧地址：13个地址中，11个建立永久重定向，2篇独立文章在原址恢复。当时原有22个公开地址和首次发布时间均保留，修复后公开文章为24篇。[S1]

这个案例的目标是保留旧入口的内容意义：重复稿由相关、覆盖充分的文章承接，独立内容补审后恢复。它没有证明搜索排名已经提高，也没有承诺搜索引擎会在特定日期完成收录。本文复盘9月14日那一批处理，后续新发现的旧链接需要另行审核，不把一次修复视为永久清零。

## 先判断内容关系，再选择网址动作

一个网址返回404，仅说明当前没有可用资源，不能单独说明应当恢复哪篇稿件。处理前应检查旧稿正文、历史发布状态、来源证据和现有目录。只有确认已有页面覆盖其核心问题与有效内容时，才适合将旧网址永久指向该页。

Google 的迁移指南要求准备旧、新网址映射，并指出不要把大量旧网址重定向到无关目的地，例如首页，这可能被当作 soft 404。这个原则同样能指导文章合并：读者从具体文章链接进入，承接页面应当回答原来的问题。[S3]

本站当次先做全文比较，再把需要保留的有效信息补入承接文章并复核。无法由现有内容替代的欧洲央行历史利率稿和纳指历史观测稿，经来源与引用修订后恢复原址。相关处理被记录为审核结果，而不是只在边缘层写一条临时规则。[S1]

## 301、canonical 与 sitemap 分别解决什么

301 是服务器告诉客户端资源已经永久迁移的HTTP响应，目标写在Location头中。MDN同时提示，不同请求方法在跟随301时可能有改为GET的行为，因此这个案例针对公开内容的GET和HEAD入口；支付或写操作不能照搬一个普通网页跳转规则。[S5]

canonical 是当前页面表达首选规范网址的信号，并不会替浏览器把一个已经404的旧地址变成正常页面。Google 将永久重定向和 rel=canonical 列为较强的规范化信号，将 sitemap 包含关系列为较弱信号；这些手段可以相互配合，但最终搜索引擎仍可能根据其他信号选择规范页。[S4]

本站让旧网页301到具体承接文章，承接文章返回200并指向自身canonical，sitemap只列保留的公开规范页。Google 的 sitemap 指南建议列出希望在搜索结果出现的规范网址；提交地图是发现方式，不能保证所有条目被抓取或收录。[S1][S6]

## 把直接访问、机器接口和站内导航分开验证

只在浏览器中看到一个正常标题，无法证明服务器真正发出了301。前端应用可能只换了内容却没有更新地址，CDN也可能继续返回旧缓存。先检查HTTP状态与Location，再检查最终页内容和canonical，最后验证JavaScript开启后的站内导航。[S1]

下面是当次实际映射中的一个示例。第一条命令观察旧网址的状态和Location；第二条跟随跳转确认终点；第三条查看目标HTML里的规范地址。命令面向公开只读页面，不包含任何后台凭据。[S1]

```
curl -I 'https://aperture.zhangwangshu.com/article/agent-research-20260903-2103-396'
curl -L -o /dev/null -w '%{http_code} %{url_effective}\n' \
  'https://aperture.zhangwangshu.com/article/agent-research-20260903-2103-396'
curl -s 'https://aperture.zhangwangshu.com/article/agent-research-20260905-0003-599' \
  | rg 'rel="canonical"'
```

## 当次修复的验收结果，以及它没有证明的事

9月14日的验证记录显示，11个旧网址的GET和HEAD返回301，24篇公开文章返回200并有单一H1及正确canonical；文章sitemap包含24篇，未包含重定向来源。浏览器还验证了脚本开启或关闭、旧链接站内导航、地址更新及后退前进。[S1]

这组结果证明当时的路径与渲染行为符合修复目标，不证明全部文章已经收录。Google Search Console中的页面索引状态、查询展示及点击需要另行查看；日志中的Googlebot或Bingbot访问也只是抓取证据。

维护上可以把“最近仍被请求的404旧地址”做成固定检查清单。每发现一个入口，先回到正文覆盖和发布历史，再决定恢复、相关301或保持真实404。这样网页数量、搜索可用性和内容去重就不再相互冲突：合并的是重复表达，保留的是读者仍然需要的入口。

## 来源

[S1] [本站2026-09-14旧网址修复记录](https://github.com/milan9527/geo/blob/34b646e1f34d74c740020df4d9f59dd770449646/reports/legacy-url-repair-2026-09-14.md)

[S2] [持久文章重定向及数据库保护实现](https://github.com/milan9527/geo/blob/34b646e1f34d74c740020df4d9f59dd770449646/backend/article_redirects.py)

[S3] [Site moves with URL changes](https://developers.google.com/search/docs/crawling-indexing/site-move-with-url-changes)

[S4] [How to specify a canonical URL](https://developers.google.com/search/docs/crawling-indexing/consolidate-duplicate-urls)

[S5] [301 Moved Permanently](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Status/301)

[S6] [Build and submit a sitemap](https://developers.google.com/search/docs/crawling-indexing/sitemaps/build-sitemap)

本站原文与完整复现材料：https://aperture.zhangwangshu.com/article/preserve-article-urls-301-canonical-sitemap?utm_source=juejin&utm_medium=community&utm_campaign=permanent_urls
