# 把新闻 MCP 接到 ChatGPT / 豆包

本项目有两层：

1. 本地 **stdio** 服务：`python -m news_mcp.server`，适合 Claude Desktop、Cursor、DeepSeek Harness、Inspector；
2. 远程 **Streamable HTTP** 服务：`python -m news_mcp.http_server`，适合 ChatGPT、豆包等云端客户端。

业务逻辑、工具、数据库完全复用，不需要复制一套 MCP。

## 1. 启动远程 HTTP 服务

```powershell
.\.venv\Scripts\python.exe -m news_mcp.http_server `
  --host 127.0.0.1 `
  --port 8765 `
  --path /mcp
```

默认地址：

```text
http://127.0.0.1:8765/mcp
```

健康检查：

```powershell
curl.exe http://127.0.0.1:8765/healthz
```

返回：

```json
{"ok":true,"service":"news-mcp","version":"1.0.0","transport":"streamable-http"}
```

### 可选鉴权

```powershell
$env:NEWS_MCP_HTTP_TOKEN = "请换成随机长令牌"
.\.venv\Scripts\python.exe -m news_mcp.http_server --host 127.0.0.1 --port 8765
```

请求必须带：

```text
Authorization: Bearer <token>
```

或：

```text
X-API-Key: <token>
```

`/healthz` 不鉴权，方便探活。

### 可选 Host 白名单

```powershell
$env:NEWS_MCP_ALLOWED_HOSTS = "news.example.com,127.0.0.1:8765"
```

用于 MCP SDK 的 DNS rebinding 防护。Cloudflare/ngrok 的临时域名每次变化，测试期可不设置；正式域名建议设置。

## 2. 暴露公网 HTTPS

ChatGPT 和豆包都不能访问 `127.0.0.1`。必须使用公网 HTTPS。

### Cloudflare Tunnel

```powershell
cloudflared tunnel --url http://127.0.0.1:8765
```

得到类似：

```text
https://random-words.trycloudflare.com
```

MCP 地址：

```text
https://random-words.trycloudflare.com/mcp
```

### ngrok

```powershell
ngrok http 8765
```

得到类似：

```text
https://xxxx.ngrok-free.app/mcp
```

> Quick Tunnel 是临时地址，进程关闭后失效；正式使用应部署到云服务器并绑定固定域名。

## 3. ChatGPT 配置

官方参考：[Developer mode and MCP apps in ChatGPT](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt)

步骤：

1. 打开 ChatGPT 网页版；
2. Settings → Apps → Advanced settings；
3. 开启 **Developer mode**；
4. Apps → Create；
5. 名称：`News MCP`；
6. MCP Server URL：`https://你的公网域名/mcp`；
7. Authentication：本项目未实现 OAuth，选择 **No authentication**；
8. 点击 **Scan tools**；
9. 创建后，在对话中启用 App。

测试问题：

> 用 News MCP 查询新华网时政最新 3 条新闻，列出标题、北京时间、原文链接和 data_status。

### ChatGPT 的限制

- 必须有公网 HTTPS；本地 `localhost` 不行；
- 套餐和 Developer Mode 决定是否可用、是否允许写操作；
- ChatGPT 自定义 MCP 主要支持 OAuth 或无鉴权，不能假设它会发送自定义 Bearer Header；
- 无鉴权公开 URL 存在滥用风险，正式部署应加 OAuth/网关/IP 限制；
- 如果套餐只允许只读工具，建议本地定时刷新数据库，ChatGPT 只调用 `list_sites`、`list_channels`、`get_latest_news(refresh=false)`、`search_news`、`get_article`。

## 4. 豆包插件配置

豆包开放平台参考：

- [MCP Server 开发规范](https://open.larkenterprise.com/document/plugin-docs-center/build/mcp-server-development/plugin-development-spec/mcp-server-development)
- [插件开发规范](https://open.feishu.cn/document/doubao-developer-guide/plugin-development-specification)

豆包插件包中放：

```text
integrations/doubao/
├── plugin.json
├── mcp.json
└── README.md
```

`mcp.json`：

```json
{
  "mcpServers": {
    "news": {
      "type": "streamable-http",
      "url": "https://YOUR_PUBLIC_HOST/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_MCP_HTTP_TOKEN"
      }
    }
  }
}
```

`plugin.json` 的 `extensions["com.doubao"].mcpServers.news` 必须和 `mcp.json` 的服务键 `news` 一致。

启动服务时设置同一个 token：

```powershell
$env:NEWS_MCP_HTTP_TOKEN = "YOUR_MCP_HTTP_TOKEN"
.\.venv\Scripts\python.exe -m news_mcp.http_server --host 127.0.0.1 --port 8765
```

豆包要求：

- 远程 MCP 必须使用 HTTPS；
- 不能填 `localhost`/`127.0.0.1`；
- 插件包只保存连接配置，不部署服务端；
- 静态凭据建议按豆包文档放到 `com.doubao/auth/news.json`，不要把真实 token 提交到仓库。

## 5. 本地协议验证

不需要 ChatGPT/豆包账号，先验证 HTTP MCP：

```powershell
npx -y @modelcontextprotocol/inspector --cli `
  --transport http `
  --server-url http://127.0.0.1:8765/mcp `
  --method tools/list
```

预期 8 个工具。

测试工具调用：

```powershell
npx -y @modelcontextprotocol/inspector --cli `
  --transport http `
  --server-url http://127.0.0.1:8765/mcp `
  --method tools/call `
  --tool-name get_latest_news `
  --tool-arg channel=politics `
  --tool-arg limit=2
```

带 token 时：

```powershell
npx -y @modelcontextprotocol/inspector --cli `
  --transport http `
  --server-url http://127.0.0.1:8765/mcp `
  --header "Authorization: Bearer YOUR_TOKEN" `
  --method tools/list
```

## 6. 部署建议

- 用 Windows 任务计划程序定时执行 `refresh`，把数据写入 SQLite；
- HTTP 服务只读查询，不对外暴露 `refresh_news` 的写能力更安全；
- 用 Nginx/Caddy 反向代理并启用 HTTPS；
- 生产环境设置 `NEWS_MCP_HTTP_TOKEN` 或 OAuth 网关；
- 单进程默认有状态会话；多副本部署时加 `--stateless`，并确保 SQLite 只有单写入者（建议把 SQLite 换 PostgreSQL 后再水平扩展）。

## 7. 已验证范围

- 本地 HTTP `/healthz`：200；
- HTTP `tools/list`：8 个工具；
- HTTP `tools/call`：`get_latest_news` 成功；
- Bearer token：未带 token 返回 401，带 token 可访问；
- 证据：`evidence/inspector/http_tools_list.json`、`evidence/inspector/http_call_latest.json`。

未验证：真实 ChatGPT/豆包账号中的 App 扫描和对话调用。这需要你的公网 HTTPS 地址、平台账号和平台审核，无法在本地代替。
