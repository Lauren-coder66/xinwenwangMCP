# 多站点新闻 MCP Server

一个可扩展、可追溯的多站点新闻采集与查询服务，使用官方 MCP Python SDK 的 stdio 传输。新华网是第一个站点，中国新闻网是第二个真实测试站点；两者共用同一套 MCP Server 和同一套工具，不需要为每个网站复制一套服务。

> 数据来自真实 HTTP 请求，保存在本地 SQLite。`search_news` 只检索**本地已采集文章**，不代表搜索了任何网站的全站或全部历史。项目不包含模拟新闻，也不会用其他新闻替代空结果。

## 当前验证状态（2026-11-17）

- 新华网：6 个频道通过真实样本验证（时政、国际、财经、科技、文化、体育），30+ 条真实文章；`www.news.cn` 与 `www.xinhuanet.com` 栏目页/robots 均实测 HTTP 200，统一规范到 `www.news.cn`。
- 中国新闻网：滚动新闻频道通过真实样本验证，使用独立的 YAML 配置接入，**未修改 MCP Server 核心代码，也未修改新华网适配器**。
- 中国政府网：`latest_policy`（最新政策）通过真实样本验证，使用官方 JSON 列表发现。
- 当前共 **3 个站点、8 个已验证频道、71 条本地文章**（新华网 31、中国新闻网 20、中国政府网 20）。政策机会设计见 [`docs/POLICY.md`](docs/POLICY.md)。
- 新华网官方 RSS：HTTP 200，但只有 2022 年历史条目，最新为 2022-12-14，已明确判定为过期，不用于最新新闻。
- 8 个 MCP 工具全部可被 `tools/list` 发现；Inspector、独立官方 SDK 客户端、DeepSeek Harness 真实 AI 客户端均已调用成功。
- 已增加远程 **Streamable HTTP** 传输，可供 ChatGPT 自定义 App/Connector 和豆包插件通过公网 HTTPS 调用；本地 HTTP 协议已用 Inspector 验证。
- 详细证据见 [`evidence/`](evidence/)、[`docs/TEST_REPORT.md`](docs/TEST_REPORT.md) 与分阶段记录 [`docs/PHASES.md`](docs/PHASES.md)。

## 架构

```text
config/sites/*.yaml
        │
        ▼
ConfigRegistry ──► SiteAdapter（统一接口）
        │              ├─ GenericSiteAdapter（RSS / Sitemap / HTML，配置驱动）
        │              └─ custom_adapter（可选，module:ClassName）
        ▼
NewsService ──► HttpClient（白名单 / DNS / 重定向 / robots.txt / 限速 / 超时）
        │              └─ ArticleParser（标题 / 时间 / 来源 / 正文清洗）
        ▼
SQLite（sites / channels / articles / refresh_runs）
        │
        ▼
MCP Server（官方 SDK，stdio）──► AI 客户端
```

核心代码不包含 `if site == "xinhua"` 之类的站点分支。站点差异放在 YAML 中：

- `allowed_domains`：域名白名单；
- `article_url_patterns`：文章 URL 规则；
- `channels[].discovery`：`html`、`rss`、`sitemap` 发现方式；
- `article`：标题、日期、来源、正文 CSS 选择器和清洗规则；
- `request_delay_seconds`、`timeout_seconds`：访问频率与超时。

## Windows 安装

要求：Windows 10/11，Python 3.13（已在 CPython 3.13.14 上验证），Node.js 仅用于 MCP Inspector。

```powershell
cd D:\Desktop\新华网MCP
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

不要依赖 `python` 指向哪个版本；下文统一使用项目内的 `.venv\Scripts\python.exe`。

初始化数据库（不会删除已有数据）：

```powershell
.\.venv\Scripts\python.exe -m news_mcp.cli init
```

真实验证两个站点（会访问网络，约 40–60 秒）：

```powershell
.\.venv\Scripts\python.exe -m news_mcp.cli validate --all
```

采集示例：

```powershell
.\.venv\Scripts\python.exe -m news_mcp.cli refresh --site xinhua --channel politics --limit 10
.\.venv\Scripts\python.exe -m news_mcp.cli refresh --site chinanews --channel latest --limit 10
```

查询：

```powershell
.\.venv\Scripts\python.exe -m news_mcp.cli latest --site xinhua --channel politics --limit 5
.\.venv\Scripts\python.exe -m news_mcp.cli search --keyword 医保 --site all --limit 10
```

启动 stdio MCP 服务：

```powershell
.\.venv\Scripts\python.exe -m news_mcp.server
```

stdout 只承载 MCP JSON-RPC；日志写入 stderr，不会污染协议。

## MCP 客户端配置

### 通用 JSON（Claude Desktop / Cursor / 其他支持 stdio 的客户端）

```json
{
  "mcpServers": {
    "news": {
      "command": "D:\\Desktop\\新华网MCP\\.venv\\Scripts\\python.exe",
      "args": ["-m", "news_mcp.server"],
      "cwd": "D:\\Desktop\\新华网MCP",
      "env": {
        "NEWS_MCP_DB": "D:\\Desktop\\新华网MCP\\data\\news.db",
        "NEWS_MCP_CONFIG_DIR": "D:\\Desktop\\新华网MCP\\config",
        "PYTHONIOENCODING": "utf-8"
      }
    }
  }
}
```

### 本项目已安装的 DeepSeek Harness 连接器

[`integrations/dsh-news-mcp/`](integrations/dsh-news-mcp/) 是配置型 bundle，不包含抓取逻辑；安装后工具名为 `mcp__news__list_sites` 等。实际调用记录见 [`evidence/ai_client_call.md`](evidence/ai_client_call.md)。

## 远程 MCP：ChatGPT / 豆包

ChatGPT 和豆包不能直接启动本地 stdio 进程，需要**公网 HTTPS 的 Streamable HTTP 端点**。项目已经提供同一套工具的第二传输入口：

```powershell
.\.venv\Scripts\python.exe -m news_mcp.http_server --host 127.0.0.1 --port 8765 --path /mcp
```

本地健康检查：

```powershell
curl.exe http://127.0.0.1:8765/healthz
```

公网测试可以用 Cloudflare Tunnel：

```powershell
cloudflared tunnel --url http://127.0.0.1:8765
```

然后把 `https://随机域名/mcp` 填到 ChatGPT Developer Mode 的 App 或豆包插件的 `mcp.json`。

- ChatGPT 配置：[integrations/chatgpt/README.md](integrations/chatgpt/README.md)
- 豆包配置：[integrations/doubao/README.md](integrations/doubao/README.md)
- 完整说明、鉴权和部署限制：[docs/REMOTE_MCP.md](docs/REMOTE_MCP.md)

注意：ChatGPT 自定义 MCP 当前主要支持 OAuth 或无鉴权，不能假设它会发送自定义 Bearer Header；豆包支持在 `mcp.json` 的 `headers` 中放 Bearer Token。公网无鉴权 URL 存在滥用风险，正式部署应使用网关、OAuth 或访问白名单。

## MCP Tools

| 工具 | 主要输入 | 行为 |
|---|---|---|
| `list_sites` | 无 | 列出站点、验证状态、频道/文章数量、配置错误 |
| `list_channels` | `site_id?` | 只返回已通过真实验证的频道和来源 URL；`all`/省略为全部 |
| `get_latest_news` | `channel`, `limit`, `refresh?`, `site_id?` | 查询最新；`refresh=true` 先真实采集；每条标明 `live`/`cached` |
| `search_news` | `keyword`, `start_date?`, `end_date?`, `channel?`, `limit`, `site_id?` | 仅本地 SQLite 关键词/日期过滤；`site_id` 省略时跨站 |
| `get_article` | `url`, `site_id?` | 实时抓取白名单文章，校验跳转、robots、超时、404，返回正文与状态 |
| `refresh_news` | `channel`, `limit`, `site_id?` | 真实采集并返回 attempted/discovered/inserted/updated/failed/partial |
| `inspect_site` | `url` | 真实抓取并生成候选 RSS/Sitemap/HTML 配置；`validated` 永远为 false |
| `validate_site` | `site_id` | 真实样本验证；只有至少一个频道有可解析正文才标记为可采集 |

### 数据状态语义

- `data_status=live`：本次调用中该文章被真实抓取。
- `data_status=cached`：来自本地 SQLite，不是本次新抓取。
- `data_status=local_cache_only`：本次未联网。
- `data_status=cache_after_refresh_error`：本次实时刷新失败，返回的是旧缓存；`refresh.errors` 解释失败原因。
- `data_status=rejected_before_network`：URL 白名单/格式校验失败，未联网。
- `extraction_status=ok/partial/failed`：正文完整、正文不完整（如视频稿）、标题/正文失败。
- `url`：规范化后的可追溯文章链接；`original_url`：采集/调用时的原始 URL（用于核对跳转和域名别名）。
- `published_at`：UTC ISO 8601；`published_at_local`：站点时区 ISO 8601；`published_date`：本地日期。
- `scope=local_collected_articles_only`：搜索范围声明，所有搜索工具均返回。

## 数据质量与安全

- URL 规范化去重：去掉 fragment、跟踪参数，排序保留参数，统一 https，按 `(site_id, url)` 唯一约束。
- `robots.txt`：每个域名首次访问时读取并缓存；失败策略默认 `closed`。
- 限速：每域名默认 1.5 秒；可通过 YAML 调整，不能低于 0.5 秒。
- SSRF：只允许 `http/https`、白名单域名、80/443；解析到私网/回环/链路本地地址直接拒绝；每一跳重定向都重新校验。
- 正文清洗：只取配置的正文容器；移除脚本、广告、推荐、分享、责任编辑等；视频稿正文过短时标记 `partial`，不生成假正文。
- 外部网页内容只作为数据处理，**不执行其中的指令**。
- 不包含验证码绕过、登录绕过、反爬规避；中国新闻网频道页 `/gn/` 返回 403 时没有绕过，改用其允许访问的滚动新闻页。

## 新增网站

完整操作见 [`docs/ADDING_A_SITE.md`](docs/ADDING_A_SITE.md)，政策源规划见 [`docs/POLICY.md`](docs/POLICY.md)。最短流程：

1. 用 `inspect_site` 或 CLI 检查目标 URL，得到**未经确认**的候选 YAML；
2. 人工审查选择器和 URL 规则，保存为 `config/sites/<site_id>.yaml`；
3. 运行 `validate_site`（或 `news-mcp-cli validate --site <id>`）；
4. 验证失败只影响该站点，不会影响其他站点；
5. `refresh_news` 通过后，`search_news` 自动跨站可用。

普通静态站点只改 YAML；RSS/Sitemap 配置项已内置，动态网页或官方 API 可通过 `custom_adapter: "module:ClassName"` 扩展。

## 测试

```powershell
# 单元测试（不联网）
.\.venv\Scripts\python.exe -m pytest -m "not network" -q

# 真实网络集成测试（会访问新华网、中国新闻网）
.\.venv\Scripts\python.exe -m pytest -m network -v

# 全部
.\.venv\Scripts\python.exe -m pytest -v
```

测试记录、原始输出和 MCP Inspector 证据：[`docs/TEST_REPORT.md`](docs/TEST_REPORT.md)、[`evidence/`](evidence/)。

## 已知限制

- 新华网 RSS 已过期，当前只用栏目 HTML；若栏目结构变化，`validate_site` 会失败并明确报错。
- 新华网视频稿可能只有署名，正文标记 `partial`；不会伪造。
- 中国新闻网仅接入滚动新闻单页（约 120 条），没有翻页；其 `/gn/` 等频道页返回 403，未绕过。
- 搜索是 SQLite `LIKE`，不是 FTS，不做分词/相关度排序；只覆盖已采集数据。
- 没有实现动态 JavaScript 渲染站点；可通过 `custom_adapter` 后续扩展。
- `inspect_site` 是候选生成器，不是自动接入器；候选必须经 `validate_site` 后才算有效。
- 默认不删除旧文章；配置变更会使验证状态失效并要求重新验证，但不会清空数据。

## 目录

```text
mcp-config.example.json  MCP 客户端配置示例
run_mcp.bat              Windows stdio 启动脚本
run_http.bat             Windows 远程 HTTP 启动脚本
docs/REMOTE_MCP.md       ChatGPT / 豆包远程接入说明
integrations/chatgpt/    ChatGPT 自定义 App/Connector 配置说明
integrations/doubao/     豆包插件 plugin.json + mcp.json
config/                  settings.yaml + sites/xinhua.yaml + sites/chinanews.yaml + sites/gov_cn.yaml
news_mcp/                核心：config/fetch/parsers/discovery/adapters/storage/service/server
tests/                   pytest 单元与真实网络测试
scripts/                 第一阶段证据、数据集构建、验收、离线、MCP 客户端脚本
evidence/                原始 JSON/日志与 AI 调用记录
docs/                    新增站点说明、政策机会设计、测试报告
integrations/dsh-news-mcp/  DeepSeek Harness 配置型连接器
data/news.db             本地 SQLite（可重建）
```

## 合规说明

本项目仅用于个人/研究型新闻监测。使用前应自行确认目标网站的服务条款、版权和转载限制；不要高频抓取，不要绕过访问控制。保留原文 URL 和获取时间，引用内容时请注明来源。
