# 引流方案实施记录 · 2026-09-20

已完成本站内容、旧网址和统计功能上线，公开文章由32篇增至35篇。站外稿件已备好；掘金/知乎实际发布和Google/Bing搜索表现数据仍待账号访问。

## 已发布的原创案例

- [Aurora Data API 超过 1 MiB 导致后台 502：本站复现与分块读取修复](https://aperture.zhangwangshu.com/article/aurora-data-api-1mb-502-chunked-read)：质量复审95分。

- [旧文章网址如何保留：本站 301、canonical 与 sitemap 修复实录](https://aperture.zhangwangshu.com/article/preserve-article-urls-301-canonical-sitemap)：质量复审94分。

两篇均保存了来源证据，并与发布后的完整公开目录进行67组全文比较；比较置信度满足现行门槛，未放宽去重或质量规则。新文章的外部引用计数从0开始，来源数量不当作外部引用次数。

## 旧网址与现有页面

9个遗留地址中，8个建立相关301，1篇独立文章在原址恢复。为避免承接页面遗漏旧稿的有效内容，文章58、117、223经补充和复审后继续使用原网址。文章142在原址恢复。此批修订与最终公开目录完成122组全文比较，8个跳转另有覆盖审核。

| 旧文章 | 承接文章 |
| --- | --- |
| [cloud-research-20260903-0906-151](https://aperture.zhangwangshu.com/article/cloud-research-20260903-0906-151) | [阿里云安全更新与记忆服务介绍：企业 AI 如何划清状态、推理和权限边界](https://aperture.zhangwangshu.com/article/cloud-research-20260903-0909-170) |
| [commerce-research-20260903-0927-212](https://aperture.zhangwangshu.com/article/commerce-research-20260903-0927-212) | [Stripe讨论Agent买方定价准备，商业影响取决于真实交易](https://aperture.zhangwangshu.com/article/commerce-research-20260905-0004-600) |
| [agent-research-20260903-0930-220](https://aperture.zhangwangshu.com/article/agent-research-20260903-0930-220) | [企业Agent如何走向受控执行：Anthropic“脑手解耦”、AWS平台与OpenAI案例](https://aperture.zhangwangshu.com/article/agent-research-20260905-0003-599) |
| [agent-research-20260903-2052-395](https://aperture.zhangwangshu.com/article/agent-research-20260903-2052-395) | [企业Agent如何走向受控执行：Anthropic“脑手解耦”、AWS平台与OpenAI案例](https://aperture.zhangwangshu.com/article/agent-research-20260905-0003-599) |
| [agent-research-20260904-0736-491](https://aperture.zhangwangshu.com/article/agent-research-20260904-0736-491) | [企业Agent如何走向受控执行：Anthropic“脑手解耦”、AWS平台与OpenAI案例](https://aperture.zhangwangshu.com/article/agent-research-20260905-0003-599) |
| [agent-research-20260904-1943-573](https://aperture.zhangwangshu.com/article/agent-research-20260904-1943-573) | [企业Agent如何走向受控执行：Anthropic“脑手解耦”、AWS平台与OpenAI案例](https://aperture.zhangwangshu.com/article/agent-research-20260905-0003-599) |
| [commerce-research-20260906-1204-843](https://aperture.zhangwangshu.com/article/commerce-research-20260906-1204-843) | [Stripe讨论Agent买方定价准备，商业影响取决于真实交易](https://aperture.zhangwangshu.com/article/commerce-research-20260905-0004-600) |
| [finance-research-20260903-0907-167](https://aperture.zhangwangshu.com/article/finance-research-20260903-0907-167) | [AI资本开支与科技股回报：融资之外，还要看收入和现金](https://aperture.zhangwangshu.com/article/finance-research-20260917-0212-1232) |

恢复文章：[AWS、百度与训练式编译案例呈现AI任务的云端组织与本地复用方式](https://aperture.zhangwangshu.com/article/ai-research-20260906-0610-804)。

原有32篇的固定网址与首次发布时间均保留；其中29篇的正文与来源哈希完全一致。数据库中没有删除或撤回任何原有公开文章。现有定时发布继续执行质量审核、公开目录去重及永久链接保护。

## 读者与渠道统计

管理后台新增“读者与引流渠道”，记录匿名浏览会话、参与阅读、回访、RSS点击和相关阅读点击，并按来源、媒介和推广活动汇总。会话在30分钟无活动后重新建立；参与阅读要求前台停留至少30秒并到达正文一半。RSS点击不等于成功订阅。

UTM或外部来源域名在同一会话内保留；不保存完整来源网址、访客IP或邮箱。采用浏览器上报，因此脚本拦截、存储限制及未识别自动化仍会影响估计，不能等同于经过身份确认的真人人数。历史CloudFront估计不回填改写，后台已注明可能包含脚本。

文章末尾已增加RSS、分享链接，保留相关阅读。直接打开页面与站内导航均支持代码块和新增入口。诊断User-Agent与浏览器测试模式不会计入新读者数据；测试模式在当前标签页导航后继续生效。采集器也过滤已识别诊断请求。

301保留查询参数。由于CloudFront内容缓存键不含查询字符串，301响应使用no-store，避免把某个来源的UTM带给另一位访客；永久重定向语义仍由301表达。

## 验证与部署

- 38项Python测试通过，另完成采集器检查和浏览器端到端验证。

- 35篇文章全部HTTP 200，单个H1、自指canonical且无noindex；文章sitemap完整覆盖35篇。

- 19个新旧映射完成152次线上检查，覆盖网页、文章JSON、开放及付费Agent旧入口和两个不同推广参数；均正确301，未请求付费目标结算。

- 后台真实登录、初始化数据、新统计区、研究列表、刷新恢复和退出通过；临时测试管理员与会话已删除。

- 手机端无横向溢出，直接加载与站内导航均正常；生产验证前后新读者事件都是0，未用测试访问制造增长。

- API任务定义33、公开站及后台静态资源已部署；最终线上静态文件与本地逐字节一致。流量采集Lambda已更新。API镜像扫描完成，无报告漏洞。

- IndexNow接受19个网址更新，返回HTTP 200；已刷新发现入口缓存。接受通知不代表已经收录，也未代替Google Search Console后台操作。

## 站外传播与下一次复盘

已准备[两份掘金稿、两份知乎答疑稿及分发安排](../content/distribution/2026-09-20/README.md)，每篇带独立UTM。GitHub README加入两篇案例入口。外站稿件需要在相关主题下发布，不能批量投放到无关问题。

本次未取得掘金/知乎登录会话或发布接口，未向这些平台发帖，也未创建外站自动发布任务。Google/Bing站点表现数据同样缺少账号访问，因此没有报告展示量、搜索点击或新增索引数量。

建议在9月27日和10月4日复盘来源会话、参与阅读、回访及RSS点击，再结合搜索查询数据决定下一篇选题与传播渠道。这个日期是执行计划，不是已经创建的提醒或保证。

详细审核、部署和验证摘要见[同名JSON](growth-launch-2026-09-20.json)。原始审核材料保存在不提交Git的`.review-runs/2026-09-20-implementation/`。
