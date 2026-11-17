# 多站点新闻 MCP 连接器

该配置仅连接同项目的 `news_mcp.server`（stdio）到 DeepSeek Harness，不包含任何抓取逻辑。

- 工具命名：`mcp__news__list_sites`、`mcp__news__get_latest_news` 等。
- 数据来源：`D:\Desktop\新华网MCP\data\news.db`。
- 所有文章保留原文 URL、发布时间和抓取时间；`search_news` 只检索本地已采集数据。
- 服务端遵守 robots.txt、域名白名单和 1.5 秒/域名限速。
- 如需更换数据库或配置目录，修改 `cordis.patch.yml` 中的 `NEWS_MCP_DB` / `NEWS_MCP_CONFIG_DIR`。
