@echo off
setlocal
cd /d "%~dp0"
set "NEWS_MCP_DB=%~dp0data\news.db"
set "NEWS_MCP_CONFIG_DIR=%~dp0config"
set "PYTHONIOENCODING=utf-8"
".venv\Scripts\python.exe" -m news_mcp.server
