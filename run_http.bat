@echo off
setlocal
cd /d "%~dp0"
set "NEWS_MCP_DB=%~dp0data\news.db"
set "NEWS_MCP_CONFIG_DIR=%~dp0config"
set "PYTHONIOENCODING=utf-8"

rem 可选：设置访问令牌。设置后请求必须带 Authorization: Bearer <token>
rem set "NEWS_MCP_HTTP_TOKEN=change-me"

rem 可选：公网域名 Host 白名单，多个用逗号分隔
rem set "NEWS_MCP_ALLOWED_HOSTS=news.example.com"

rem 本地监听；公网通过 cloudflared/ngrok 反向代理
".venv\Scripts\python.exe" -m news_mcp.http_server --host 127.0.0.1 --port 8765 --path /mcp %*
