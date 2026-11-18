# 客户端接入包

| 目录 | 客户端 | 用途 |
|---|---|---|
| `dsh-news-mcp/` | DeepSeek Harness | 配置型 bundle，工具名 `mcp__news__*` |
| `chatgpt/` | ChatGPT 自定义 App/Connector | 远程 HTTPS MCP + Developer Mode |
| `doubao/` | 豆包开放平台插件 | `plugin.json` + `mcp.json`，Streamable HTTP + Bearer |

ChatGPT 和豆包都需要远程 URL，不能直接运行本地 stdio。先启动：

```powershell
.\.venv\Scripts\python.exe -m news_mcp.http_server --host 127.0.0.1 --port 8765
```

再通过 Cloudflare Tunnel/ngrok 暴露为 HTTPS：

```powershell
cloudflared tunnel --url http://127.0.0.1:8765
```

MCP 地址是 `https://公网域名/mcp`。

完整说明：[docs/REMOTE_MCP.md](../docs/REMOTE_MCP.md)。
