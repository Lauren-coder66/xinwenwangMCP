# Re-run the MCP Inspector evidence set. Requires Node.js/npx.
$ErrorActionPreference = 'Continue'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$out = Join-Path $root 'evidence\inspector'
New-Item -ItemType Directory -Force $out | Out-Null
$env:NPM_CONFIG_CACHE = Join-Path $root '.tmp\npm-cache'
$env:NEWS_MCP_DB = Join-Path $root 'data\news.db'
$env:NEWS_MCP_CONFIG_DIR = Join-Path $root 'config'
$target = @('--', (Join-Path $root '.venv\Scripts\python.exe'), '-m', 'news_mcp.server')
$common = @(
  '--cwd', $root, '--transport', 'stdio',
  '-e', "NEWS_MCP_DB=$($env:NEWS_MCP_DB)",
  '-e', "NEWS_MCP_CONFIG_DIR=$($env:NEWS_MCP_CONFIG_DIR)",
  '--format', 'json'
)

function Invoke-Inspector([string[]]$extra) {
  & npx -y '@modelcontextprotocol/inspector' --cli @extra @target
  Write-Host "exit=$LASTEXITCODE"
}

Invoke-Inspector (@('--strict', '--method', 'tools/list', '--output', "$out\tools_list.json") + $common)
Invoke-Inspector (@('--method', 'tools/call', '--tool-name', 'list_sites', '--output', "$out\list_sites.json") + $common)
Invoke-Inspector (@('--method', 'tools/call', '--tool-name', 'list_channels', '--tool-arg', 'site_id=all', '--output', "$out\list_channels.json") + $common)
Invoke-Inspector (@('--method', 'tools/call', '--tool-name', 'get_latest_news', '--tool-arg', 'channel=politics', '--tool-arg', 'limit=3', '--output', "$out\latest_xinhua.json") + $common)
Invoke-Inspector (@('--method', 'tools/call', '--tool-name', 'get_latest_news', '--tool-arg', 'channel=latest', '--tool-arg', 'site_id=chinanews', '--tool-arg', 'limit=3', '--output', "$out\latest_chinanews.json") + $common)
Invoke-Inspector (@('--method', 'tools/call', '--tool-name', 'search_news', '--tool-arg', 'keyword=中国', '--tool-arg', 'site_id=all', '--tool-arg', 'limit=5', '--output', "$out\search_all.json") + $common)
Invoke-Inspector (@('--method', 'tools/call', '--tool-name', 'get_article', '--tool-arg', 'url=https://www.news.cn/politics/20261117/3f1e8a88dbcb46df9ea455ffb5e144d9/c.html', '--tool-arg', 'site_id=xinhua', '--output', "$out\get_article.json") + $common)
Invoke-Inspector (@('--method', 'tools/call', '--tool-name', 'get_article', '--tool-arg', 'url=https://example.com/not-news', '--output', "$out\invalid_url.json") + $common)
Invoke-Inspector (@('--method', 'tools/call', '--tool-name', 'refresh_news', '--tool-arg', 'channel=politics', '--tool-arg', 'limit=2', '--output', "$out\refresh_xinhua.json") + $common)
Invoke-Inspector (@('--method', 'tools/call', '--tool-name', 'validate_site', '--tool-arg', 'site_id=chinanews', '--output', "$out\validate_chinanews.json") + $common)
Invoke-Inspector (@('--method', 'tools/call', '--tool-name', 'inspect_site', '--tool-arg', 'url=https://www.chinanews.com.cn/scroll-news/news1.html', '--output', "$out\inspect_site.json") + $common)

& npx -y '@modelcontextprotocol/inspector' --cli @common --method tools/call --tool-name get_latest_news --tool-arg channel=politics --tool-arg limit=0 @target 2>&1 |
  Tee-Object (Join-Path $out 'invalid_limit.log') | Out-Host
Write-Host "invalid_limit_exit=$LASTEXITCODE"
