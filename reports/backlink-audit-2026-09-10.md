# Bing 外链提示检查（2026-09-10）

Bing Webmaster Tools 的 “Your site lacks inbound links from high-quality domains”
是对站点外部引用的建议。报告没有列出具体错误页面；此提示本身不表示页面禁止抓取或
禁止索引，也不能仅靠修改 sitemap、站内链接或再次提交 IndexNow 消除。

## 公开入口检查

| 检查对象 | 实际结果 |
| --- | --- |
| GitHub 仓库 `milan9527/geo` | 公开，默认分支为 `main` |
| GitHub 仓库 Website 字段 | API 返回 `homepage: null` |
| 远程 README | 网站地址写在行内代码中 |
| GitHub 仓库渲染页面 | 未发现指向 `aperture.zhangwangshu.com` 的 `<a href>` |
| 网站首页、`/methodology` | HTTP 200，canonical 分别指向对应正式网址 |
| `/feed.xml` | HTTP 200，Content-Type 为 `application/rss+xml` |

GitHub 检查使用公开仓库 API、远程 `main` 的原始 README 和仓库 HTML。
本次没有登录 Bing Webmaster Tools，也没有取得其外链导出数据，因此不能据此断言
网站没有其他外链，或判断其全部来源域名的质量。

## 已准备的修改

本地 README 顶部增加网站、研究方法和 RSS 的 Markdown 链接，并将部署入口中的
公开站地址改为可点击链接。上述三个目标均已验证可访问。引用指引建议使用文章固定网址。

这些修改需要推送到 GitHub 后才会在仓库页面生效。当前环境缺少 GitHub 写入凭据，
尚未推送，也未修改远程仓库的 Website 字段。后者建议填写
`https://aperture.zhangwangshu.com/`。

GitHub 链接方便读者发现网站，但不能保证 Bing 将其视为高质量推荐、给予排名权重，
或撤销此提示。

## 后续外链工作

1. 在真实维护、与本项目相关的个人主页或项目介绍中加入网站链接。
2. 持续发布有独立价值的研究、可复现比较或数据，并保留文章网址，让相关作者有理由引用。
3. 对已有自然提及但未附链接的页面，可由站点所有者联系作者请求补充准确来源链接；
   本次没有代发联系消息。
4. 在 Bing 的 Backlinks 报告查看引用域名、目标页面及链接增长，并与主题和规模相近的
   网站比较。搜索引擎重新抓取外部页面后才可能反映变化，不承诺处理时间。

不建议购买链接、批量注册目录或发布无关评论。来源的相关性和真实引用价值比单纯增加
域名数量更重要。新文章继续经过现有质量审核与去重流程；自动发布本身不会自动获得外链。
