# 搜索信息更新与自动发布状态

2026-09-09，针对去重后的公开内容再次核对并提交搜索变更。

| 项目 | 结果 |
| --- | --- |
| 当前公开文章 | 15 篇 |
| 主站点地图 | 26 个页面，其中15篇文章 |
| Googlebot / Bingbot 检查 | 52 次页面检查全部通过 |
| 当前页面 IndexNow 通知 | 26 个网址，HTTP 200 |
| 已退回审核文章 | 21 个网址确认返回404，并通知 IndexNow，HTTP 200 |
| Google Search Console 后台提交 | 未执行：没有发现本站配置的 Search Console 授权 |

两份站点地图均反映当前发布状态：

- https://aperture.zhangwangshu.com/sitemap.xml
- https://aperture.zhangwangshu.com/sitemap-articles.xml

IndexNow 通知已受理，不等于搜索结果已更新；Google不使用IndexNow。
Google可以通过robots.txt中声明的站点地图发现变更，实际索引和摘要更新需等待重新抓取。
公开的Google验证文件不能代替Search Console账户或API授权。

## 自动发布

> 以下为启用前的检查记录；随后已部署新版并开启自动发布，见
> [自动发布记录](automatic-publication-2026-09-09.md)。

线上AgentCore Runtime版本46，状态READY，`RESEARCH_AUTO_PUBLISH=false`。
当前定时任务生成待审核稿，不会自动发布。

新版“相同核心内容及包含关系去重”已用于批量审核脚本，
但尚未接入定时生成流程。定时流程现有的同类别来源重合、标题相似检查，
不能替代新版逐对全文去重。仅打开自动发布开关，不会自动获得新版规则。

详细检查结果见 [搜索更新数据](search-refresh-2026-09-09.json)；
线上配置核对见 [运行状态](search-runtime-status-2026-09-09.json)。
