# 测试与验收报告

- 日期：2026-11-17
- 系统：Windows 11 (10.0.26200)
- Python：CPython 3.13.14
- MCP SDK：`mcp==2.3.0`（官方 Python SDK；当前 2.x 的 `MCPServer` API）
- 协议版本（实测）：`2025-11-25`
- httpx 0.28.1 / BeautifulSoup 4.15.0 / lxml 6.1.3 / PyYAML 6.0.3 / feedparser 6.0.14 / tzdata 2026.6
- Node.js 24.14.1；MCP Inspector 2.13.0

## 1. 第一阶段数据源验证

机器可读原始证据：[`evidence/phase1_xinhua.json`](../evidence/phase1_xinhua.json)；摘要：[`evidence/phase1_xinhua.md`](../evidence/phase1_xinhua.md)。

| 项目 | 实测结果 |
|---|---|
| `https://www.news.cn/robots.txt` | HTTP 200，`User-Agent: * / Allow: /` |
| `https://www.xinhuanet.com/robots.txt` | HTTP 200，同一条 `Allow: /`；两个域名栏目页均 HTTP 200（见 `evidence/domains.json`） |
| 新华网时政栏目页 | HTTP 200，发现 57 条同频道文章链接 |
| 官方 RSS `https://www.news.cn/politics/news_politics.xml` | HTTP 200，300 条，但无 `pubDate`；按文章 URL 日期推断最新为 **2022-12-14**，过期，不可作为最新新闻源 |
| 随机抽样 | 15 篇，14 篇有可见正文并解析成功；取 10 篇作为正文样本 |
| 5 篇人工核对样本 | 标题 5/5、日期 5/5、URL 5/5；正文最小 294 字，最大 8129 字 |
| 结构差异 | 多数页面有 `h1 .title`，但 2026-11-15《中共中央印发…》没有 `h1`，改用 `<title>` 回退并正确去掉“-新华网” |
| 视频稿 | 《我国北斗终端设备应用数量近4200万》正文仅 21 字（记者/编导署名），标记 `partial`，不伪造正文 |

5 篇样本（原始 JSON 含全文）：

1. [推动城市发展绿色低碳转型走深走实](https://www.news.cn/politics/20260803/1472f86405a5426baef62732a3e1b820/c.html) — 2026-08-03 10:13:31 +08:00，1183 字
2. [亚太人文交流活动15日在广东深圳举行](https://www.news.cn/politics/20261116/369a10ef11a446bda631ac4d0de202a7/c.html) — 2026-11-16 01:25:10 +08:00，294 字
3. [学习贯彻党的二十届五中全会精神中央宣讲团在外交部宣讲](https://www.news.cn/politics/20261112/9db79194057143eea5e88a8e224ed920/c.html) — 2026-11-12 23:23:55 +08:00，515 字
4. [中共中央印发《中国共产党地方委员会工作条例》](https://www.news.cn/politics/20261115/aac5a93da4fe438a99ce4bcd477d2e38/c.html) — 2026-11-15 16:56:55 +08:00，8129 字；无 `h1`，标题回退成功
5. [2027年全国硕士研究生招生考试报名人数为347万](https://www.news.cn/politics/20261113/9c1e8a597d4d4003a0e72f2b4008c8eb/c.html) — 2026-11-13 11:33:10 +08:00，647 字

## 2. pytest 结果

原始输出：[`evidence/pytest_full.txt`](../evidence/pytest_full.txt)

```text
...............................................                          [100%]
53 passed in 51.06s
```

- 单元测试（不联网）：48 passed（含 HTTP 传输/鉴权测试、JSON 政策发现）
- 真实网络集成测试：5 passed（新增中国政府网政策验证）
  - 新华网真实采集 + 重复采集去重
  - 新华网 + 中国新闻网跨站检索
  - 中国新闻网真实验证
  - 新华网 RSS 可访问但过期

覆盖点：URL 规范化/去重、域名白名单、私网/回环拒绝、重定向目标校验、robots 失败关闭、超时、404、解析器、RSS 过期、Sitemap、HTML 同频道过滤、SQLite 去重/搜索/日期边界、配置故障隔离、MCP 初始化/工具发现/参数校验、HTTP `/healthz`、Bearer 401。

## 3. MCP Inspector

命令与结果文件：[`evidence/inspector/`](../evidence/inspector/)

| 测试 | 结果 |
|---|---|
| `tools/list` + `--strict` | exit 0；8 个工具，schema 可移植性检查通过 |
| `initialize` | Inspector 自动完成，服务名 `news-mcp` |
| `list_sites` | 3 个站点，均为 `active` |
| `list_channels` | 8 个已验证频道（新华网 6 + 中国新闻网 1 + 中国政府网 1） |
| `get_latest_news` 新华网 | 真实缓存返回，`data_status=local_cache_only` |
| `get_latest_news` 中国新闻网 | 真实缓存返回，来源字段正确 |
| `search_news` 跨站 | 5 条结果，包含 `site_id/site_name`，带本地范围声明 |
| `get_article` 新华网 | HTTP 200，标题/时间/正文/来源完整 |
| `get_article` 非白名单 | `status=rejected`，`error_code=URL_NOT_ALLOWED`，未联网 |
| `refresh_news` | 真实执行，返回 attempted/discovered/inserted/updated/failed |
| `validate_site` 中国新闻网 | 1 个频道通过，3/3 样本正文成功 |
| `inspect_site` 中国新闻网滚动页 | `status=candidate`，`validated=false`，生成候选 YAML 与真实样本 |
| 非法 `limit=0` | MCP 返回 `isError=true`，Inspector exit 5；参数校验生效 |

### 远程 HTTP 传输

- 本地 `python -m news_mcp.http_server` 启动成功；
- `GET /healthz` 返回 200；
- Inspector `--transport http --server-url http://127.0.0.1:8765/mcp --method tools/list` 返回 8 个工具；
- HTTP `tools/call` 调用 `get_latest_news` 成功；
- Bearer Token：无 token 返回 401，带 `Authorization: Bearer ...` 可完成 `tools/list`；
- 证据：`evidence/inspector/http_tools_list.json`、`evidence/inspector/http_call_latest.json`、`evidence/inspector/http_auth.json`、`evidence/inspector/http_tools_list_auth.json`。

> 未验证真实 ChatGPT/豆包账号中的 App 扫描与对话调用；这需要公网 HTTPS、平台账号和平台审核，本地无法代替。

原始文件：

- `evidence/inspector/http_tools_list.json`
- `evidence/inspector/http_call_latest.json`
- `evidence/inspector/tools_list.json`
- `evidence/inspector/list_sites.json`
- `evidence/inspector/list_channels.json`
- `evidence/inspector/latest_xinhua.json`
- `evidence/inspector/latest_chinanews.json`
- `evidence/inspector/search_all.json`
- `evidence/inspector/get_article.json`
- `evidence/inspector/invalid_url.json`
- `evidence/inspector/refresh_xinhua.json`
- `evidence/inspector/validate_chinanews.json`
- `evidence/inspector/inspect_site.json`
- `evidence/inspector/invalid_limit.log`

## 4. 独立 MCP Client

使用官方 `mcp` SDK 的 `stdio_client` + `ClientSession`，不是 Inspector 内部复用：

[`evidence/mcp_client_smoke.json`](../evidence/mcp_client_smoke.json)

- `initialize`：`news-mcp 1.0.0` / `2025-11-25`
- `tools/list`：8 个
- `tools/call`：`list_sites`、`list_channels`、`get_latest_news`、`search_news`、`get_article`（有效 + 非白名单）全部成功返回；非白名单为 `rejected`，不是协议错误。

## 5. 真实 AI 客户端

DeepSeek Harness 通过配置型 bundle `integrations/dsh-news-mcp/` 连接同一 stdio 服务，工具名为 `mcp__news__*`。

完整调用记录与最终回答：[`evidence/ai_client_call.md`](../evidence/ai_client_call.md)

- `mcp__news__list_sites`：录制时为 2 个站点；新增政策源后当前为 3 个
- `mcp__news__get_latest_news(refresh=true)`：真实刷新 3 条，`data_status=live_refresh_completed`
- `mcp__news__search_news(site_id=all)`：跨站 5/12 条
- `mcp__news__get_article`：中国新闻网正文 718 字
- `mcp__news__list_channels`：录制时 7 个；新增政策源后当前为 8 个

## 6. 验收量化结果

原始证据：[`evidence/acceptance_audit.json`](../evidence/acceptance_audit.json)、[`evidence/dataset_build.json`](../evidence/dataset_build.json)、[`evidence/news_samples.csv`](../evidence/news_samples.csv)

| 验收项 | 结果 |
|---|---|
| 5 个原始工具可发现/调用 | 通过；另加 `list_sites`、`inspect_site`、`validate_site`，共 8 个 |
| 至少 10 条有效新闻 | 验收时 32 条；新增政策源后当前 71 条（新华网 31 + 中国新闻网 20 + 中国政府网 20） |
| 10 条人工抽样标题/日期/链接 | 10/10、10/10、10/10 |
| 10 篇可见正文提取 | 10/10（每篇 ≥80 字） |
| 正文不含导航/广告/推荐 | 10/10 `nav_clean` |
| 重复采集去重 | 首次 12 条 inserted；重复 12 条 inserted=0，总数不增加 |
| 关键词/日期过滤 | 单元测试覆盖；跨站搜索结果来源正确 |
| 无结果 | 不存在关键词返回 `count=0`、`results=[]`，未用其他新闻替代（`evidence/empty_result.json`） |
| 断网 | `NETWORK_DISABLED`，无新增文章，`cache_after_refresh_error` 明确标注 |
| 代理拒绝/真实连接失败 | `FETCH_CONNECT_ERROR`，无新增文章 |
| 超时 | 单元测试 `FETCH_TIMEOUT` |
| 404 | 真实白名单 URL 返回 `HTTP_404`，`status=failed` |
| 无效/非白名单 URL | `URL_NOT_ALLOWED`，联网前拒绝 |
| 重新安装启动 | 已用全新 `.venv_fresh` 执行 `pip install -r requirements.txt`，并用该解释器通过 Inspector 调用 `list_sites`；证据 `evidence/fresh_install_list_sites.json` |
| MCP Inspector | 通过（见上） |
| 真实 AI 客户端 | 通过（见上） |
| 新站点不改核心/新华网适配器 | 中国新闻网仅新增 YAML；核心代码无站点分支 |
| 两站同一套工具 | 通过；按 `site_id` 和跨站查询均验证 |

### 实时性与新文章

验收期间从新华网当前栏目页取一个**数据库未保存**的 URL，通过 `get_article` 实时读取《2027年全国硕士研究生招生考试报名人数为347万》，正文 647 字，数据库由 31 增至 32。证明不是固定样本回放。

### 中国政府网政策源

- 新增 `gov_cn` / `latest_policy`；
- 官方 JSON `ZUIXINZHENGCE.json` 返回 1117 条，按配置取前 20 条；
- 2/2 样本正文成功，单篇 8175–12841 字；
- 由于文章页嵌套表格，配置 `html_parser: html.parser`（lxml 会丢失正文）；
- 该源验证后，数据库当前为 71 条。

## 7. 测试中发现并修复的问题

| 问题 | 现象 | 修复 |
|---|---|---|
| 目录 URL 重定向循环 | `/politics` ↔ `/politics/` 三次后失败 | HTTP 请求保留原始尾斜杠；去重仍用规范化 URL |
| Windows 无 IANA 时区 | `ZoneInfo("Asia/Shanghai")` 失败 | 增加 `tzdata` 依赖，并为上海时区提供 +08:00 兜底 |
| RSS 无 `pubDate` | 无法判断新鲜度 | 从文章 URL 日期路径推断并在证据中标注；仍判定过期 |
| 标题缺失 | 个别文章无 `h1` | 回退 `<title>` 并去掉“-新华网” |
| robots 网络失败误报 | 连接失败被说成“robots 禁止” | 区分 `FETCH_CONNECT_ERROR` / `ROBOTS_DISALLOWED` / `ROBOTS_UNAVAILABLE` |
| `inspect_site` 选中视频页 | 候选样本正文为空 | 排除 `/shipin/`、`/video/`、`/photo/` 路径；候选仍不自动标记为有效 |

## 8. 已知限制

- 新华网 RSS 历史化，当前只使用栏目 HTML；站点改版会导致验证失败，不会静默返回空数据。
- 中国新闻网仅接入滚动新闻单页；`/gn/` 等频道页 403，未绕过。
- 搜索为 SQLite `LIKE`，无分词、无相关度排序；只覆盖本地已采集文章。
- 没有实现 JavaScript 动态渲染；需要时通过 `custom_adapter` 扩展。
- `inspect_site` 仅生成候选，必须人工审查并 `validate_site`；这是故意设计，避免把猜测选择器当作有效规则。
- 默认仅抓取栏目页前 N 条，不做全站历史归档。
- 最终数据库中的 `refresh_runs` 保留了两次受控离线测试记录（attempted=0），用于审计，不是采集成功记录。
