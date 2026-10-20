# 期刊检索目录

核对日期：2026-10-20。公开目录保存在 `content/journals.json`，完整分区表与下载材料不进入仓库。

## 覆盖和分区口径

当前有 **329 种期刊及 1 个 arXiv 预印本入口**：

| 目录组 | 数量 |
| --- | ---: |
| Nature 品牌（含 Reviews、Protocols、已公告的 Progress 新刊） | 75 |
| npj | 93 |
| Communications 系列 | 11 |
| Scientific Reports / Scientific Data | 2 |
| AAAS Science 主系列 | 6 |
| 其他光学/材料期刊及原有显式订阅 | 142 |

Nature Portfolio 相关组共 181 种，连同 Science 主系列共 187 种。目录中 12 种是年度目录新列出的待发行/公告项，标记为 `announced_catalog`；目录存在不代表已经发表论文，采集必须通过真实出版日期检查。

**149 种**有公开 **2025 中科院期刊分区表升级版**的相关记录，条件为：

- 材料科学大类 1 区或 2 区；或
- `OPTICS 光学`小类 1 区或 2 区。

其中材料科学大类 120 种、光学小类 30 种，重合 1 种。这 149 种与 Nature/Science 组存在交集，不能与上表简单相加。原有 PRB、APL 等显式订阅继续保留，不因本轮分区筛选删除。未满足或未核查上述条件的条目不会标成中科院 1/2 区。

分区证据来自[华北电力大学公开发布页面](https://cce.ncepu.edu.cn/xzzx/yjsjx1/7632005045644e61a0d12c7bbeff6204.htm)及其[2025 升级版附件](https://cce.ncepu.edu.cn/docs//2026-09/0e77800bf2744596a26e43c83acceed0.xlsx)。条目标记为 `verified_university_2025_table`，明确这是高校转载表核对，不声称在原分区平台完成在线认证。未使用 JCR 或其他 2026 分区代替中科院口径。

## 出版社和 ISSN 证据

- [Nature A–Z](https://www.nature.com/siteindex)，以及其 Research、Reviews、npj、Communications、Progress 官方分系列目录。
- [Springer Nature 年度期刊目录](https://www.springernature.com/gp/librarians/licensing/journals-catalog)：2026 目录和 2026 年 9 月发布的 2027 目录，用作可离线使用的出版社快照。
- [Science 官方期刊入口](https://www.science.org/journals)：Science、Science Advances、Science Immunology、Science Robotics、Science Signaling、Science Translational Medicine。
- [Crossref 官方元数据 API](https://www.crossref.org/documentation/retrieve-metadata/rest-api)：按 ISSN 校验位、期刊名称与注册记录核对。

329 种期刊均有可检索的权威来源 ISSN，共 362 个不同 ISSN。295 种通过 Crossref 注册接口交叉核对；34 种尚未由该接口确认（28 种依据出版社目录、6 种依据高校分区表），包括新刊、请求失败及注册记录刊名冲突，状态单独保留。Crossref 个别注册记录曾混入相邻刊物的 ISSN，因此采集结果必须同时匹配 ISSN 和期刊名称，不能仅凭注册接口返回的全部 ISSN 自动扩张。

## 更新与采集

目录正常每 7 天尝试更新 Nature/Science 官方页面，失败保留上次数据并在 1 天后重试；新刊分批核对 Crossref。运行时缓存使用 `OPTINFO_DATA_DIR`，未设置时使用 `OPTINFO_DB_PATH` 所在目录。客户端 bootstrap 只读磁盘，不等待外网请求。

每日检索将期刊按 ISSN 分组，单次 socket 超时最多 8 秒，目录更新与论文候选采集分别有 25 秒和 90 秒预算。Crossref 公共数组接口的实测响应头要求每秒最多 1 次、同时最多 1 个请求，因此候选检索与目录注册核对共用串行节流；429 最多退避重试一次，并受同一截止时间约束。已注册期刊与尚未完成注册核对的期刊分组请求，每组最多取 100 条；截断、失败、HTTP 状态码及延期主题都写入诊断。

新增几百种期刊不会被默认全勾选，原开题方向及已保存偏好保持。未明确选中的期刊按研究方向分别发起短查询，结果取并集；每个方向的主关键词先覆盖全部期刊批次，再处理别名，主题和期刊批次起点按日期轮换，避免连续慢响应时总是跳过同一方向。不会把所有兴趣拼成长串，也不使用接口不支持的布尔 OR 语法（见 [Crossref 对最低词语匹配门槛的说明](https://community.crossref.org/t/advanced-set-boolean-operators/4596)）。空兴趣依次使用光学、光子学、超快、半导体、材料和激光主题。返回结果还要经过日期、刊名、DOI 去重、研究兴趣、全文及图表证据等后续检查。目录覆盖表示有这些来源可供检索，**不表示下载全部论文或推送全部结果**。
