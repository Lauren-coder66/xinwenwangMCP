# 豆包插件 MCP 配置

这个目录只包含豆包插件所需的连接配置和展示信息，不包含抓取逻辑。抓取服务仍然运行在项目根目录的 Python 进程中。

## 1. 启动公网可访问的 MCP 服务

```powershell
# 终端 1
.\.venv\Scripts\python.exe -m news_mcp.http_server --host 127.0.0.1 --port 8765 --token "请换成随机长令牌"
```

```powershell
# 终端 2：Cloudflare Quick Tunnel（示例）
cloudflared tunnel --url http://127.0.0.1:8765
```

得到类似：

```text
https://random-words.trycloudflare.com
```

MCP 地址是：

```text
https://random-words.trycloudflare.com/mcp
```

## 2. 修改 `mcp.json`

把：

```json
"url": "https://YOUR_PUBLIC_HOST/mcp"
```

改为真实 HTTPS 地址，并把 `YOUR_MCP_HTTP_TOKEN` 改成启动服务时的令牌。

> 生产环境不要把令牌提交到公开仓库。豆包支持静态凭据配置时，应按平台文档把凭据放到 `com.doubao/auth/news.json`，而不是写在 `mcp.json` 里。

## 3. 插件目录

```text
integrations/doubao/
├── plugin.json
├── mcp.json
└── README.md
```

`plugin.json` 的 `extensions["com.doubao"].mcpServers` 中的 `news` 必须与 `mcp.json` 的服务键一致。

## 4. 测试连接

先用浏览器或 curl 检查：

```powershell
curl.exe https://YOUR_PUBLIC_HOST/healthz
```

应返回：

```json
{"ok":true,"service":"news-mcp","version":"1.0.0","transport":"streamable-http"}
```

然后在豆包开放平台创建/上传插件，MCP 服务类型选择 `streamable-http`，URL 填 `https://YOUR_PUBLIC_HOST/mcp`，鉴权填 Bearer Token。

## 5. 限制

- 豆包要求远程 MCP 使用 HTTPS，不能填 `127.0.0.1` 或 `localhost`。
- 插件发布只上传配置，不会部署服务端；你的 Python 服务必须持续运行。
- ChatGPT/豆包的可用工具和读写权限取决于账号套餐、开发者模式和平台审核。
