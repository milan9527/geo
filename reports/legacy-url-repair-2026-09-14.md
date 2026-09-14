# 旧网址修复记录 · 2026-09-14

本次处理流量报告中确认的 13 个旧文章网址：11 个返回永久重定向（301），2 个在原网址恢复为正常文章（200）。提交时间为 2026-09-14 03:23:25 UTC。

原有 22 个已发布网址和首次发布时间全部保留。19 篇正文及来源保持原样；另外 3 篇经审核补入旧稿中的有效内容，继续使用原网址。现在有 24 篇已发布文章，未删除任何稿件。

## 恢复原网址

- [欧洲央行利率的三个历史节点：两降一升与科技投资传导](https://aperture.zhangwangshu.com/article/finance-research-20260904-1205-524)：质量审核 95 分，原网址返回 200。
- [2026年9月1日纳指回落271.12点、约1.03%：历史观测与解释边界[S4]](https://aperture.zhangwangshu.com/article/finance-research-20260906-0026-767)：质量审核 92 分，原网址返回 200。

欧洲央行稿删除无效来源，补充利率工具定义与传导机制，明确“两降一升”仅指所选历史节点。纳指稿纠正自营交易标识译法，并依据 CFPB 完整公告修正实施时间。原 FINRA 稿经全文比较，其主要证据与结论已被修订后的纳指稿覆盖，改为 301，避免恢复后再次重复。

## 永久重定向

| 旧网址 | 承接文章 | 全文对应置信度 |
| --- | --- | --- |
| [cloud-research-20260903-0908-165](https://aperture.zhangwangshu.com/article/cloud-research-20260903-0908-165) | [Snowflake多代理研究、Google Cloud代理产品与AWS混合云编排：企业AI架构如何取舍](https://aperture.zhangwangshu.com/article/cloud-research-20260909-0008-1181) | 90% |
| [commerce-research-20260903-1050-245](https://aperture.zhangwangshu.com/article/commerce-research-20260903-1050-245) | [Amazon公开列出Alexa目标价自动购买功能，Shopify披露新店首单数据](https://aperture.zhangwangshu.com/article/commerce-research-20260908-0006-1090) | 90% |
| [agent-research-20260903-2103-396](https://aperture.zhangwangshu.com/article/agent-research-20260903-2103-396) | [企业Agent如何走向受控执行：Anthropic“脑手解耦”、AWS平台与OpenAI案例](https://aperture.zhangwangshu.com/article/agent-research-20260905-0003-599) | 96% |
| [ai-research-20260904-0659-488](https://aperture.zhangwangshu.com/article/ai-research-20260904-0659-488) | [企业Agent如何走向受控执行：Anthropic“脑手解耦”、AWS平台与OpenAI案例](https://aperture.zhangwangshu.com/article/agent-research-20260905-0003-599) | 90% |
| [agent-research-20260905-0033-609](https://aperture.zhangwangshu.com/article/agent-research-20260905-0033-609) | [CrewAI、LangChain与LlamaIndex更新工具执行、内容保留和运行标识](https://aperture.zhangwangshu.com/article/agent-research-20260906-0033-769) | 99% |
| [commerce-research-20260906-1808-887](https://aperture.zhangwangshu.com/article/commerce-research-20260906-1808-887) | [eBay扩展AI卖家工具与Facebook商品分发，Salesforce展示代理与CRM连接](https://aperture.zhangwangshu.com/article/commerce-research-20260908-1807-1174) | 98% |
| [agent-research-20260907-0713-975](https://aperture.zhangwangshu.com/article/agent-research-20260907-0713-975) | [Agent执行控制更新：9月8日诊断与身份校验，如何承接工作区、会话和记忆验证](https://aperture.zhangwangshu.com/article/agent-research-20260908-2004-1175) | 96% |
| [ai-research-20260907-1806-1047](https://aperture.zhangwangshu.com/article/ai-research-20260907-1806-1047) | [Meta展示模型项目，百度与百川呈现Agent定位与工具接口区别](https://aperture.zhangwangshu.com/article/ai-research-20260908-1205-1165) | 97% |
| [agent-research-20260908-0003-1085](https://aperture.zhangwangshu.com/article/agent-research-20260908-0003-1085) | [CrewAI、LangChain与LlamaIndex更新工具执行、内容保留和运行标识](https://aperture.zhangwangshu.com/article/agent-research-20260906-0033-769) | 94% |
| [ai-research-20260908-0004-1088](https://aperture.zhangwangshu.com/article/ai-research-20260908-0004-1088) | [Anthropic聚焦长时Agent与设备接口，AWS介绍跨账户模型治理方案](https://aperture.zhangwangshu.com/article/ai-research-20260908-1805-1172) | 94% |
| [finance-research-20260907-0605-965](https://aperture.zhangwangshu.com/article/finance-research-20260907-0605-965) | [2026年9月1日纳指回落271.12点、约1.03%：历史观测与解释边界[S4]](https://aperture.zhangwangshu.com/article/finance-research-20260906-0026-767) | 94% |

所有跳转均为单次 301 到具体文章。网页、文章 JSON 和 Agent 接口使用同一份持久映射；查询参数和末尾斜杠不会造成跳转链。不存在的其他网址仍返回真实 404。

## 审核与发布保护

- 本次上线的 5 篇修订稿评分为 92–95 分，均通过证据、引用、完整性、来源和篇幅要求。
- 修订稿与最终 24 篇文章目录逐篇比较，共 105 组全文比较；11 个跳转另有全文覆盖检查。涉及的比较全部满足至少 0.90 的置信度要求。
- 合并稿保留 Meta/百度/百川产品与接口主线，补回已保存的详细百川协议，压缩与云架构文章重合的通用成本建议；补充内容注明修订与资料时间边界。
- 更新数据前，在同一事务内锁定文章、来源与映射，复查全文哈希和已发布目录。正文或目录有变化就终止提交。审核和保存证据记录在独立研究记录中。
- 数据库禁止删除、撤回或改名已发布文章；已重定向旧稿也不能删除、改网址或重新发布。定时任务排除这些旧稿，新稿继续通过质量审核和全库语义去重后才自动发布。

## 验证和部署

- 49 项本地回归测试通过；真实数据的本地事务演练确认过期审核、重复提交均被拦截。
- 11 个旧网址的 GET/HEAD 均为 301；24 篇已发布文章均返回 200，具有单一 H1 和正确 canonical。
- 首页链接覆盖 24 篇文章；总 sitemap 为 35 个网址，其中 24 篇文章；文章 sitemap 为 24 个网址，不含重定向来源。
- Chromium 验证 JavaScript 开启/关闭、浏览器内旧链接导航、地址与 canonical 更新、后退/前进和恢复文章均正常。诊断点击未写入点击统计。
- API 任务定义 31、Agent Runtime 48 已部署；两个镜像扫描完成且无发现。6 个定时任务均启用，自动发布保持开启。
- CloudFront 缓存清理完成；IndexNow 接受 26 个文章和分类网址更新，返回 HTTP 200。

Google 可重新抓取更新后的 sitemap 和 301。当前没有 Search Console 授权，本次未向其后台提交人工索引请求；IndexNow 接受通知并不代表搜索引擎已经完成收录。

详细网址、审核结论摘要和部署信息见同名 JSON 报告。原始稿件、完整证据和审核响应保存在不提交 Git 的 `.review-runs/2026-09-14-legacy-urls/` 中。
