# 分阶段实施记录

## 第一阶段：数据来源验证

**修改/新增文件**

- `scripts/phase1_validate.py`
- `evidence/phase1_xinhua.json`
- `evidence/phase1_xinhua.md`
- `config/sites/xinhua.yaml`（此阶段先确认栏目/文章结构，后固化）

**实现**

- 实测 robots.txt、官方 RSS、时政栏目页、文章页；
- 5 篇真实文章全文与标题/日期/链接核对；
- 随机 15 篇样本、10 篇可见正文；
- 逐频道结构检查。

**实际测试**

- RSS：HTTP 200、300 条、最新 2022-12-14，判定过期；
- 栏目页：HTTP 200、57 条同频道链接；
- 5 篇标题/日期/链接 5/5 匹配；
- 随机 15 篇中 14 篇正文成功。

**失败/问题**：RSS 过期；部分视频稿正文只有署名；个别文章无 `h1`（标题回退）。

**下一步**：用 HTML 栏目发现 + `#detailContent` 解析，不使用 RSS。

## 第二阶段：新闻数据模块

**修改/新增文件**

- `news_mcp/models.py`、`urls.py`、`timeutil.py`、`config.py`
- `news_mcp/fetch.py`、`discovery.py`、`parsers.py`、`adapters.py`
- `news_mcp/storage.py`
- `config/settings.yaml`、`config/sites/xinhua.yaml`
- `tests/` 单元与网络测试

**实现**

- 多站点 SQLite schema、规范化 URL 去重、日期 UTC/本地双表示；
- RSS/Sitemap/HTML 三种发现方式；
- robots、限速、SSRF/重定向校验、超时/404/网络错误；
- 正文清洗、视频稿 `partial`、配置 hash 变更使验证失效但不删数据。

**测试**

- 单元测试 43 项通过；
- 真实网络集成 4 项通过（含 RSS 过期、两站采集、去重）。

**失败/问题**：目录 URL 重定向循环；Windows 无 tzdata；robots 连接失败误分类；`inspect_site` 选中视频页。均已修复。

**下一步**：封装 MCP 层。

## 第三阶段：MCP 封装

**修改/新增文件**

- `news_mcp/server.py`
- `news_mcp/cli.py`
- `pyproject.toml`、`requirements.txt`
- `mcp-config.example.json`

**实现**

- 官方 MCP SDK 2.3.0 `MCPServer`，stdio；
- 8 个工具：`list_sites`、`list_channels`、`get_latest_news`、`search_news`、`get_article`、`refresh_news`、`inspect_site`、`validate_site`；
- 输入用 Pydantic `Field` 约束；stdout 只输出 JSON-RPC，日志走 stderr。

**测试**

- `pytest` MCP stdio 测试：initialize、tools/list、tools/call、非法 URL、非法 limit；
- MCP Inspector `tools/list --strict` 通过。

**失败/问题**：MCP SDK 2.x 把 FastMCP 改名为 `MCPServer`；按官方 2.x API 实现，没有伪造协议。

**下一步**：接入第二个站点。

## 第四阶段：真实测试与第二站点

**修改/新增文件**

- `config/sites/chinanews.yaml`
- `scripts/build_dataset.py`、`acceptance_audit.py`、`offline_check.py`、`mcp_client_smoke.py`
- `integrations/dsh-news-mcp/`
- `evidence/` 全部证据

**实现/验证**

- 中国新闻网仅通过新增 YAML 接入，核心/新华网配置未改；
- 两站共用 8 个工具，按 `site_id` 和跨站查询；
- 32 条真实文章；10 条抽样标题/日期/URL/正文 10/10；
- 重复采集插入 0；实时新 URL 插入 1；
- 断网/代理失败/404/超时/非法参数均有明确状态；
- MCP Inspector、独立 SDK 客户端、DeepSeek Harness AI 客户端调用成功。

**失败/问题**：中国新闻网 `/gn/` 等频道 403，未绕过；只接滚动新闻页。已在限制中说明。

**下一步**：交付与验收文档。

## 第五阶段：验收交付

**文件**

- `README.md`、`docs/ADDING_A_SITE.md`、`docs/TEST_REPORT.md`、`docs/PHASES.md`
- `evidence/news_samples.csv`、`evidence/ai_client_call.md`
- `run_mcp.bat`、`mcp-config.example.json`

**测试**

- 50/50 pytest 通过（含远程 HTTP 传输测试）；
- 全新 `.venv_fresh` 安装 requirements 后 Inspector `list_sites` 成功；
- 所有原始证据可追溯。

**遗留**：见 README/TEST_REPORT 的已知限制；没有把视频稿补成正文，没有把候选配置自动标记为有效。

## 第六阶段：远程 MCP（ChatGPT / 豆包）

**新增文件**

- `news_mcp/http_server.py`（Streamable HTTP + `/healthz` + 可选 Bearer）
- `run_http.bat`
- `tests/test_http_server.py`
- `docs/REMOTE_MCP.md`
- `integrations/chatgpt/README.md`
- `integrations/doubao/plugin.json`、`mcp.json`、`README.md`

**实现**

- 复用同一个 `MCPServer`，不改业务逻辑；
- stdio 与 Streamable HTTP 双传输；
- 本地 `127.0.0.1:8765/mcp`，公网通过 Cloudflare Tunnel/ngrok；
- 可选 `Authorization: Bearer` / `X-API-Key`；
- 可选 Host 白名单；
- ChatGPT 无鉴权配置说明、豆包 `mcp.json` 配置。

**测试**

- `/healthz` 200；
- HTTP `tools/list` 8 个工具；
- HTTP `tools/call` 成功；
- Bearer：无 token 401，带 token 可访问；
- 全量 pytest 50 passed。

**遗留**：没有真实 ChatGPT/豆包账号，平台 App 扫描和对话调用需用户用公网 HTTPS 地址完成；ChatGPT 当前主要支持 OAuth/无鉴权，未实现 OAuth。
