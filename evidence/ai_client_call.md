# 真实 AI 客户端端到端调用记录

- 客户端：DeepSeek Harness（desktop profile）
- 模型/代理：DeepSeek Harness 当前会话（deepseek-flash）
- MCP 连接器：`@local/news-mcp-connector`，`serverName=news`，stdio
- 服务：`news_mcp.server`，数据库 `data/news.db`
- 时间：2026-11-17 22:00（Asia/Shanghai）

连接器安装结果：`application: applied`。以下调用由 AI 客户端通过 MCP 原生工具名发起，不是直接调用 Python 函数。

> 调用时数据库为 31 条；随后的验收脚本通过 `get_article` 实时新增 1 条新华网 URL，最终数据库为 32 条。下面保留调用当时的真实快照。

## 调用 1：`mcp__news__list_sites`

参数：`{}`

结果摘要：

```json
{
  "ok": true,
  "status": "ok",
  "sites": [
    {"site_id": "chinanews", "name": "中国新闻网", "status": "active", "channels_valid": 1, "articles": 10},
    {"site_id": "xinhua", "name": "新华网", "status": "active", "channels_valid": 6, "articles": 21}
  ],
  "config_errors": []
}
```

## 调用 2：`mcp__news__get_latest_news`（真实刷新）

参数：

```json
{"channel": "politics", "limit": 3, "refresh": true, "site_id": "xinhua"}
```

结果：

```json
{
  "ok": true,
  "status": "ok",
  "data_status": "live_refresh_completed",
  "data_source": "live_http_fetch_then_sqlite",
  "articles": [
    {
      "title": "新华社权威快报丨我国将逐步实现疫苗处方全覆盖",
      "url": "https://www.news.cn/politics/20261117/3f1e8a88dbcb46df9ea455ffb5e144d9/c.html",
      "published_at_local": "2026-11-17T14:41:40+08:00",
      "source": "新华社",
      "extraction_status": "ok",
      "content_chars": 365,
      "data_status": "live"
    },
    {
      "title": "新华社消息丨我国北斗终端设备应用数量近4200万",
      "url": "https://www.news.cn/politics/20261117/ca1bb9013a0c438ab5b1a8b212348933/c.html",
      "published_at_local": "2026-11-17T11:04:48+08:00",
      "source": "新华网",
      "extraction_status": "partial",
      "content_chars": 21,
      "data_status": "live"
    },
    {
      "title": "医保钱包代缴居民医保费统筹地区范围不断扩大",
      "url": "https://www.news.cn/politics/20261117/042b31d8108b4658be9777ad2f761c5e/c.html",
      "published_at_local": "2026-11-17T08:02:10+08:00",
      "source": "新华网",
      "extraction_status": "ok",
      "content_chars": 295,
      "data_status": "live"
    }
  ],
  "refresh": {
    "attempted_count": 3,
    "discovered_count": 3,
    "inserted_count": 0,
    "updated_count": 0,
    "failed_count": 0,
    "partial_count": 1,
    "errors": [
      {"code": "PARTIAL_EXTRACTION", "message": "正文过短（21 字符，阈值 80）"}
    ]
  }
}
```

AI 客户端据此知道：第 2 条是视频稿，正文只有署名，不能当作完整正文；数据库已有该文章，因此本次 `inserted_count=0`。

## 调用 3：`mcp__news__search_news`（跨站）

参数：

```json
{"keyword": "中国", "limit": 5, "site_id": "all"}
```

结果（`total_matches=12`，以下为返回 5 条）：

| site_id | 来源 | 标题 | 发布时间（+08:00） | URL |
|---|---|---|---|---|
| chinanews | 中国新闻网 | 巴西累西腓华侨华人协会拜会中国驻累总领馆 | 2026-11-17 21:45 | https://www.chinanews.com.cn/hr/2026/11-17/10733769.shtml |
| chinanews | 中国新闻网 | （深圳APEC）韩国青年申文燮：深圳赋予我创作热情和灵感 | 2026-11-17 21:44 | https://www.chinanews.com.cn/gj/2026/11-17/10733746.shtml |
| chinanews | 中国新闻网 | 聚焦林默“少年时代” 《妈祖之林默传》动画电影项目发布 | 2026-11-17 21:41 | https://www.chinanews.com.cn/cul/2026/11-17/10733765.shtml |
| chinanews | 中国新闻网 | 两部海洋文化题材动画电影项目在福建霞浦启动 | 2026-11-17 21:40 | https://www.chinanews.com.cn/cul/2026/11-17/10733766.shtml |
| xinhua | 新华网 | 上海博物馆与美国、德国顶尖博物馆签约促文化交流 | 2026-11-16 23:43 | https://www.news.cn/politics/20261116/7d5d0633cf2b4f428a6147a65755f530/c.html |

结果显式包含 `"scope": "local_collected_articles_only"` 和免责声明；AI 客户端未把它表述为全网搜索。

## 调用 4：`mcp__news__get_article`（实时正文）

参数：

```json
{"url": "https://www.chinanews.com.cn/gj/2026/11-17/10733746.shtml", "site_id": "chinanews"}
```

结果：

```json
{
  "ok": true,
  "status": "ok",
  "title": "（深圳APEC）韩国青年申文燮：深圳赋予我创作热情和灵感",
  "published_at_local": "2026-11-17T21:44:00+08:00",
  "source": "中国新闻网",
  "data_status": "live_http_fetch",
  "http_status": 200,
  "content_chars": 718,
  "content": "中新社 深圳11月17日电 (记者 周昕)亚太经合组织(APEC)第三十三次领导人非正式会议将启，由多国青年联袂演绎的APEC主题音乐《同一个世界(One World)》受到关注。……(完)"
}
```

## 调用 5：`mcp__news__list_channels`

参数：`{"site_id": "all"}`

结果：7 个已验证频道（新华网 6 + 中国新闻网 1），均包含 `source_url`、`validated=true`、`article_count`。

## AI 客户端最终回答（节选）

> 我通过本地 MCP 服务读取了真实新闻：
>
> - 新华网时政：**《新华社权威快报丨我国将逐步实现疫苗处方全覆盖》**，2026-11-17 14:41，来源新华社，正文 365 字。[原文](https://www.news.cn/politics/20261117/3f1e8a88dbcb46df9ea455ffb5e144d9/c.html)
> - 新华网时政：**《医保钱包代缴居民医保费统筹地区范围不断扩大》**，2026-11-17 08:02，正文 295 字。[原文](https://www.news.cn/politics/20261117/042b31d8108b4658be9777ad2f761c5e/c.html)
> - 同频道的《我国北斗终端设备应用数量近4200万》是视频稿，正文只有记者/编导署名，服务标记为 `partial`，我没有把它当作完整正文。
>
> 跨站检索“中国”共命中本地已采集文章 12 条，包括中国新闻网《（深圳APEC）韩国青年申文燮：深圳赋予我创作热情和灵感》。该文通过 `get_article` 实时读取，正文 718 字。[原文](https://www.chinanews.com.cn/gj/2026/11-17/10733746.shtml)
>
> 以上结果来自本地 SQLite 中已采集的新华网/中国新闻网文章；`search_news` 的搜索范围仅为本地已采集数据，不代表全网搜索。

## 可复现性

- 连接器配置：[`integrations/dsh-news-mcp/cordis.patch.yml`](../integrations/dsh-news-mcp/cordis.patch.yml)
- 独立 SDK 客户端日志：[`evidence/mcp_client_smoke.json`](mcp_client_smoke.json)
- Inspector 日志：[`evidence/inspector/`](inspector/)
