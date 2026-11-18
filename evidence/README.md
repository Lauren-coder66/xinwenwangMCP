# 证据索引

所有 JSON 都是实际运行输出；没有手工构造的新闻内容。

| 文件 | 内容 |
|---|---|
| `domains.json` | `news.cn` / `xinhuanet.com` / `chinanews.com.cn` 的实时域名、robots 与栏目页状态 |
| `phase1_xinhua.json` | 第一阶段原始数据：RSS 状态、robots、栏目链接、5 篇全文、10 篇随机可见正文、逐频道结构检查 |
| `phase1_xinhua.md` | 第一阶段人类可读摘要 |
| `dataset_build.json` | 最终数据集构建过程：两站验证、采集计数、重复采集、审计运行 |
| `acceptance_audit.json` | 10 条人工可核对样本（标题/日期/URL/正文/导航清洗）、实时新 URL、404、去重 |
| `gov_cn_validation.json` | 中国政府网最新政策源真实验证（JSON 发现 + 2 篇正文） |
| `policy_scan.json` | 一人公司相关关键词在政策库中的命中扫描 |
| `news_samples.csv` | 32 条入库文章的完整 CSV（含正文与原文 URL，UTF-8 BOM） |
| `news_samples.json` | 同上，JSON |
| `pytest_full.txt` | 53 个 pytest 的原始输出 |
| `mcp_client_smoke.json` | 独立官方 SDK 客户端 initialize/tools/list/tools/call |
| `ai_client_call.md` | DeepSeek Harness 真实 AI 客户端工具调用日志与最终回答 |
| `dsh_bundle_install.json` | DeepSeek Harness 配置型 bundle 安装结果（`application=applied`）与暴露的工具名 |
| `empty_result.json` | 不存在关键词返回空列表、不是替代新闻 |
| `offline_app-offline.json` | `NEWS_MCP_OFFLINE=1` 断网路径 |
| `offline_proxy-refused.json` | 代理指向关闭端口造成的真实连接失败 |
| `inspector/tools_list.json` | Inspector stdio `tools/list`（`--strict` 通过） |
| `inspector/http_tools_list.json` | Inspector HTTP `tools/list`（Streamable HTTP） |
| `inspector/http_call_latest.json` | Inspector HTTP `tools/call` 真实新闻 |
| `inspector/http_auth.json` | HTTP 健康检查 200、无 token 401 |
| `inspector/http_tools_list_auth.json` | 带 Bearer token 的 HTTP `tools/list` |
| `inspector/list_sites.json` | Inspector `list_sites` |
| `inspector/list_channels.json` | Inspector `list_channels` |
| `inspector/latest_xinhua.json` | Inspector 新华网最新新闻 |
| `inspector/latest_chinanews.json` | Inspector 中国新闻网最新新闻 |
| `inspector/search_all.json` | Inspector 跨站搜索 |
| `inspector/get_article.json` | Inspector 实时正文 |
| `inspector/invalid_url.json` | Inspector 非白名单 URL 拒绝 |
| `inspector/refresh_xinhua.json` | Inspector 真实刷新 |
| `inspector/validate_chinanews.json` | Inspector 真实验证第二站点 |
| `inspector/inspect_site.json` | Inspector 自动检测候选配置（validated=false） |
| `inspector/invalid_limit.log` | 非法参数 `limit=0` 的 MCP 参数校验错误 |
| `fresh_install_list_sites.json` | 全新 venv 安装 requirements 后，通过 Inspector 启动并调用 `list_sites` |

复现命令见项目根目录 `README.md`，或逐个运行 `scripts/` 下的脚本。
