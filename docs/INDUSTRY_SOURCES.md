# 企业新闻来源与回溯

`optinfo.industry_sources` 采集企业官方网站已公开的 RSS 或 HTML 公告。下表接口于 **2026-10-20** 逐一实际访问并验证能解析标题、官方链接和明确发布日期；不依赖搜索摘要生成资讯，不调用付费模型进行采集。

| 领域 | 企业 | 官方入口 | 实际解析方式 |
| --- | --- | --- | --- |
| 芯片设计 | NVIDIA | [Newsroom](https://nvidianews.nvidia.com/releases.xml) | RSS `pubDate` |
| 芯片设计 | AMD | [投资者公告](https://ir.amd.com/news-events/press-releases/rss) | RSS `pubDate` |
| 芯片设计 | Intel | [Newsroom](https://www.intel.com/content/www/us/en/newsroom/home.html) | HTML 新闻卡片及卡片日期；旧 RSS 已重定向为网页 |
| 芯片设计 | Arm | [Newsroom](https://newsroom.arm.com/news/feed/) | RSS 及经验证的 `paged` 分页 |
| 晶圆代工 | TSMC | [最新公告](https://pr.tsmc.com/english/latest-news)、[历史公告](https://pr.tsmc.com/english/news-archives) | `article.articleItem-body`、`time`；跟随页面实际给出的分页链接 |
| 晶圆代工 | 中芯国际 | [官方公告](https://www.smics.com/en/site/news) | HTML 表格日期及页面实际给出的年份档案 |
| 存储 | Samsung Electronics | [半导体分类](https://news.samsung.com/global/category/technology/semiconductors/feed) | 半导体分类 RSS，避免混入手机家电全站新闻 |
| 存储 | SK hynix | [Newsroom](https://news.skhynix.com/en/feed/) | RSS 及经验证的 `paged` 分页 |
| 存储 | Micron | [官方新闻](https://www.micron.com/about/press/news) | HTML 公告卡片；接受官方页面链接的 `investors.micron.com` 公告 |
| 制造设备 | KLA | [投资者公告](https://ir.kla.com/news-events/press-releases/rss) | RSS `pubDate` |
| 制造设备 | Lam Research | [Newsroom](https://newsroom.lamresearch.com/press-releases) | HTML `.wd_item`、`.wd_date` 及实际下一页链接 |
| 制造设备 | 中微公司 | [最新新闻](https://www.amec-inc.com/latestNews.html) | 列表只给月份，额外读该公告正文的 `<time>` 确认具体日期 |
| 光子、探测 | Hamamatsu | [官方新闻](https://www.hamamatsu.com/us/en/news.html) | HTML 新闻列表的 `Date` / `Title`；保留官方 PDF 公告原链接 |
| 半导体光学 | ZEISS SMT | [新闻与活动](https://www.zeiss.com/semiconductor-manufacturing-technology/news-and-events.html) | HTML 新闻卡片和卡片日期 |
| 激光 | IPG Photonics | [官方新闻](https://www.ipgphotonics.com/en-us/newsroom/news) | HTML 新闻卡片、`.date-details` |
| 激光与加工 | TRUMPF | [全球新闻](https://www.trumpf.com/en_US/newsroom/global-press-releases/) | HTML 公告、`time` 及实际下一页链接 |

接口：

```python
collect("2026-10-20", sections={"industry"}, industry_days=90, topic_ids=effective_topics)
# -> (candidates, source_diagnostics)
```

支持 `industry_days=7/30/90/365`。每天先收近期候选，数量或企业覆盖不足时，由 pipeline 仅扩大企业窗口；研究与重大新闻不重复采集。每条候选保留 `published_at`、`source_url`、`source_id`、`industry_category`、`publication_date_evidence`，来源所属企业放在 `company_ids` 首位，供公平分配与企业图鉴使用。发布日期绝不替换成简报日期，不从 URL、页脚版权年份或抓取时间猜测。只给月份而无法从正文核实日的条目会跳过。

各源最多 2 / 2 / 3 / 4 个列表页（分别对应四个窗口），最多 60 条候选；中微最多额外读取 6 个正文日期。每源设 45 秒的启动新请求预算，每次请求有超时及有限重试，最多 4 个企业源并发。已解析元数据在当前进程缓存 15 分钟、最多 96 页，逐级扩大窗口复用缓存。只跟随明确允许的官方主机，源站失败互相隔离，某历史页失败不会丢掉已成功获得的近期新闻。

这是**有预算的历史补充，不是完整年度档案镜像**。有的官网只提供少量最新条目；部分列表不按时间完全排序。`bounded` 表示触及页数、候选量或时间预算；`status=partial` 表示部分页面失败但仍有可用内容。诊断仅记录源 ID、计数和异常类型，不记录正文或认证信息。台积电、TRUMPF 在实测中各出现过单页超时，复测仍可取得有效公告。企业间公平分配、跨 URL 重复公告及不同日期同标题识别在 pipeline / 数据库统一处理；采集器本身只消除相同 URL。

已实测但本轮没有启用的入口包括 ASML、Applied Materials 和 Coherent 投资者新闻页（403 或列表仅由前端动态加载），以及北方华创新闻页（连接超时）。已有的 ASML、Applied Materials、Coherent 图鉴保留，但不把不可用接口伪装为正常新闻来源，也不通过绕过验证抓取。

图鉴新增 Arm、中芯国际、三星电子、SK hynix、美光、KLA、Lam Research、IPG 的官方业务与上市信息、官网标志引用。没有新增未经公开证据支持的薪资数字；原有带来源薪资记录保留。

离线回归覆盖日期证据、月份补取、时间窗口、官方主机校验、分页预算、失败隔离、缓存复用、相同标题跨日期保留，以及行业独立采集接口。执行：

```powershell
backend/.venv/Scripts/python.exe -m pytest backend/tests/test_industry_sources.py -q
```
