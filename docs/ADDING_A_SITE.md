# 新增网站接入操作说明

目标：从输入一个真实网站/栏目 URL，到该站点能通过同一套 MCP 工具查询。普通静态站点只新增 YAML，不改 `news_mcp/` 核心代码，也不复制 MCP Server。

## 0. 先确认合规与可访问性

必须确认：

- `robots.txt` 允许抓取目标路径；
- 网站服务条款/版权允许该用途；
- 不绕过登录、验证码、403/反爬；
- 不把请求频率设得过高（默认 1.5 秒/域名，最低 0.5 秒）。

中国新闻网案例：`https://www.chinanews.com.cn/robots.txt` 允许 `/`，但 `/gn/` 等频道页返回 403；因此只配置允许访问的滚动新闻页 `https://www.chinanews.com.cn/scroll-news/news1.html`，没有尝试绕过 403。

## 1. 用 `inspect_site` 探测

CLI：

```powershell
.\.venv\Scripts\python.exe -m news_mcp.cli inspect --url https://www.chinanews.com.cn/scroll-news/news1.html
```

MCP 工具：

```json
{"url": "https://www.chinanews.com.cn/scroll-news/news1.html"}
```

`inspect_site` 会真实抓取页面，并检查：

- `robots.txt`；
- RSS/Atom autodiscovery 和常见 feed 路径；
- robots 中的 Sitemap；
- 同站文章链接候选；
- 最多 5 个真实文章样本的标题、日期、正文选择器；
- 每个候选选择器的命中样本数。

输出关键字段：

- `validated: false`：**永远**为 false；这是候选，不是已验证配置。
- `candidate_ready`：至少 2 个样本有标题和正文。
- `suggested_selectors`：从真实样本中统计出的候选选择器。
- `candidate_config_yaml`：可直接保存的候选 YAML（`enabled: false`）。
- `warnings`：RSS 过期、样本不足等。

> 不要因为 `candidate_ready=true` 就宣称站点已接入。必须完成下一步。

## 2. 审查并保存 YAML

把候选 YAML 保存为：

```text
config/sites/<site_id>.yaml
```

人工检查：

1. `site_id` 唯一、全小写；
2. `allowed_domains` 只包含该站及其必要子域；
3. `article_url_patterns` 不会匹配导航/推荐/视频（如不需要）；
4. `channels[].discovery.url` 是实际可访问的栏目页；
5. `discovery.url_pattern` 是**路径正则**或完整 URL 正则均可；
6. `content_selectors` 指向正文容器，不是整页；
7. 把 `enabled` 改为 `true`。

### 字段说明

```yaml
site_id: example
name: 示例新闻
homepage: https://example.com/
enabled: true
timezone: Asia/Shanghai
allowed_domains: [example.com]
host_aliases:              # 可选：把镜像域规范化到主域
  www.example.com: example.com
article_url_patterns:
  - '^https://example\.com/news/20\d{6}/[0-9a-f]+\.html$'
request_delay_seconds: 1.5
timeout_seconds: 25
max_redirects: 3
respect_robots: true
robots_failure_policy: closed
channels:
  - id: main
    name: 要闻
    url: https://example.com/news/
    discovery:
      - type: html          # html / rss / sitemap
        url: https://example.com/news/
        link_selector: "a[href]"
        url_pattern: '^/news/20\d{6}/[0-9a-f]+\.html$'
        same_channel_path: true
article:
  title_selectors: ["h1 .title", "h1"]
  published_at_selectors: [".info", "time"]
  date_meta_names: [publishdate, article:published_time]
  source_selectors: [".source"]
  content_selectors: ["#detailContent", "article"]
  paragraph_selector: p
  remove_selectors: [script, style, ".ad", ".recommend", ".share"]
  min_content_chars: 80
validation:
  sample_articles: 3
  min_ok_articles: 1
```

采集模式：

- `html`：`a[href]` + `url_pattern`；`same_channel_path` 可过滤跨频道推荐。
- `rss`：`url` 指向 feed；`max_age_days` 可拒绝过期 feed（新华网案例）。
- `sitemap`：`url` 指向 urlset/sitemapindex；可递归一层子 sitemap。
- 动态网页/官方 API：在站点 YAML 写 `custom_adapter: "my_package:MyAdapter"`，实现 `SiteAdapter` 的四个方法；核心 MCP 层不需要改动。

## 3. 自动验证门槛

保存配置后运行：

```powershell
.\.venv\Scripts\python.exe -m news_mcp.cli validate --site <site_id>
```

或 MCP：

```json
{"site_id": "<site_id>"}
```

验证会真实访问：

- 每个频道发现文章链接；
- 每个频道抓取 1–3 篇文章；
- 检查标题、日期、正文长度；
- 只有至少一个频道达到 `min_ok_articles`，该频道才会进入 `list_channels`。

验证结果写入 SQLite，重启后保留；配置 hash 改变会自动使旧验证失效，必须重新验证。**验证失败不会删除其他站点，也不会影响其他站点查询。**

## 4. 采集并验证查询

```powershell
.\.venv\Scripts\python.exe -m news_mcp.cli refresh --site <site_id> --channel main --limit 5
.\.venv\Scripts\python.exe -m news_mcp.cli latest --site <site_id> --channel main --limit 5
.\.venv\Scripts\python.exe -m news_mcp.cli search --keyword 关键词 --site <site_id> --limit 5
```

跨站：

```powershell
.\.venv\Scripts\python.exe -m news_mcp.cli search --keyword 关键词 --site all --limit 10
```

## 5. 实际案例：中国新闻网（第二站点）

2026-11-17 实测：

- 首页 HTTP 200；
- 滚动新闻页 HTTP 200，单页约 121 条当日文章链接；
- 文章 URL：`https://www.chinanews.com.cn/{频道}/2026/11-17/{id}.shtml`；
- 文章标题 `h1`、日期 `.content_left_time`、正文 `.left_zw`；
- 栏目 `/gn/`、`/cj/`、`/gj/` 返回 403，未使用；
- `robots.txt` 允许 `/`，禁止 `/part/`、`/iframe/`、`/ad/` 等。

配置文件：[`config/sites/chinanews.yaml`](../config/sites/chinanews.yaml)

接入结果：

- 新增 YAML 后运行 `validate_site`，1 个频道通过（3/3 样本正文成功）；
- 采集 10 条真实文章；
- 与新华网共用 `list_channels`、`get_latest_news`、`search_news`、`get_article`、`refresh_news`；
- 没有修改 `news_mcp/` 核心代码，也没有修改 `config/sites/xinhua.yaml`；
- 跨站搜索能同时返回并标明 `site_id` / `site_name`。

证据：[`evidence/dataset_build.json`](../evidence/dataset_build.json)、[`evidence/acceptance_audit.json`](../evidence/acceptance_audit.json)。

## 6. 故障隔离检查

新增错误配置不应影响已有站点。验证方法：

1. 写一个字段拼错/选择器缺失的 YAML；
2. 启动服务；
3. `list_sites` 的 `config_errors` 会报告该文件，服务仍返回其他站点；
4. 对应 `site_id` 不出现在有效频道/文章查询中；
5. 修复后重新 `validate_site`。

单元测试 `tests/test_config.py` 覆盖该行为。

## 7. 接入完成清单

- [ ] `robots.txt` 和条款已确认；
- [ ] `site_id` 唯一；
- [ ] 只有白名单域名；
- [ ] 文章 URL 规则经过真实链接验证；
- [ ] `validate_site` 至少一个频道通过；
- [ ] `refresh_news` 返回真实计数；
- [ ] 标题/日期/链接/正文抽样与网页一致；
- [ ] 跨站 `search_news` 来源字段正确；
- [ ] 新站点故障不影响其他站点；
- [ ] 文档/配置已提交。
