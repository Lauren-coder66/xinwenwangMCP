# ChatGPT 自定义 MCP App / Connector 配置

ChatGPT 不能启动本地 stdio 进程，也不能访问 `127.0.0.1`。因此需要：

1. 把本项目的 MCP Server 以 **Streamable HTTP** 运行；
2. 通过 Cloudflare Tunnel、ngrok 或正式服务器暴露为 **公网 HTTPS**；
3. 在 ChatGPT 的 Developer Mode 中创建 App/Connector，填写 `https://你的域名/mcp`。

## 1. 启动本地 HTTP MCP

```powershell
.\.venv\Scripts\python.exe -m news_mcp.http_server --host 127.0.0.1 --port 8765 --path /mcp
```

检查：

```powershell
curl.exe http://127.0.0.1:8765/healthz
```

## 2. 暴露公网 HTTPS

Cloudflare Quick Tunnel 示例：

```powershell
cloudflared tunnel --url http://127.0.0.1:8765
```

得到：

```text
https://random-words.trycloudflare.com
```

MCP URL：

```text
https://random-words.trycloudflare.com/mcp
```

ngrok 示例：

```powershell
ngrok http 8765
```

## 3. 在 ChatGPT 创建 App

1. 登录 ChatGPT 网页版；
2. Settings → Apps → Advanced settings → 开启 **Developer mode**；
3. Apps → Create；
4. 名称：`News MCP`；
5. MCP Server URL：`https://你的公网域名/mcp`；
6. Authentication：当前项目未实现 OAuth，选择 **No authentication**；
7. 点击 **Scan tools**；
8. 创建后，在对话中启用该 App，然后问：
   “用 News MCP 查询新华网时政最新 3 条新闻，给出时间和原文链接。”

## 4. 鉴权说明

ChatGPT Developer Mode 的自定义 MCP 当前主要使用 **OAuth 或无鉴权**；它不提供任意静态 API Key 头。

本项目的 `NEWS_MCP_HTTP_TOKEN` 主要给豆包、Claude、Cursor、Inspector 等支持 Bearer/自定义 Header 的客户端使用，**不要假设 ChatGPT 会发送这个 Header**。

如果 ChatGPT 使用无鉴权 URL：

- 该 URL 等同公开接口；
- 建议只在测试期使用临时隧道；
- 正式部署应放在带 OAuth、IP 白名单或访问网关的反向代理后面；
- `refresh_news` 会真实请求新闻站点，公开无鉴权时存在被滥用风险。

## 5. 套餐与读写限制

ChatGPT 的自定义 MCP/App 能力取决于套餐和 Developer Mode：

- Business、Enterprise、Edu 对完整 MCP（含写操作）支持更完整；
- Plus/Pro 的自定义 MCP 可能只允许读取/搜索类工具；
- 具体以你账号的 Apps 页面和 OpenAI 当前文档为准。

因此建议让定时任务在本地更新 `data/news.db`，ChatGPT 主要使用：

- `list_sites`
- `list_channels`
- `get_latest_news`（`refresh=false`）
- `search_news`
- `get_article`

这样即使 ChatGPT 只允许只读工具，也能获得最新新闻。

## 6. 本地协议验证

不需要 ChatGPT 账号，也可以先验证 HTTP MCP：

```powershell
npx -y @modelcontextprotocol/inspector --cli `
  --transport http `
  --server-url http://127.0.0.1:8765/mcp `
  --method tools/list
```

应返回 8 个工具。

完整协议与部署说明见项目 [`docs/REMOTE_MCP.md`](../../docs/REMOTE_MCP.md)。
